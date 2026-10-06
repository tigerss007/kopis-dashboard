"""전국 공연시설 인터랙티브 웹페이지용 데이터 빌드.

2025년 전국 (시설,관) 가동률(prfstsPrfByFct, 기존 collect.py/calc.py 재사용) +
시설 마스터(prfplc, 지역 조인용) + 공연목록(pblprfr)을 모아
지역->시설->관/공연목록 구조의 JSON을 만들고, template.html에 인라인 삽입해
서버 없이 더블클릭으로 여는 단일 index.html을 생성한다.

시설 주소·좌표·관ID는 prfplc 상세조회에서, 공연별 관 배정은 pblprfr 상세조회의
mt13id에서 가져온다. 지역지도의 시설 점 위치(mapX/mapY)는 korea_paths.json에
저장된 투영 상수(_build_map.py 참고)로 실제 위경도를 지도 SVG 좌표계에 투영한 값이다.
"""
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calc import aggregate  # noqa: E402
from cache import ResponseCache  # noqa: E402
from collect import collect_year_region  # noqa: E402
from config import CACHE_DIR, REGION_CODE  # noqa: E402
from facility_match import build_hall_index, find_matches, match_hall_id, normalize_facility_name  # noqa: E402
from kopis_client import KopisApiError, KopisClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_site_data")

YEAR = 2025
WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = WEB_DIR / "template.html"
OUTPUT_PATH = WEB_DIR / "index.html"
KOREA_PATHS_PATH = WEB_DIR / "korea_paths.json"

DETAIL_SLEEP_SEC = 0.2
PERF_PROGRESS_EVERY = 500

YEAR_START = date(YEAR, 1, 1)
YEAR_END = date(YEAR, 12, 31)
YEAR_DAYS = (YEAR_END - YEAR_START).days + 1

# 모든 공연에 일괄 적용하는 준비(공연 앞)·철수(공연 뒤) 기간. KOPIS에는 실제 값이 없다.
SETUP_DAYS = 3
TEARDOWN_DAYS = 1


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


def _cached_fetch_detail(client: KopisClient, cache: ResponseCache, endpoint: str, item_id: str) -> tuple[dict | None, bool]:
    """(detail, from_cache) 반환. 캐시 히트일 때는 호출부에서 sleep을 건너뛴다."""
    cache_endpoint = f"{endpoint}_detail"
    params = {"id": item_id}
    cached = cache.get(cache_endpoint, params)
    if cached is not None:
        return cached, True
    try:
        record = client.fetch_detail(endpoint, item_id)
    except KopisApiError as exc:
        logger.warning("%s/%s 상세조회 실패: %s", endpoint, item_id, exc)
        record = None
    cache.set(cache_endpoint, params, record)
    return record, False


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
                "id": r.get("mt10id", ""),
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
                "mt20id": r.get("mt20id", ""),
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
                "id": info["id"] if info else "",
                "halls": [],
                "perf": _lookup_performances(fclty_name, norm, perf_by_norm, perf_raw_keys, performances),
            }
        facilities[norm]["halls"].append({
            "idx": len(facilities[norm]["halls"]),
            "name": row["공연장(관)"],
            "seat": row["좌석수"],
            "tickets": row["총 티켓판매수"],
            "occ": row["좌석점유율(%)"],
            "occNote": row["좌석점유율_비고"],
        })

    return list(facilities.values()), unmatched_region


