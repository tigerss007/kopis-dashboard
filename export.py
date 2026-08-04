"""3단계: 엑셀 출력 (요약 / 상세 / 데이터 미확보 시트 분리)."""
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DETAIL_COLUMNS = [
    "연도", "공연시설명", "공연장(관)", "좌석수", "상연횟수", "총 티켓판매수",
    "캘린더가동률_비고", "좌석점유율(%)", "좌석점유율_비고",
    "데이터출처_제한사항",
]


def export_excel(rows: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        logger.warning("출력할 데이터가 없습니다. 빈 엑셀 파일만 생성합니다.")
        pd.DataFrame(columns=DETAIL_COLUMNS).to_excel(output_path, sheet_name="상세", index=False)
        return

    df = pd.DataFrame(rows)[DETAIL_COLUMNS]

    missing_mask = df["좌석수"] <= 0
    detail_df = df[~missing_mask].copy()
    missing_df = df[missing_mask].copy()

    # 요약: 연도 x 공연시설(관 단위) 매트릭스, 값 = 좌석점유율(%)
    if not detail_df.empty:
        summary_df = detail_df.pivot_table(
            index=["공연시설명", "공연장(관)"],
            columns="연도",
            values="좌석점유율(%)",
            aggfunc="first",
        ).reset_index()
    else:
        summary_df = pd.DataFrame(columns=["공연시설명", "공연장(관)"])

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="요약", index=False)
        detail_df.to_excel(writer, sheet_name="상세", index=False)
        missing_df.to_excel(writer, sheet_name="데이터_미확보", index=False)

    logger.info("엑셀 저장 완료: %s", output_path)
    logger.info(
        "요약 %d행 / 상세 %d행 / 데이터미확보(좌석수 0) %d행",
        len(summary_df), len(detail_df), len(missing_df),
    )
