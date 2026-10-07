"""KOPIS 「공연시장 티켓판매 현황 분석 보고서」(PDF)에서 표를 뽑아 web/market_data.json에 병합한다.

사용법:  python web/extract_market_report.py <보고서.pdf>

- 연도를 코드에 박아두지 않는다. 각 표의 머리글("2025년 2024년 ...")에서 연도 열을 읽으므로
  내년에 2026년 보고서를 같은 명령으로 넣으면 market_data.json에 2026년이 추가된다.
- 같은 연도가 여러 보고서에 나오면(예: 2026년 보고서 안의 2025년 열) 나중에 넣은 보고서 값이
  덮어쓰고, 연도별 출처(yearSource)도 함께 바뀐다. 추출일이 다르면 값이 조금 변할 수 있다.
- 금액은 모두 원 단위 정수로 저장한다(보고서의 천원 표는 ×1000).
- 보고서에서 가져오는 것은 숫자와 사실 정보뿐이다. 해설 문장·도표 이미지는 가져오지 않는다.
- 끝에서 합계 검증(장르 합 ≤ 전체, 지역 합 = 합계 등)을 하고 어긋나면 경고를 출력한다.
"""
import io
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_PATH = Path(__file__).resolve().parent / "market_data.json"

NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
YEAR_TOKEN = re.compile(r"(\d{4})년")

# 보고서 지역명 -> 대시보드(지도)에서 쓰는 짧은 이름
REGION_SHORT = {
    "서울특별시": "서울", "경기도": "경기", "인천광역시": "인천", "강원특별자치도": "강원",
    "대전광역시": "대전", "세종특별자치시": "세종", "충청북도": "충북", "충청남도": "충남",
    "부산광역시": "부산", "대구광역시": "대구", "울산광역시": "울산", "경상북도": "경북",
    "경상남도": "경남", "광주광역시": "광주", "전북특별자치도": "전북", "전라남도": "전남",
    "제주특별자치도": "제주",
}
VENUE_SIZES = [
    "1만 석 이상", "5,000석~1만 석 미만", "1,000석~5,000석 미만", "500석~1,000석 미만",
    "300석~500석 미만", "1석~300석 미만", "0석(좌석미상)",
]
# 장르별 5개년 "공연실적" 표 (표 캡션에 들어 있는 문구로 찾는다)
GENRE_TABLES = {
    "연극": r"표-\d+\s+\d{4}년\s+연극\s+공연실적",
    "뮤지컬": r"표-\d+\s+\d{4}년\s+뮤지컬\s+공연실적",
    "서양음악": r"표-\d+\s+\d{4}년\s+서양음악\(클래식\)\s+공연실적",
    "한국음악": r"표-\d+\s+\d{4}년\s+한국음악\(국악\)\s+공연실적",
    "무용": r"표-\d+\s+\d{4}년\s+무용\(서양/한국\)\s+공연실적",
    "대중음악": r"표-\d+\s+\d{4}년\s+대중음악\s+공연실적",
}
FEATURES = ["아동 공연", "내한 공연", "대학로 공연", "오픈런", "축제"]


def ints(s):
    return [int(x.replace(",", "")) for x in NUM.findall(s)]


def block(text, caption_re, stop_re=r"표-\d+\s"):
    """caption_re로 시작해 다음 표 캡션 직전까지의 텍스트(캡션 다음 줄부터)."""
    m = re.search(caption_re, text)
    if not m:
        raise SystemExit(f"표를 찾지 못했습니다: {caption_re}")
    rest = text[m.end():]
    nxt = re.search(stop_re, rest)
    return rest[: nxt.start()] if nxt else rest


def header_years(blk):
    for line in blk.splitlines():
        ys = YEAR_TOKEN.findall(line)
        if len(ys) >= 2 and re.fullmatch(r"(?:구분\s*)?(?:\s*\d{4}년)+\s*", line):
            return [int(y) for y in ys]
    raise SystemExit("연도 머리글을 찾지 못했습니다")