def enrich_facility_details(client: KopisClient, cache: ResponseCache, facilities: list[dict]) -> None:
    """시설별 상세조회(prfplc/{mt10id})로 주소·좌표와 각 관의 mt13id를 채운다(in-place).

    build_radar_feed.py가 이미 대부분의 시설을 상세조회해 캐시에 넣어뒀을 가능성이
    높아(같은 cache_endpoint/params 규칙 사용) 실제로는 대부분 캐시 히트로 즉시 끝난다.
    """
    total = len(facilities)
    for i, f in enumerate(facilities, start=1):
        facility_id = f.pop("id", "")
        f["addr"] = None
        f["lat"] = None
        f["lng"] = None
        if not facility_id:
            continue

        detail, from_cache = _cached_fetch_detail(client, cache, "prfplc", facility_id)
        if not from_cache:
            time.sleep(DETAIL_SLEEP_SEC)
        if not detail:
            continue

        raw_addr = (detail.get("adres") or "").strip()
        f["addr"] = raw_addr or None
        try:
            f["lat"] = float(detail["la"]) if detail.get("la") else None
            f["lng"] = float(detail["lo"]) if detail.get("lo") else None
        except (TypeError, ValueError):
            f["lat"], f["lng"] = None, None

        hall_index = build_hall_index(detail.get("mt13s", []))
        if hall_index:
            for hall in f["halls"]:
                hall["mt13id"] = match_hall_id(hall["name"], hall_index)

        if i % 200 == 0 or i == total:
            logger.info("시설 상세(주소·관ID) 진행: %d/%d", i, total)


def apply_map_projection(facilities: list[dict]) -> None:
    """시설의 실제 lat/lng를 지역지도 SVG 좌표계(mapX/mapY)로 투영한다(in-place).

    korea_paths.json의 "projection"은 _build_map.py가 지도 경계 SVG를 만들 때 쓴
    투영식(경위도 -> x/y)의 상수다(같은 소스 GeoJSON으로 재생성해 기존 경로와
    한 글자도 다르지 않음을 확인함, 2026-08-07). 같은 상수를 시설 좌표에 적용하면
    지도 경계와 정확히 같은 좌표계 위에 점을 찍을 수 있다. projection 정보가 없으면
    (korea_paths.json이 오래된 버전이면) 조용히 건너뛴다 — 프런트엔드가 mapX/mapY
    없는 시설은 기존 임의 위치 배치로 대체한다.
    """
    if not KOREA_PATHS_PATH.exists():
        logger.warning("%s 없음 — 지도 실좌표 투영을 건너뜁니다.", KOREA_PATHS_PATH)
        return
    korea_map = json.loads(KOREA_PATHS_PATH.read_text(encoding="utf-8"))
    proj = korea_map.get("projection")
    if not proj:
        logger.warning("korea_paths.json에 projection 정보가 없어 지도 실좌표 투영을 건너뜁니다.")
        return

    cos_lat, proj_x_min, scale, lat_max = proj["cosLat"], proj["projXMin"], proj["scale"], proj["latMax"]
    projected = 0
    for f in facilities:
        if f.get("lat") is None or f.get("lng") is None:
            continue
        f["mapX"] = round((f["lng"] * cos_lat - proj_x_min) * scale, 1)
        f["mapY"] = round((lat_max - f["lat"]) * scale, 1)
        projected += 1
    logger.info("지도 실좌표 투영: %d/%d개 시설", projected, len(facilities))


def fetch_performance_halls(client: KopisClient, cache: ResponseCache, facilities: list[dict]) -> None:
    """공연 각각을 상세조회(pblprfr/{mt20id})해 어느 관(mt13id) 소속인지 채운다(in-place).

    pblprfr 목록 API는 관 구분을 주지 않지만 상세 API는 mt13id를 준다(2026-08-06 실제
    호출로 확인). 공연 건수가 많아(연간 2만건대) 처음 실행 시 오래 걸리지만 결과가
    캐시되므로 이후 재빌드에서는 신규 공연만 새로 조회한다.
    """
    all_perf = [p for f in facilities for p in f["perf"]]
    total = len(all_perf)
    logger.info("공연 상세(관ID) 조회 대상: %d건", total)
    for i, p in enumerate(all_perf, start=1):
        mt20id = p.get("mt20id")
        p["mt13id"] = None
        if not mt20id:
            continue
        detail, from_cache = _cached_fetch_detail(client, cache, "pblprfr", mt20id)
        if not from_cache:
            time.sleep(DETAIL_SLEEP_SEC)
        if detail:
            p["mt13id"] = detail.get("mt13id") or None
        if i % PERF_PROGRESS_EVERY == 0 or i == total:
            logger.info("공연 상세(관ID) 진행: %d/%d", i, total)


