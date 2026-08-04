"""특정 시설의 2025년 개별 공연 목록을 pblprfr(공연목록) 엔드포인트로 조회해 엑셀로 저장.
prfstsPrfByFct(통계)와 달리 이 엔드포인트는 관(공연장) 단위 구분이 없고 시설명 단위로만 나온다.
"""
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from config import BASE_URL, KOPIS_SERVICE_KEY

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def fetch_all(facility_name: str, stdate: str, eddate: str) -> list[dict]:
    all_items = []
    for page in range(1, 50):
        params = {
            "service": KOPIS_SERVICE_KEY,
            "stdate": stdate,
            "eddate": eddate,
            "cpage": page,
            "rows": 50,
            "shprfnmfct": facility_name,
        }
        import requests
        resp = requests.get(BASE_URL + "pblprfr", params=params, timeout=20)
        resp.encoding = "utf-8"
        if not resp.text.strip():
            break
        items = ET.fromstring(resp.text).findall("db")
        if not items:
            break
        for it in items:
            rec = {c.tag: (c.text or "").strip() for c in it}
            if rec.get("fcltynm") == facility_name:
                all_items.append(rec)
    return all_items


def main():
    parser = argparse.ArgumentParser(description="시설별 공연 목록 조회")
    parser.add_argument("--facility", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    items = fetch_all(args.facility, f"{args.year}0101", f"{args.year}1231")
    items.sort(key=lambda r: r.get("prfpdfrom", ""))

    rows = [
        {
            "공연기간_시작": r.get("prfpdfrom", ""),
            "공연기간_종료": r.get("prfpdto", ""),
            "공연명": r.get("prfnm", ""),
            "장르": r.get("genrenm", ""),
            "지역": r.get("area", ""),
            "진행상태": r.get("prfstate", ""),
        }
        for r in items
    ]
    df = pd.DataFrame(rows)

    output_path = Path(args.output) if args.output else OUTPUT_DIR / f"{args.facility}_{args.year}_공연목록.xlsx"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, sheet_name="공연목록", index=False)
    print(f"총 {len(rows)}건 -> {output_path}")


if __name__ == "__main__":
    main()