def rows_with_prefix(blk, prefixes):
    """줄이 prefix로 시작하는 행의 숫자 목록. prefix는 길이순으로 먼저 맞춘다."""
    out = {}
    for line in blk.splitlines():
        s = line.strip()
        for p in sorted(prefixes, key=len, reverse=True):
            if s.startswith(p):
                nums = ints(s[len(p):])
                if nums and p not in out:
                    out[p] = nums
                break
    return out


def pct_tokens(s):
    """'▲11.4%' / '▼2.4%p' / '82.8%' 같은 토큰을 부호 있는 실수로."""
    vals = []
    for m in re.finditer(r"([▲▼]?)(\d+(?:\.\d+)?)%", s):
        v = float(m.group(2))
        vals.append(-v if m.group(1) == "▼" else v)
    return vals


def main(pdf_path):
    reader = PdfReader(pdf_path)
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages)

    title = next((l.strip() for l in pages[2].splitlines() if "분석" in l and "공연시장" in l), "")
    rpt_year = int(re.search(r"(\d{4})년\s+공연시장 티켓판매 현황 분석 보고서", text).group(1))
    extracted_on = re.search(r"데이터 추출일\s*\n?.*?(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.", text, re.S)
    ext = None
    m = re.search(r":\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*\n:\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*~", text)
    if m:
        ext = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    connected = re.search(r"총\s*(\d+)개\s*(?:연계기관|티켓 발권 시스템)", text)
    report = {
        "id": f"kopis-market-{rpt_year}",
        "title": f"{rpt_year}년 공연시장 티켓판매 현황 분석 보고서",
        "publisher": "(재)예술경영지원센터 공연정보팀 (KOPIS 공연예술통합전산망)",
        "reportYear": rpt_year,
        "extractedOn": ext,
        "connectedSystems": int(connected.group(1)) if connected else None,
    }

    data = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
    data.setdefault("units", {"sales": "원", "tickets": "매", "shows": "건", "sessions": "회"})
    data["reports"] = [r for r in data.get("reports", []) if r["id"] != report["id"]] + [report]
    year_source = data.setdefault("yearSource", {})
    for key in ("overall", "genres", "regions", "venueSizes", "monthly", "top10Share", "snapshots"):
        data.setdefault(key, {})

    def put(path, year, value):
        d = data
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d.setdefault(path[-1], {})[str(year)] = value
        year_source[str(year)] = report["id"]

    # ---- 1) 전체 공연실적 (표-1) -------------------------------------------------
    blk = block(text, r"표-1\s+\d{4}년\s+전체 공연실적")
    yrs = header_years(blk)
    rows = rows_with_prefix(blk, ["공연건수(건)", "공연회차(회)", "티켓예매수(매)", "티켓판매액(원)"])
    for i, y in enumerate(yrs):
        put(["overall"], y, {
            "shows": rows["공연건수(건)"][i], "sessions": rows["공연회차(회)"][i],
            "tickets": rows["티켓예매수(매)"][i], "sales": rows["티켓판매액(원)"][i],
        })

    # ---- 2) 장르별 5개년 공연실적 -------------------------------------------------
    for genre, cap in GENRE_TABLES.items():
        blk = block(text, cap)
        yrs = header_years(blk)
        rows = rows_with_prefix(blk, ["공연건수(건)", "공연회차(회)", "티켓예매수(매)", "티켓판매액(원)"])
        for i, y in enumerate(yrs):
            put(["genres", genre], y, {
                "shows": rows["공연건수(건)"][i], "sessions": rows["공연회차(회)"][i],
                "tickets": rows["티켓예매수(매)"][i], "sales": rows["티켓판매액(원)"][i],
            })

    # ---- 3) 지역별 (표-5: 건수/회차, 표-6: 예매수/판매액(천원)) ----------------------
    for cap, k1, k2, mult in [(r"표-5\s+\d{4}~\d{4}년 전국 지역별 공연건수/공연회차", "shows", "sessions", 1),
                              (r"표-6\s+\d{4}~\d{4}년 전국 지역별 티켓예매수/티켓판매액", "tickets", "sales", 1000)]:
        blk = block(text, cap)
        yrs = header_years(blk)
        rows = rows_with_prefix(blk, list(REGION_SHORT) + ["합계"])
        for full, nums in rows.items():
            if len(nums) < 2 * len(yrs):
                raise SystemExit(f"지역 행 숫자 부족: {full} {nums}")
            short = "전국" if full == "합계" else REGION_SHORT[full]
            for i, y in enumerate(yrs):
                cur = data["regions"].setdefault(short, {}).setdefault(str(y), {})
                cur[k1] = nums[2 * i]
                cur[k2] = nums[2 * i + 1] * mult
        for y in yrs:
            year_source[str(y)] = report["id"]

    # ---- 4) 공연장 규모별 (표-7, 표-8) --------------------------------------------
    for cap, k1, k2, mult in [(r"표-7\s+\d{4}~\d{4}년 시설 규모별 공연건수/공연회차", "shows", "sessions", 1),
                              (r"표-8\s+\d{4}~\d{4}년 시설 규모별 티켓예매수/티켓판매액", "tickets", "sales", 1000)]:
        blk = block(text, cap)
        yrs = header_years(blk)
        rows = rows_with_prefix(blk, VENUE_SIZES + ["공연시장 전체"])
        for label, nums in rows.items():
            key = "전체" if label == "공연시장 전체" else label
            for i, y in enumerate(yrs):
                cur = data["venueSizes"].setdefault(key, {}).setdefault(str(y), {})
                cur[k1] = nums[2 * i]
                cur[k2] = nums[2 * i + 1] * mult

    # ---- 5) 월별 (표-4: 보고서 연도와 전년) ----------------------------------------
    blk = block(text, r"표-4\s+\d{4}~\d{4}년 월별 공연시장 현황", stop_re=r"표-5\s")
    cur_year = None
    for line in blk.splitlines():
        s = line.strip()
        if re.fullmatch(r"\d{4}년", s):
            cur_year = int(s[:4])
            continue
        m = re.match(r"(\d{1,2})월\s+(.*)", s)
        if m and cur_year:
            nums = ints(m.group(2))
            if len(nums) == 4:
                data["monthly"].setdefault(str(cur_year), [])
                lst = [r for r in data["monthly"][str(cur_year)] if r["month"] != int(m.group(1))]
                lst.append({"month": int(m.group(1)), "shows": nums[0], "sessions": nums[1],
                            "tickets": nums[2], "sales": nums[3] * 1000})
                data["monthly"][str(cur_year)] = sorted(lst, key=lambda r: r["month"])
                year_source[str(cur_year)] = report["id"]

    # ---- 6) 상위 10개 공연 쏠림 (표-10) -------------------------------------------
    blk = block(text, r"표-10\s+\d{4}~\d{4}년 상위 10개 공연 티켓판매 규모 및 비율")
    yrs = header_years(blk)
    allv = top = ratio = None
    for line in blk.splitlines():
        s = line.strip()
        if s.startswith("공연시장 전체"):
            allv = ints(s)
        elif s.startswith("상위 10개 공연"):
            top = ints(s[len("상위 10개 공연"):])  # 라벨의 "10"이 숫자로 섞이지 않게 뗀다
        elif s.startswith("상위 10개 판매 비율"):
            ratio = pct_tokens(s)
    for i, y in enumerate(yrs):
        put(["top10Share"], y, {"top10": top[i], "all": allv[i], "ratioPct": ratio[i]})
        calc = round(top[i] / allv[i] * 100, 1)
        if abs(calc - ratio[i]) > 0.1:  # 보고서의 비율과 직접 계산한 비율이 다르면 파싱이 밀린 것
            raise SystemExit(f"상위 10개 비중 불일치 {y}: 계산 {calc} vs 표 {ratio[i]}")

    # ---- 7) 보고서 연도 스냅샷: 대중예술 비중(표-2), 공연 특성별(표-9), 상위 20개 공연(표-11) ----
    snap = data["snapshots"].setdefault(str(rpt_year), {})

    blk = block(text, r"표-2\s+\d{4}년 대중예술 및 대중예술 제외 전년 대비 증감률")
    pa = {}
    for line in blk.splitlines():
        s = line.strip()
        for label, key in [("전체 ", "all"), ("대중예술 ", "popular"), ("제외 ", "other")]:
            if s.startswith(label) and key not in pa and "%" in s:
                n = ints(re.sub(r"\d+(?:\.\d+)?%", " ", s))
                pc = pct_tokens(s)
                # 공연건수, 공연회차, 티켓예매수, 티켓판매액(천원) 각각: 값, 비율%, 증감%
                pa[key] = {
                    "shows": n[0], "sessions": n[1], "tickets": n[2], "sales": n[3] * 1000,
                    "sharePct": {"shows": pc[0], "sessions": pc[2], "tickets": pc[4], "sales": pc[6]},
                    "yoyPct": {"shows": pc[1], "sessions": pc[3], "tickets": pc[5], "sales": pc[7]},
                }
                break
    snap["popularArts"] = pa

    blk = block(text, r"표-9\s+\d{4}년 공연 특성별 티켓판매 현황 및 비율")
    feats = []
    for line in blk.splitlines():
        s = line.strip()
        for f in FEATURES:
            if s.startswith(f + " ") and "%" in s:
                rest = s[len(f):]
                vals = re.findall(r"[▲▼]?\d[\d,]*(?:\.\d+)?%?", rest)
                nums = [v for v in vals if "%" not in v]
                pcs = pct_tokens(rest)
                n = [int(x.replace(",", "")) for x in nums]
                feats.append({"name": f, "shows": n[0], "sessions": n[1], "tickets": n[2], "sales": n[3],
                              "avgPerShow": n[4], "avgPerTicket": n[5],
                              "yoyPct": {"shows": pcs[0], "sessions": pcs[1], "tickets": pcs[2], "sales": pcs[3]}})
                break
    snap["features"] = feats
    snap["topPerformances"] = parse_top_performances(block(text, r"표-11\s+\d{4}년 공연시장 티켓판매액 상위 20개 공연 목록"))

    data["_note"] = ("web/extract_market_report.py가 KOPIS 보고서 PDF에서 뽑아 병합한 값. 직접 고치지 말고 "
                     "스크립트로 다시 만들 것. 금액 단위는 원.")

    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    verify(data, text)
    print("저장:", OUT_PATH)