def assign_perf_to_halls(facilities: list[dict]) -> None:
    """시설 단위로 모아뒀던 공연목록을 mt13id 기준으로 각 관에 나눠 담는다(in-place).

    관 매칭이 안 된(mt13id가 없거나 이 시설의 어느 관과도 일치하지 않는) 공연은
    시설의 perf에 "관 정보 없음" 목록으로 남긴다.
    """
    for f in facilities:
        by_mt13id: dict[str, list[dict]] = {}
        unmatched: list[dict] = []
        for p in f["perf"]:
            mt13id = p.pop("mt13id", None)
            p.pop("mt20id", None)
            if mt13id:
                by_mt13id.setdefault(mt13id, []).append(p)
            else:
                unmatched.append(p)

        for hall in f["halls"]:
            hall_mt13id = hall.pop("mt13id", None)
            hall["perf"] = by_mt13id.pop(hall_mt13id, []) if hall_mt13id else []

        # 남은 값(이 시설의 어느 관과도 매칭되지 않은 mt13id 포함)은 관 정보 없음으로 묶는다.
        leftover = unmatched + [p for plist in by_mt13id.values() for p in plist]
        f["perf"] = leftover


def _parse_kopis_date(s: str | None) -> date | None:
    try:
        y, m, d = s.split(".")
        return date(int(y), int(m), int(d))
    except (AttributeError, ValueError, TypeError):
        return None


def _year_day_set(start: date, end: date) -> set[int]:
    """[start, end]에서 2025년에 속하는 날짜들을 toordinal() 값의 집합으로 돌려준다."""
    start, end = max(start, YEAR_START), min(end, YEAR_END)
    return set(range(start.toordinal(), end.toordinal() + 1))


def compute_calendar_occupancy(facilities: list[dict]) -> None:
    """관별 공연 일수와 캘린더가동률을 계산한다(in-place).

    h["runDays"]      : 공연 상연기간(from~to)의 합집합 일수(2025년분만, 겹침 제외)
    h["occupiedDays"] : 각 공연 앞뒤로 준비 SETUP_DAYS일·철수 TEARDOWN_DAYS일을 더한 기간의
                        합집합 일수(2025년분만). 캘린더가동률의 분자.
    h["calendarOcc"]  : occupiedDays ÷ 365 × 100

    KOPIS는 관별 실제 공연 "일자" 목록을 API로 주지 않는다(2026-08-04 확인, calc.py
    참고). 대신 공연 하나하나의 상연기간(prfpdfrom~prfpdto)은 있으므로, "그 기간 동안
    해당 프로덕션이 관을 사용했다"고 보고 센다 — 런 중 쉬는 요일이 있어도(예: 매주
    월요일 다크데이) 그 관은 그 기간 동안 해당 공연에 배정돼 있었다고 보는 게 합리적이라는
    판단(2026-08-08 사용자 논의). 공연 전 무대 설치·리허설과 공연 후 철수에도 관을
    쓰므로 모든 공연에 공통으로 앞 3일·뒤 1일을 더한다(2026-10-06 사용자 제안; KOPIS에
    준비/철수 기간 데이터는 없어 일괄 가정). 같은 날은 한 번만 세고, 연도 밖으로 걸친
    기간은 잘라낸다.

    공연별 p["occDays"]: 그 공연에 배정된 점유일(2025년분). 날짜가 겹칠 때는 같은 날을
    한 번만 배정해서, 한 관의 공연별 occDays를 모두 더하면 h["occupiedDays"]와 같다.
    배정 순서는 (1) 실제 공연일을 시작일 순으로 먼저, (2) 남은 날에 준비·철수 기간을
    시작일 순으로 — 즉 공연일이 준비·철수보다 우선하고, 공연 사이 빈 날은 앞 공연의
    철수가 먼저 가져간 뒤 남은 날을 뒤 공연의 준비가 쓴다.
    """
    for f in facilities:
        for h in f["halls"]:
            entries: list[tuple[date, dict, set[int], set[int]]] = []
            for p in h.get("perf", []):
                start, end = _parse_kopis_date(p.get("from")), _parse_kopis_date(p.get("to"))
                if not start or not end:
                    p["occDays"] = None
                    continue
                if end < start:
                    start, end = end, start
                run = _year_day_set(start, end)
                padded = _year_day_set(start - timedelta(days=SETUP_DAYS), end + timedelta(days=TEARDOWN_DAYS))
                entries.append((start, p, run, padded))

            if not any(padded for _, _, _, padded in entries):
                h["runDays"] = h["occupiedDays"] = h["calendarOcc"] = None
                continue

            entries.sort(key=lambda e: e[0])
            claimed: set[int] = set()
            run_all: set[int] = set()
            for _, p, run, _ in entries:
                new = run - claimed
                p["occDays"] = len(new)
                claimed |= new
                run_all |= run
            for _, p, _, padded in entries:
                new = padded - claimed
                p["occDays"] += len(new)
                claimed |= new

            h["runDays"] = len(run_all)
            h["occupiedDays"] = len(claimed)
            h["calendarOcc"] = round(h["occupiedDays"] / YEAR_DAYS * 100, 1)


