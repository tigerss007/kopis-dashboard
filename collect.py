"""1단계: KOPIS 공연시설별 통계(prfstsPrfByFct) 수집.

이 엔드포인트는 실제 호출로 확인한 결과 조회기간이 최대 31일로 제한되어 있어
연 단위 데이터는 월 단위로 쪼개서 호출한 뒤 합산한다. 지역(sharea)을 지정하지
않으면 전국 데이터를 받아 대상 시설명으로 필터링한다(전국 조회는 페이지 수가
많아 다소 느릴 수 있음).
"""
import calendar
import datetime
import logging

from cache import ResponseCache
from config import REGION_CODE
from facility_match import find_matches
from kopis_client import KopisApiError, KopisClient

logger = logging.getLogger(__name__)

ENDPOINT = "prfstsPrfByFct"


def _month_chunks(year: int):
    today = datetime.date.today()
    for month in range(1, 13):
        first = datetime.date(year, month, 1)
        if first > today:
            break
        last_day = calendar.monthrange(year, month)[1]
        last = datetime.date(year, month, last_day)
        if last > today:
            last = today
        yield first.strftime("%Y%m%d"), last.strftime("%Y%m%d")


def collect_year_region(
    client: KopisClient, cache: ResponseCache, year: int, region: str | None
) -> list[dict]:
    """해당 연도·지역의 시설별 통계 원본 레코드를 월 단위로 수집(캐시 활용)."""
    sharea = REGION_CODE[region] if region else None
    records: list[dict] = []
    for stdate, eddate in _month_chunks(year):
        params = {"stdate": stdate, "eddate": eddate, "sharea": sharea}
        cached = cache.get(ENDPOINT, params)
        if cached is not None:
            month_records = cached
        else:
            try:
                month_records = client.fetch_list(ENDPOINT, params)
            except KopisApiError as exc:
                logger.error("수집 실패 %s~%s (region=%s): %s", stdate, eddate, region, exc)
                month_records = []
            cache.set(ENDPOINT, params, month_records)

        for r in month_records:
            r["_year"] = year
        records.extend(month_records)
        logger.info(
            "%s %s~%s (region=%s): %d건 (캐시=%s)",
            ENDPOINT, stdate, eddate, region or "전국", len(month_records), cached is not None,
        )
    return records


def collect(
    client: KopisClient,
    cache: ResponseCache,
    years: list[int],
    region: str | None,
    target_facilities: list[str] | None,
) -> dict[int, list[dict]]:
    """연도별 원본 통계 레코드. target_facilities가 주어지면 느슨한 매칭으로 필터링."""
    result: dict[int, list[dict]] = {}
    for year in years:
        raw = collect_year_region(client, cache, year, region)

        if not target_facilities:
            result[year] = raw
            continue

        candidate_names = sorted({r.get("prfnmfct", "") for r in raw})
        filtered = []
        unmatched = []
        for target in target_facilities:
            matches = find_matches(target, candidate_names)
            if not matches:
                unmatched.append(target)
                continue
            if len(matches) > 1:
                logger.warning(
                    "'%s' 이(가) 여러 시설명과 매칭됩니다: %s (모두 포함해서 집계)", target, matches
                )
            filtered.extend(r for r in raw if r.get("prfnmfct") in matches)

        if unmatched:
            logger.warning("%s년: 다음 대상 시설을 찾지 못했습니다 -> %s", year, unmatched)

        result[year] = filtered
    return result
