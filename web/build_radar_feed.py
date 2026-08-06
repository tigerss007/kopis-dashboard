"""공연장 가동률 레이더: kopis-dashboard 수집/집계 로직을 재사용해 scnd.kr의
수집 전용 API(POST .../api/internal/kopis/ingest)로 매일 데이터를 전송한다.

web/build_site_data.py(정적 대시보드 빌드)와는 독립적으로 동작하며 그 파일을
수정하지 않는다. 주소·관(hall) ID는 prfplc 상세조회(mt10id 기준)에서 가져온다 —
필드명(mt10id/adres/mt13s/mt13id/prfplcnm)은 2026-08-06 실제 라이브 호출로
확인했다. 좌표(la/lo)도 응답에 포함돼 있지만 scnd 쪽에서 addr 기준으로 직접
지오코딩하기로 해서 payload에는 넣지 않는다.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from calc import aggregate  # noqa: E402
from cache import ResponseCache  # noqa: E402
from collect import collect_year_region  # noqa: E402
from config import CACHE_DIR, REGION_CODE  # noqa: E402
from facility_match import find_matches, normalize_facility_name  # noqa: E402
from kopis_client import KopisApiError, KopisClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_radar_feed")

YEAR = 2025
OUTPUT_DIR = ROOT / "output"
FEED_PATH = OUTPUT_DIR / "kopis-radar.json"
ISSUES_PATH = OUTPUT_DIR / "kopis-radar-address-issues.json"

# scnd 쪽 값 확정 전까지는 미설정 상태로 두면 전송을 건너뛰고 로컬 파일만
# 만든다(수집·주소보강 로직을 독립적으로 검증할 수 있게).
# 반드시 www.scnd.kr을 가리켜야 한다 — apex(scnd.kr)는 308로 리다이렉트되고,
# 그 과정에서 Authorization 헤더가 제거된다(push_to_scnd 참고).
SCND_INGEST_URL = os.getenv("SCND_INGEST_URL")
SCND_INGEST_API_KEY = os.getenv("SCND_INGEST_API_KEY")

DETAIL_SLEEP_SEC = 0.2
ADDRESS_ISSUE_WARN_PCT = 15.0


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
    """정규화된 시설명 -> {name, sido, gugun, id(mt10id)} 매핑 (17개 시도 조회)."""
    master: dict[str, dict] = {}
    for region_key, code in REGION_CODE.items():
        params = {"signgucode": code}
        records = _cached_fetch_list(client, cache, "prfplc", params)
        logger.info("prfplc region=%s(%s): %d건", region_key, code, len(records))
        for r in records:
            name = r.get("fcltynm", "").strip()
            norm = normalize_facility_name(name)
            if not norm:
                continue
            master[norm] = {
                "name": name,
                "sido": r.get("sidonm", ""),
                "gugun": r.get("gugunnm", ""),
                "id": r.get("mt10id", ""),
            }
    return master


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


def build_facilities(rows: list[dict], master: dict) -> list[dict]:
    facilities: dict[str, dict] = {}
    for row in rows:
        fclty_name = row["공연시설명"]
        norm = normalize_facility_name(fclty_name)
        if norm not in facilities:
            info = _lookup_master(fclty_name, norm, master)
            facilities[norm] = {
                "name": fclty_name,
                "sido": info["sido"] if info else "지역 미상",
                "gugun": info["gugun"] if info else "",
                "id": info["id"] if info else "",
                "halls": [],
            }
        facilities[norm]["halls"].append({
            "name": row["공연장(관)"],
            "seat": row["좌석수"],
            "tickets": row["총 티켓판매수"],
            "perfCount": row["상연횟수"],
            "occ": row["좌석점유율(%)"],
        })
    return list(facilities.values())


def _match_hall_id(hall_name: str, hall_master: dict[str, tuple[str, str]]) -> str | None:
    """hall_master: 정규화명 -> (원본 관명, mt13id)."""
    norm = normalize_facility_name(hall_name)
    if norm in hall_master:
        return hall_master[norm][1]
    matches = find_matches(hall_name, [raw for raw, _ in hall_master.values()])
    if not matches:
        return None
    target = matches[0]
    for raw, mt13id in hall_master.values():
        if raw == target:
            return mt13id
    return None


def enrich_addresses(client: KopisClient, cache: ResponseCache, facilities: list[dict]) -> tuple[list[dict], list[dict]]:
    """각 시설에 addr(번지수 포함 원문 주소)와 각 관의 kopisId(mt13id)를 채우고,
    주소 확보 실패한 시설 목록을 별도로 반환한다. 좌표(lat/lng)는 scnd 쪽에서
    직접 지오코딩하기로 해서 내려보내지 않는다.
    """
    issues: list[dict] = []
    for f in facilities:
        facility_id = f.pop("id", "")
        addr = None
        detail = None

        if facility_id:
            detail, from_cache = _cached_fetch_detail(client, cache, "prfplc", facility_id)
            if not from_cache:
                time.sleep(DETAIL_SLEEP_SEC)
            if detail:
                raw_addr = (detail.get("adres") or "").strip()
                addr = raw_addr or None

        f["kopisId"] = facility_id or None
        f["addr"] = addr
        if addr is None:
            issues.append({"name": f["name"], "sido": f["sido"], "gugun": f["gugun"]})

        hall_master: dict[str, tuple[str, str]] = {}
        if detail:
            for mt13 in detail.get("mt13s", []):
                raw_name = (mt13.get("prfplcnm") or "").strip()
                mt13id = mt13.get("mt13id") or None
                norm = normalize_facility_name(raw_name)
                if norm and mt13id:
                    hall_master[norm] = (raw_name, mt13id)
        for hall in f["halls"]:
            hall["kopisId"] = _match_hall_id(hall["name"], hall_master) if hall_master else None

    return facilities, issues


def push_to_scnd(payload: dict) -> bool:
    if not SCND_INGEST_URL:
        logger.warning("SCND_INGEST_URL 미설정 — 전송을 건너뜁니다(로컬 검증 모드).")
        return False

    headers = {"Content-Type": "application/json"}
    if SCND_INGEST_API_KEY:
        headers["Authorization"] = f"Bearer {SCND_INGEST_API_KEY}"

    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            # allow_redirects=False: scnd.kr(apex)로 보내면 www.scnd.kr로 308
            # 리다이렉트되는데, requests/curl 등 대부분의 HTTP 클라이언트는 보안상
            # 크로스 호스트 리다이렉트를 따라갈 때 Authorization 헤더를 조용히
            # 제거한다. 리다이렉트를 자동으로 따라가게 두면 이후 401/405로만
            # 나타나서 원인 파악이 오래 걸린다(2026-08-06 실제로 겪음) —
            # SCND_INGEST_URL은 반드시 www.scnd.kr로 설정해야 하고, 리다이렉트가
            # 오면 설정 오류로 바로 실패시킨다.
            resp = requests.post(SCND_INGEST_URL, json=payload, headers=headers, timeout=30, allow_redirects=False)
            if resp.is_redirect:
                raise RuntimeError(
                    f"SCND_INGEST_URL이 리다이렉트됩니다({resp.status_code} -> {resp.headers.get('Location')}). "
                    "www.scnd.kr을 직접 가리키도록 설정을 바로잡아야 합니다(리다이렉트를 따라가면 "
                    "Authorization 헤더가 제거되어 401로 실패함)."
                )
            resp.raise_for_status()
            logger.info("scnd ingest 전송 성공 (status=%s)", resp.status_code)
            return True
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("scnd ingest 전송 실패 (%d/3): %s", attempt, exc)
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"scnd ingest 전송이 3회 재시도 후에도 실패했습니다: {last_exc}")


def main():
    client = KopisClient()
    cache = ResponseCache(CACHE_DIR)

    logger.info("=== 1/4: %d년 전국 통계 수집 ===", YEAR)
    raw_records = collect_year_region(client, cache, YEAR, region=None)
    rows = aggregate({YEAR: raw_records})
    rows = [r for r in rows if r["좌석수"] > 0]
    logger.info("%d년 (시설,관) 행 수: %d", YEAR, len(rows))

    logger.info("=== 2/4: 시설 마스터(지역·ID) 수집 ===")
    master = fetch_facility_master(client, cache)
    logger.info("시설 마스터 총 %d건", len(master))

    logger.info("=== 3/4: 시설 join ===")
    facilities = build_facilities(rows, master)
    logger.info("join된 시설 수: %d", len(facilities))

    logger.info("=== 4/4: 주소·관(mt13id) 보강 (시설당 상세조회 1회) ===")
    facilities, issues = enrich_addresses(client, cache, facilities)

    payload = {
        "year": YEAR,
        "collectedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "facilities": facilities,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FEED_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ISSUES_PATH.write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")

    total = len(facilities)
    issue_pct = (len(issues) / total * 100) if total else 0.0
    logger.info(
        "=== 완료 === 시설 %d건, 관 %d건, 주소 미확보 %d건(%.1f%%)",
        total, sum(len(f["halls"]) for f in facilities), len(issues), issue_pct,
    )
    if issue_pct > ADDRESS_ISSUE_WARN_PCT:
        logger.warning(
            "주소 미확보 비율이 %.0f%%를 넘었습니다 — mt10id/adres 필드 가정이 맞는지 재확인하세요.",
            ADDRESS_ISSUE_WARN_PCT,
        )

    push_to_scnd(payload)


if __name__ == "__main__":
    main()