def main():
    client = KopisClient()
    cache = ResponseCache(CACHE_DIR)

    logger.info("=== 1/6: %d년 전국 통계 수집 ===", YEAR)
    raw_records = collect_year_region(client, cache, YEAR, region=None)
    rows = aggregate({YEAR: raw_records})
    rows = [r for r in rows if r["좌석수"] > 0]  # 좌석수 미상(비공연 공간 등)은 웹페이지에서 제외
    logger.info("%d년 (시설,관) 행 수: %d", YEAR, len(rows))

    logger.info("=== 2/6: 시설 마스터(지역 정보) 수집 ===")
    master = fetch_facility_master(client, cache)
    logger.info("시설 마스터 총 %d건", len(master))

    logger.info("=== 3/6: 공연목록 수집 ===")
    performances = fetch_performances(client, cache)
    total_perf = sum(len(v) for v in performances.values())
    logger.info("공연목록 총 %d건, 시설 수 %d", total_perf, len(performances))

    facilities, unmatched_region = build_facilities(rows, master, performances)

    if unmatched_region:
        logger.warning(
            "지역 매칭 실패 %d개 시설 (지역 미상으로 표시): %s",
            len(unmatched_region), sorted(set(unmatched_region)),
        )

    logger.info("=== 4/6: 시설 상세(주소·좌표·관ID) 보강 ===")
    enrich_facility_details(client, cache, facilities)
    apply_map_projection(facilities)

    logger.info("=== 5/6: 공연별 상세(관ID) 조회 — 오래 걸릴 수 있습니다 ===")
    fetch_performance_halls(client, cache, facilities)

    logger.info("=== 6/6: 공연목록을 관별로 분리 ===")
    assign_perf_to_halls(facilities)
    compute_calendar_occupancy(facilities)

    data = {"year": YEAR, "setupDays": SETUP_DAYS, "teardownDays": TEARDOWN_DAYS, "facilities": facilities}
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__KOPIS_DATA__" not in template:
        raise RuntimeError("template.html에 __KOPIS_DATA__ 플레이스홀더가 없습니다.")
    output = template.replace("__KOPIS_DATA__", data_json)
    OUTPUT_PATH.write_text(output, encoding="utf-8")

    size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    hall_perf = sum(len(h["perf"]) for f in facilities for h in f["halls"])
    unmatched_perf = sum(len(f["perf"]) for f in facilities)
    logger.info("=== 완료 ===")
    logger.info(
        "시설 수: %d, 관 수: %d, 공연 수: %d(관 매칭 %d + 관 정보 없음 %d)",
        len(facilities), len(rows), hall_perf + unmatched_perf, hall_perf, unmatched_perf,
    )
    logger.info("지역 미상 시설 수: %d", len(set(unmatched_region)))
    logger.info("출력 파일: %s (%.2f MB)", OUTPUT_PATH, size_mb)


if __name__ == "__main__":
    main()
