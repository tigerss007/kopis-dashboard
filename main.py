"""KOPIS 연간 공연시설별 가동률 산출 도구 — 전체 파이프라인 실행.

사용 예:
    python main.py --years 2023 2024 2025 --region seoul --facilities "샤롯데씨어터" "충무아트센터"
    python main.py --years 2024 --facilities "예술의전당"   # 지역 생략 시 전국에서 이름으로 검색
"""
import argparse
import logging
import sys
from pathlib import Path

from calc import aggregate
from cache import ResponseCache
from collect import collect
from config import (
    CACHE_DIR,
    DEFAULT_FACILITIES,
    DEFAULT_REGION,
    DEFAULT_YEARS,
    OUTPUT_DIR,
    REGION_CODE,
)
from export import export_excel
from kopis_client import KopisClient


def parse_args():
    parser = argparse.ArgumentParser(description="KOPIS 연간 공연시설별 가동률 산출")
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS)
    parser.add_argument("--region", choices=sorted(REGION_CODE), default=DEFAULT_REGION)
    parser.add_argument("--facilities", nargs="*", default=DEFAULT_FACILITIES)
    parser.add_argument("--output", default=str(OUTPUT_DIR / "kopis_가동률_결과.xlsx"))
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("main")

    args = parse_args()
    facilities = args.facilities or None

    logger.info(
        "수집 시작: years=%s region=%s facilities=%s",
        args.years, args.region or "전국", facilities or "(전체)",
    )

    client = KopisClient()
    cache = ResponseCache(CACHE_DIR)

    records_by_year = collect(client, cache, args.years, args.region, facilities)

    total_records = sum(len(v) for v in records_by_year.values())
    if total_records == 0:
        logger.error("수집된 데이터가 없습니다. 시설명/지역/연도를 확인해주세요.")
        sys.exit(1)

    rows = aggregate(records_by_year)
    export_excel(rows, Path(args.output))

    # 실행 결과 요약
    seat_unknown = [r for r in rows if r["좌석수"] <= 0]
    logger.info("=== 실행 결과 요약 ===")
    logger.info("총 (연도x시설x관) 행 수: %d", len(rows))
    if seat_unknown:
        names = sorted({f"{r['공연시설명']}/{r['공연장(관)']}" for r in seat_unknown})
        logger.info("좌석수 미상으로 '데이터_미확보' 시트에 분리된 관: %s", names)
    logger.info("결과 파일: %s", args.output)


if __name__ == "__main__":
    main()
