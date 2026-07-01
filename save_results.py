import httpx
import io
import numpy as np
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud import bigquery
from collections import defaultdict

S3_BASE = "https://returneeds-prod.s3.amazonaws.com/"
BQ_PROJECT = "returneeds-general-489208"
RESULT_TABLE = "returneeds-general-489208.tmp_jyshin.marker_detection"

def red_ratio(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        arr = np.array(img, dtype=np.int16)
        r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
        red_mask = (r > 150) & (g < 80) & (b < 80)
        return float(red_mask.sum()) / float(red_mask.size)
    except Exception:
        return -1.0

def check_image(row):
    url = S3_BASE + row["thumbnail"]
    try:
        resp = httpx.get(url, timeout=10)
        ratio = red_ratio(resp.content)
        return {
            "bulk_number": str(row["bulk_number"]),
            "item_code": str(row["item_code"]),
            "brand": str(row["brand"]),
            "period": str(row["period"]),
            "thumbnail": str(row["thumbnail"]),
            "red_ratio": float(round(ratio, 4)),
            "has_marker": bool(ratio > 0.015),
        }
    except Exception:
        return {
            "bulk_number": str(row["bulk_number"]),
            "item_code": str(row["item_code"]),
            "brand": str(row["brand"]),
            "period": str(row["period"]),
            "thumbnail": str(row["thumbnail"]),
            "red_ratio": -1.0,
            "has_marker": False,
        }

def main():
    client = bigquery.Client(project=BQ_PROJECT)

    print("BigQuery에서 이미지 목록 조회 중...")
    query = """
    SELECT
      i.bulk_number,
      i.item_code,
      i.thumbnail,
      CASE WHEN o.ecommerce_id = 'MMB' THEN '모어서울' ELSE '웨얼하우스' END AS brand,
      CASE
        WHEN DATE(o.inspected_at) BETWEEN '2026-06-10' AND '2026-06-18' THEN '6/10~18'
        WHEN DATE(o.inspected_at) BETWEEN '2026-06-19' AND '2026-06-27' THEN '6/19~27'
      END AS period
    FROM `returneeds-dataware.public.inspect_image` i
    JOIN `returneeds-dataware.public.order` o USING (bulk_number)
    WHERE o.ecommerce_id IN ('MMB', 'YJL', 'ESP', 'FID')
      AND i.item_code IS NOT NULL
      AND i.thumbnail IS NOT NULL
      AND DATE(o.inspected_at) BETWEEN '2026-06-10' AND '2026-06-27'
    """
    rows = list(client.query(query).result())
    print(f"총 {len(rows)}장 분석 시작...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_image, dict(r)): r for r in rows}
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(rows)} 완료...")

    print(f"\n분석 완료. BigQuery에 저장 중...")
    # 1000건씩 나눠서 insert
    chunk_size = 1000
    for i in range(0, len(results), chunk_size):
        chunk = results[i:i+chunk_size]
        errors = client.insert_rows_json(RESULT_TABLE, chunk)
        if errors:
            print(f"  저장 오류 ({i}~{i+chunk_size}): {errors[:2]}")
    print("저장 완료.")

    print("\n=== 마커 사용률 요약 ===")
    summary = defaultdict(lambda: {"total": 0, "marker": 0})
    for r in results:
        if r["red_ratio"] < 0:
            continue
        key = (r["brand"], r["period"])
        summary[key]["total"] += 1
        if r["has_marker"]:
            summary[key]["marker"] += 1

    for (brand, period), v in sorted(summary.items()):
        rate = v["marker"] / v["total"] * 100 if v["total"] else 0
        print(f"{brand} {period}: {v['marker']}/{v['total']}장 ({rate:.1f}%)")

if __name__ == "__main__":
    main()