GENRE_NAMES = ["대중음악", "뮤지컬", "서커스/마술", "연극", "무용", "서양음악", "한국음악", "대중무용", "복합"]
REGION_LONG = list(REGION_SHORT)
# PDF 텍스트 추출이 글자를 띄어 쓰는 알려진 경우
TITLE_FIXES = {"DA Y6": "DAY6"}


def parse_top_performances(blk):
    """표-11(티켓판매액 상위 20개 공연). 순위는 표의 순서. 좌석수 토큰("43,000석")으로 행을 나눈다."""
    flat = re.sub(r"\s+", " ", blk)
    # 표 뒤에 붙는 해설 문장("- 대중음악과 ...")은 버린다
    cut = re.search(r"\s-\s{1,2}\S", flat)
    flat = flat[: cut.start()] if cut else flat
    parts = re.split(r"(\d[\d,]*석)", flat)
    rows = []
    for pre, seat in zip(parts[0::2], parts[1::2]):
        m = re.search(r"(\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})", pre)
        if not m:
            continue
        head, tail = pre[: m.start()].strip(), pre[m.end():].strip()
        # 머리글줄("장르 공연명 공연기간 ...")이 첫 행 앞에 붙어 있으면 떼어낸다
        head = re.sub(r"^.*공연기간\s*공연시설/공연장\s*지역\s*좌석수\s*", "", head).strip()
        gm = re.match(r"(%s)\s*(\([^)]*\))?\s*(.*)$" % "|".join(map(re.escape, GENRE_NAMES)), head)
        region = next((r for r in REGION_LONG if tail.endswith(r)), None)
        if not gm or not region:
            raise SystemExit(f"상위 공연 행을 해석하지 못했습니다: {pre!r}")
        title = gm.group(3).strip()
        for bad, good in TITLE_FIXES.items():
            title = title.replace(bad, good)
        rows.append({
            "rank": len(rows) + 1,
            "genre": gm.group(1),
            "subgenre": (gm.group(2) or "").strip("()") or None,
            "title": title,
            "from": m.group(1), "to": m.group(2),
            "venue": tail[: -len(region)].strip(),
            "region": REGION_SHORT[region],
            "seats": int(seat[:-1].replace(",", "")),
        })
    if len(rows) != 20:
        raise SystemExit(f"상위 20개 공연이 {len(rows)}건으로 읽혔습니다")
    return rows


