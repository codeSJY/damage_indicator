import os
import json
import time
import csv
import httpx
from pathlib import Path
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
MODEL = "gemini-2.0-flash"

PROMPT = (
    "이 검품 이미지에 흰색 원형 다이얼 마커가 있나요? "
    "마커는 흰색 원판 중앙에 빨간 링이 있고 주변에 한국어 텍스트가 방사형으로 적힌 원형 도구입니다. "
    "반드시 YES 또는 NO 한 단어로만 답하세요."
)

def analyze_image(row):
    url = "https://returneeds-prod.s3.amazonaws.com/" + row["thumbnail"]
    try:
        img_bytes = httpx.get(url, timeout=15).content
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                PROMPT,
            ],
        )
        answer = response.text.strip().upper()
        has_marker = answer.startswith("YES")
        return {**row, "has_marker": has_marker, "answer": answer, "error": ""}
    except Exception as e:
        return {**row, "has_marker": False, "answer": "", "error": str(e)}

def main():
    with open("image_list.json") as f:
        rows = json.load(f)

    results_path = Path("results.csv")
    done_keys = set()

    if results_path.exists():
        with open(results_path) as f:
            for r in csv.DictReader(f):
                done_keys.add((r["bulk_number"], r["item_code"]))

    remaining = [r for r in rows if (r["bulk_number"], r["item_code"]) not in done_keys]
    print(f"전체: {len(rows)}건 / 남은 작업: {len(remaining)}건")

    fieldnames = ["bulk_number", "item_code", "brand", "period", "thumbnail", "has_marker", "answer", "error"]
    write_header = not results_path.exists()

    with open(results_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for i, row in enumerate(remaining):
            result = analyze_image(row)
            writer.writerow(result)
            f.flush()

            marker_str = "✓" if result["has_marker"] else ("-" if not result["error"] else "!")
            print(f"[{i+1}/{len(remaining)}] {row['brand']} {row['period']} {row['item_code']} → {marker_str}")

            # 무료 티어 rate limit (15 RPM) 대응
            if (i + 1) % 14 == 0:
                time.sleep(62)

    print("\n=== 마커 사용률 요약 ===")
    from collections import defaultdict
    summary = defaultdict(lambda: {"total": 0, "marker": 0})
    errors = 0
    with open(results_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["error"]:
                errors += 1
                continue
            key = (r["brand"], r["period"])
            summary[key]["total"] += 1
            if r["has_marker"] == "True":
                summary[key]["marker"] += 1

    for (brand, period), v in sorted(summary.items()):
        rate = v["marker"] / v["total"] * 100 if v["total"] else 0
        print(f"{brand} {period}: {v['marker']}/{v['total']}건 ({rate:.1f}%)")
    if errors:
        print(f"오류: {errors}건")

if __name__ == "__main__":
    main()
