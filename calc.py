"""2단계: 좌석 점유율 계산.

지표별 정의와 한계
------------------
필드 매핑은 2026-08-04에 KOPIS 공식 통계 페이지(perfoStatsPerfoByFct.do)에 뜬
실제 값(올림픽공원 KSPO DOME, 2025년: 상연횟수=117, 총 티켓판매수=1,151,114)을
캐시에 쌓인 prfstsPrfByFct 원본 필드의 연간 합산치와 1:1로 대조해서 확정했다.
  - prfdtcnt -> 상연횟수 (기존에 "공연일수"로 잘못 알고 있었음)
  - totnmrs  -> 총 티켓판매수 (KOPIS 공식 필드, 비공식 추정치가 아니었음)
  - prfprocnt -> 공연건수/개막편수 (본 도구에서는 미사용)
  - prfcnt   -> 정확한 공식 명칭 불명. 월 단위로 나눠 수집하다 보니 월 경계를
    걸친 공연이 이중 집계돼 정확도가 낮아(예: 실제 40건인데 합산 43건) 쓰지 않는다.

좌석 점유율 = 총 티켓판매수(totnmrs 합산) / (seatcnt * 상연횟수(prfdtcnt 합산)) * 100
  - seatcnt가 0(좌석 미상)이거나 상연횟수 합산이 0이면 산출 불가로 표기한다.

캘린더 가동률(연간 실제 가동일수 ÷ 가동가능일수)은 의도적으로 제공하지 않는다.
KOPIS API가 관(공연장) 단위로 "실제 날짜별" 데이터를 주지 않고, 있는 값 중 가장
가까운 prfdtcnt는 날짜 수가 아니라 상연 횟수라서(하루 다회 공연 시 날짜 수보다
커짐) 날짜 기준 가동률의 대용치로 쓰기에 부적합하다고 판단했다(2026-08-04,
KOPIS 웹 통계 페이지의 공연별 상세(공연기간+상연횟수+판매실적) 데이터를 API로
가져올 수 있는지 확인해봤으나 공개 Open API가 아닌 웹 전용 내부 엔드포인트라
접근 불가였음). 정확히 계산할 방법이 없는 지표를 부정확한 근사치로 보여주는
대신 산정하지 않는다.
"""
from dataclasses import dataclass

SOURCE_NOTE = (
    "출처: KOPIS 공연시설별 통계(prfstsPrfByFct). "
    "좌석점유율 = 총 티켓판매수(totnmrs) ÷ (좌석수 × 상연횟수(prfdtcnt)). "
    "캘린더가동률(실제 가동일수 기준)은 KOPIS API가 관별 실제 공연일수 정보를 "
    "제공하지 않아 산정하지 않음."
)


@dataclass
class VenueYearStats:
    year: int
    fcltynm: str  # 시설명 (prfnmfct)
    prfnmplc: str  # 관/공연장명 (prfnmplc)
    seatcnt: int = 0
    prfdtcnt: int = 0
    totnmrs: int = 0

    def to_row(self) -> dict:
        occupancy_rate = None
        occupancy_note = "산출 불가(좌석수 미상 또는 상연횟수 0)"
        if self.seatcnt > 0 and self.prfdtcnt > 0:
            occupancy_rate = round(self.totnmrs / (self.seatcnt * self.prfdtcnt) * 100, 1)
            occupancy_note = "총 티켓판매수 ÷ (좌석수×상연횟수) 기준"

        return {
            "연도": self.year,
            "공연시설명": self.fcltynm,
            "공연장(관)": self.prfnmplc,
            "좌석수": self.seatcnt,
            "상연횟수": self.prfdtcnt,
            "총 티켓판매수": self.totnmrs,
            "캘린더가동률_비고": "산정 불가(KOPIS API가 관별 실제 공연일수 정보를 제공하지 않음)",
            "좌석점유율(%)": occupancy_rate,
            "좌석점유율_비고": occupancy_note,
            "데이터출처_제한사항": SOURCE_NOTE,
        }


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def aggregate(records_by_year: dict[int, list[dict]]) -> list[dict]:
    """(연도, 시설명, 관명) 단위로 월별 원본 레코드를 합산해 최종 행을 만든다."""
    buckets: dict[tuple[int, str, str], VenueYearStats] = {}

    for year, records in records_by_year.items():
        for r in records:
            fcltynm = r.get("prfnmfct", "")
            prfnmplc = r.get("prfnmplc", "")
            key = (year, fcltynm, prfnmplc)
            if key not in buckets:
                buckets[key] = VenueYearStats(year=year, fcltynm=fcltynm, prfnmplc=prfnmplc)

            stats = buckets[key]
            stats.prfdtcnt += _to_int(r.get("prfdtcnt"))
            stats.totnmrs += _to_int(r.get("totnmrs"))
            # seatcnt는 시설의 고정 속성이므로 합산하지 않고 최댓값을 취한다
            # (월별로 동일해야 정상이지만 혹시 모를 값 누락에 대비).
            stats.seatcnt = max(stats.seatcnt, _to_int(r.get("seatcnt")))

    return [stats.to_row() for stats in buckets.values()]
