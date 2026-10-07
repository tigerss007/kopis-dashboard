"""메인(공연시장 동향) 페이지를 만든다: main_template.html + market_data.json + 연도별 요약 -> web/index.html

사용법:  python web/build_main.py

입력
  web/market_data.json          KOPIS 공연시장 보고서 숫자 (extract_market_report.py가 만든다)
  web/data/summary_<연도>.json   공연장 대시보드의 연도별 요약 (build_site_data.py가 만든다)
  web/korea_paths.json          지도 경계
연도 목록은 site_config.ALL_YEARS와 market_data.json의 연도에서 자동으로 정해진다.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB_DIR.parent))
sys.path.insert(0, str(WEB_DIR))

from facility_match import normalize_facility_name  # noqa: E402
from site_config import ALL_YEARS  # noqa: E402

TEMPLATE = WEB_DIR / "main_template.html"
OUTPUT = WEB_DIR / "index.html"


def load_summaries() -> dict[int, dict]:
    out = {}
    for y in ALL_YEARS:
        p = WEB_DIR / "data" / f"summary_{y}.json"
        if p.exists():
            out[y] = json.loads(p.read_text(encoding="utf-8"))
        else:
            print(f"경고: {p.name} 없음 — {y}년 대시보드는 메인에서 제외됩니다 (build_site_data.py {y} 필요)")
    if not out:
        raise SystemExit("연도별 요약이 없습니다. 먼저 python web/build_site_data.py 를 실행하세요.")
    return out


def build_arenas(summaries: dict[int, dict]) -> list[dict]:
    """공연장(시설+관)을 기준으로 연도별 값을 한 줄로 합친다. 좌석수는 가장 최근 연도 값을 쓴다."""
    latest = max(summaries)
    halls: dict[tuple[str, str], dict] = {}
    for y in sorted(summaries):
        for h in summaries[y]["largeHalls"]:
            key = (h["facility"], h["hall"])
            rec = halls.setdefault(key, {"facility": h["facility"], "hall": h["hall"], "sido": h["sido"],
                                         "seat": h["seat"], "byYear": {}})
            rec["seat"] = h["seat"]
            rec["sido"] = h["sido"]
            rec["byYear"][str(y)] = {"tickets": h["tickets"], "occ": h["occ"], "calendarOcc": h["calendarOcc"],
                                     "facilityIdx": h["facilityIdx"], "hallIdx": h["hallIdx"]}
            rec["_lastYear"] = y
    out = []
    for rec in halls.values():
        y = rec.pop("_lastYear")
        idx = rec["byYear"][str(y)]["facilityIdx"]
        rec["href"] = f"{y}/index.html#/facility/{idx}"
        out.append(rec)
    out.sort(key=lambda r: -max(v["tickets"] for v in r["byYear"].values()) if r["byYear"] else 0)
    out.sort(key=lambda r: -(r["byYear"].get(str(latest), {}).get("tickets", 0)))
    return out


def link_venues(top: list[dict], summary: dict) -> list[dict]:
    """상위 공연의 공연장 문자열에서 시설을 찾아 대시보드 링크를 붙인다(이름 접두 일치, 가장 긴 쪽)."""
    names = summary.get("facilityNames", [])
    norms = [(normalize_facility_name(n), i, n) for i, n in enumerate(names)]
    year = summary["year"]
    out = []
    for row in top:
        v = normalize_facility_name(row["venue"])
        cands = [(len(n), i, name) for n, i, name in norms if len(n) >= 3 and v.startswith(n)]
        href = None
        if cands:
            best = max(c[0] for c in cands)
            top_c = [c for c in cands if c[0] == best]
            tags = lambda s: re.findall(r"\[[^\]]+\]", s)  # noqa: E731
            pick = next((c for c in top_c if tags(c[2]) and all(t in row["venue"] for t in tags(c[2]))), top_c[0])
            href = f"{year}/index.html#/facility/{pick[1]}"
        out.append({**row, "href": href})
    return out


def main() -> None:
    market = json.loads((WEB_DIR / "market_data.json").read_text(encoding="utf-8"))
    summaries = load_summaries()
    korea = json.loads((WEB_DIR / "korea_paths.json").read_text(encoding="utf-8"))

    snap_years = sorted(market["snapshots"], key=int)
    top = market["snapshots"][snap_years[-1]].pop("topPerformances", [])
    latest_summary = summaries[max(summaries)]
    dashboards = [
        {k: summaries[y][k] for k in ("year", "facilities", "halls", "performances", "tickets", "avgCalendarOcc",
                                      "avgSeatOcc", "closedFacilities")}
        for y in ALL_YEARS if y in summaries
    ]
    main_data = {
        "market": market,
        "koreaMap": {"width": korea["width"], "height": korea["height"], "paths": korea["paths"]},
        "dashboards": dashboards,
        "dashboardYears": sorted(summaries),
        "arenas": build_arenas(summaries),
        "topPerformances": link_venues(top, latest_summary),
        "generatedAt": date.today().isoformat(),
    }
    payload = json.dumps(main_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE.read_text(encoding="utf-8")
    if "__MAIN_DATA__" not in template:
        raise SystemExit("main_template.html에 __MAIN_DATA__ 가 없습니다.")
    OUTPUT.write_text(template.replace("__MAIN_DATA__", payload), encoding="utf-8")
    linked = sum(1 for r in main_data["topPerformances"] if r["href"])
    print(f"저장: {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB) · 대시보드 연도 {sorted(summaries)} · "
          f"아레나 표 {len(main_data['arenas'])}곳 · 상위 공연 {len(top)}건 중 {linked}건 링크")


if __name__ == "__main__":
    main()
