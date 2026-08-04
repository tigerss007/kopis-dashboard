"""전국 공연시설 인터랙티브 웹페이지용 데이터 빌드.

2025년 전국 (시설,관) 가동률(prfstsPrfByFct, 기존 collect.py/calc.py 재사용) +
시설 마스터(prfplc, 지역 조인용) + 공연목록(pblprfr)을 모아
지역->시설->관/공연목록 구조의 JSON을 만들고, template.html에 인라인 삽입해
서버 없이 더블클릭으로 여는 단일 index.html을 생성한다.
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calc import aggregate  # noqa: E402
from cache import ResponseCache  # noqa: E402
from collect import collect_year_region  # noqa: E402
from config import CACHE_DIR, REGION_CODE  # noqa: E402
from facility_match import find_matches, normalize_facility_name  # noqa: E402
from kopis_client import KopisApiError, KopisClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_site_data")

YEAR = 2025
WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = WEB_DIR / "template.html"
OUTPUT_PATH = WEB_DIR / "index.html"


def _cached_fetch_list(client: KopisClient, cache: ResponseCache, endpoint: str, params: dict) -> list[dict]:
    cached = cache.get(endpoint, params)
    if cached is not None:
        return cached
    try:
        records = client.fetch_list(endpoint, params)
    except KopisApiError as exc:
        logger.error("%s 호출 실패 params=%s: %s", endpoint, params, exc)
        records = []
    cache.set(endpoint, params, records)
    return records


def fetch_facility_master(client: KopisClient, cache: ResponseCache) -> dict:
    """정규화된 시설명 -> {name, sido, gugun, hall_count} 매핑 (17개 시도 signgucode로 조회)."""
    master: dict[str, dict] = {}
    for region_key, code in REGION_CODE.items():
        params = {"signgucode": code}
        cached_before = cache.get("prfplc", params) is not None
        records = _cached_fetch_list(client, cache, "prfplc", params)
        logger.info("prfplc region=%s(%s): %d건 (캐시=%s)", region_key, code, len(records), cached_before)
        for r in records:
            name = r.get("fcltynm", "").strip()
            norm = normalize_facility_name(name)
            if not norm:
                continue
            master[norm] = {
                "name": name,
                "sido": r.get("sidonm", ""),
                "gugun": r.get("gugunnm", ""),
                "hall_count": r.get("mt13cnt", ""),
            }
    return master


def fetch_performances(client: KopisClient, cache: ResponseCache) -> dict:
    """원본 시설명(fcltynm) -> 공연 목록. 17개 시도 signgucode + 연간 전체기간으로 조회."""
    by_facility: dict[str, list[dict]] = {}
    for region_key, code in REGION_CODE.items():
        params = {"signgucode": code, "stdate": f"{YEAR}0101", "eddate": f"{YEAR}1231"}
        cached_before = cache.get("pblprfr", params) is not None
        records = _cached_fetch_list(client, cache, "pblprfr", params)
        logger.info("pblprfr region=%s(%s): %d건 (캐시=%s)", region_key, code, len(records), cached_before)
        for r in records:
            fclty = r.get("fcltynm", "").strip()
            if not fclty:
                continue
            by_facility.setdefault(fclty, []).append({
                "name": r.get("prfnm", ""),
                "from": r.get("prfpdfrom", ""),
                "to": r.get("prfpdto", ""),
                "genre": r.get("genrenm", ""),
                "state": r.get("prfstate", ""),
            })
    return by_facility


def _lookup_master(fclty_name: str, norm: str, master: dict) -> dict | None:
    info = master.get(norm)
    if info is not None:
        return info
    matches = find_matches(fclty_name, [v["name"] for v in master.values()])
    if not matches:
        return None
    target = matches[0]
    for v in master.values():
        if v["name"] == target:
            return v
    return None


def _lookup_performances(fclty_name: str, norm: str, perf_by_norm: dict, perf_raw_keys: list[str], performances: dict) -> list[dict]:
    plist = perf_by_norm.get(norm)
    if plist is not None:
        return plist
    matches = find_matches(fclty_name, perf_raw_keys)
    combined: list[dict] = []
    for m in matches:
        combined.extend(performances.get(m, []))
    return combined


def build_facilities(rows: list[dict], master: dict, performances: dict) -> tuple[list[dict], list[str]]:
    perf_by_norm: dict[str, list[dict]] = {}
    for raw_name, plist in performances.items():
        perf_by_norm.setdefault(normalize_facility_name(raw_name), []).extend(plist)
    perf_raw_keys = list(performances.keys())

    facilities: dict[str, dict] = {}
    unmatched_region: list[str] = []

    for row in rows:
        fclty_name = row["공연시설명"]
        norm = normalize_facility_name(fclty_name)
        if norm not in facilities:
            info = _lookup_master(fclty_name, norm, master)
            if info is None:
                unmatched_region.append(fclty_name)
            facilities[norm] = {
                "name": fclty_name,
                "sido": info["sido"] if info else "지역 미상",
                "gugun": info["gugun"] if info else "",
                "halls": [],
                "perf": _lookup_performances(fclty_name, norm, perf_by_norm, perf_raw_keys, performances),
            }
        facilities[norm]["halls"].append({
            "name": row["공연장(관)"],
            "seat": row["좌석수"],
            "tickets": row["총 티켓판매수"],
            "occ": row["좌석점유율(%)"],
            "occNote": row["좌석점유율_비고"],
        })

    return list(facilities.values()), unmatched_region


def main():
    client = KopisClient()
    cache = ResponseCache(CACHE_DIR)

    logger.info("=== 1/3: %d년 전국 통계 수집 ===", YEAR)
    raw_records = collect_year_region(client, cache, YEAR, region=None)
    rows = aggregate({YEAR: raw_records})
    rows = [r for r in rows if r["좌석수"] > 0]  # 좌석수 미상(비공연 공간 등)은 웹페이지에서 제외
    logger.info("%d년 (시설,관) 행 수: %d", YEAR, len(rows))

    logger.info("=== 2/3: 시설 마스터(지역 정보) 수집 ===")
    master = fetch_facility_master(client, cache)
    logger.info("시설 마스터 총 %d건", len(master))

    logger.info("=== 3/3: 공연목록 수집 ===")
    performances = fetch_performances(client, cache)
    total_perf = sum(len(v) for v in performances.values())
    logger.info("공연목록 총 %d건, 시설 수 %d", total_perf, len(performances))

    facilities, unmatched_region = build_facilities(rows, master, performances)

    if unmatched_region:
        logger.warning(
            "지역 매칭 실패 %d개 시설 (지역 미상으로 표시): %s",
            len(unmatched_region), sorted(set(unmatched_region)),
        )

    data = {"year": YEAR, "facilities": facilities}
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__KOPIS_DATA__" not in template:
        raise RuntimeError("template.html에 __KOPIS_DATA__ 플레이스홀더가 없습니다.")
    output = template.replace("__KOPIS_DATA__", data_json)
    OUTPUT_PATH.write_text(output, encoding="utf-8")

    size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    logger.info("=== 완료 ===")
    logger.info("시설 수: %d, 관 수: %d, 공연 수(연결됨): %d", len(facilities), len(rows), sum(len(f["perf"]) for f in facilities))
    logger.info("지역 미상 시설 수: %d", len(set(unmatched_region)))
    logger.info("출력 파일: %s (%.2f MB)", OUTPUT_PATH, size_mb)


if __name__ == "__main__":
    main()