def verify(data, text):
    problems = []
    years = sorted(data["overall"], key=int)
    # 장르 합 <= 전체
    for y in years:
        for k in ("shows", "sessions", "tickets", "sales"):
            tot = data["overall"][y][k]
            s = sum(g[y][k] for g in data["genres"].values() if y in g)
            if s > tot:
                problems.append(f"장르 합이 전체보다 큼 {y} {k}: {s} > {tot}")
            elif tot and s < tot * 0.8:
                problems.append(f"장르 합이 전체의 80% 미만 {y} {k}: {s}/{tot}")
    # 지역 합 == 합계
    for y in years:
        for k in ("shows", "sessions", "tickets", "sales"):
            nat = data["regions"]["전국"].get(y, {}).get(k)
            if nat is None:
                continue
            s = sum(v[y][k] for r, v in data["regions"].items() if r != "전국" and y in v)
            tol = 1 if k != "sales" else 20000
            if abs(s - nat) > tol:
                problems.append(f"지역 합 불일치 {y} {k}: {s} vs {nat}")
            ov = data["overall"][y][k]
            if abs(nat - ov) > (5 if k != "sales" else 20000):
                problems.append(f"전국 합계와 표-1 불일치 {y} {k}: {nat} vs {ov}")
    # 규모별 합 == 전체
    for y in years:
        for k in ("shows", "sessions", "tickets", "sales"):
            vs = data["venueSizes"]
            if y not in vs.get("전체", {}):
                continue
            s = sum(v[y][k] for n, v in vs.items() if n != "전체" and y in v)
            tot = vs["전체"][y][k]
            tol = 10 if k != "sales" else 20000
            if abs(s - tot) > tol:
                problems.append(f"규모별 합 불일치 {y} {k}: {s} vs {tot}")
    # 월별 합 == 연간
    for y, rows in data["monthly"].items():
        if len(rows) == 12:
            # 공연건수는 여러 달에 걸친 공연이 달마다 한 번씩 세어져 월별 합이 연간 건수보다 크다(검증 제외).
            for k in ("sessions", "tickets", "sales"):
                s = sum(r[k] for r in rows)
                tot = data["overall"][y][k]
                tol = 60 if k == "sessions" else (5 if k == "tickets" else 20000)
                if abs(s - tot) > tol:
                    problems.append(f"월별 합 불일치 {y} {k}: {s} vs {tot}")
        else:
            problems.append(f"월별 행 수가 12가 아님 {y}: {len(rows)}")
    # 표-1 파생 단가 확인(보고서 본문: 2025년 티켓 1매당 69,928원)
    o = data["overall"]
    for y in years:
        pt = round(o[y]["sales"] / o[y]["tickets"])
        print(f"  {y}: 건수 {o[y]['shows']:,} / 예매수 {o[y]['tickets']:,} / 판매액 {o[y]['sales']:,} / 티켓 1매당 {pt:,}원")
    print("검증 문제:", "없음" if not problems else "")
    for p in problems:
        print("  !", p)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
