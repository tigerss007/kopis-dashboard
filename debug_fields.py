"""좌석점유율_추정 계산에 쓰이는 원본 필드(prfprocnt/prfdtcnt/totnmrs/prfcnt/seatcnt)를
공연장별·월별로 그대로 출력해서, 왜 100%를 넘는 값이 나오는지 눈으로 확인하기 위한
일회성 디버그 스크립트. cache/에 있는 원본 응답을 그대로 읽으므로 API를 재호출하지 않는다.
"""
import json
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "cache"
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "좌석점유율_공식비교.xlsx"
TARGET = "예술의전당 [서울]"  # 실제 리포트에 들어간 시설(find_matches의 정확매칭)만 본다

raw_rows = []
for path in sorted(CACHE_DIR.glob("prfstsPrfByFct_*.json")):
    records = json.loads(path.read_text(encoding="utf-8"))
    for r in records:
        if r.get("prfnmfct", "") == TARGET:
            raw_rows.append(r)

raw_rows.sort(key=lambda r: (r.get("prfnmplc", ""), r.get("_year", 0)))

rows = []
for r in raw_rows:
    seatcnt = int(r.get("seatcnt") or 0)
    prfcnt = int(r.get("prfcnt") or 0)
    prfdtcnt = int(r.get("prfdtcnt") or 0)
    totnmrs = int(r.get("totnmrs") or 0)
    prfprocnt = int(r.get("prfprocnt") or 0)

    by_prfcnt = round(totnmrs / (seatcnt * prfcnt) * 100, 1) if seatcnt and prfcnt else None
    by_prfdtcnt = round(totnmrs / (seatcnt * prfdtcnt) * 100, 1) if seatcnt and prfdtcnt else None

    rows.append({
        "공연장": r.get("prfnmplc", ""),
        "prfprocnt": prfprocnt,
        "prfdtcnt(공연일수)": prfdtcnt,
        "prfcnt(공연건수)": prfcnt,
        "seatcnt(좌석수)": seatcnt,
        "totnmrs(누적관객)": totnmrs,
        "÷prfcnt 방식(%)": by_prfcnt,
        "÷prfdtcnt 방식(%)": by_prfdtcnt,
        "100% 초과 여부": "초과" if (by_prfcnt is not None and by_prfcnt > 100) else "",
    })

df = pd.DataFrame(rows)
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
df.to_excel(OUTPUT_PATH, sheet_name="공식비교", index=False)
print(f"저장 완료: {OUTPUT_PATH}")
