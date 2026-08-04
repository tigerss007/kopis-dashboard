"""KOPIS 가동률 산출 도구 설정."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

KOPIS_SERVICE_KEY = os.getenv("KOPIS_SERVICE_KEY")
BASE_URL = "http://www.kopis.or.kr/openApi/restful/"

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"

# KOPIS 지역코드 (signgucode / sharea 공통)
REGION_CODE = {
    "seoul": "11", "busan": "26", "daegu": "27", "inchon": "28", "gwangju": "29",
    "daejeon": "30", "ulsan": "31", "sejong": "36", "gyeonggi": "41", "gangwon": "42",
    "chungbuk": "43", "chungnam": "44", "jeonbuk": "45", "jeonnam": "46",
    "gyeongbuk": "47", "gyeongnam": "48", "jeju": "50",
}

# 기본 실행 대상 (main.py --facilities / --years / --region 인자로 덮어쓸 수 있음)
DEFAULT_YEARS = [2021, 2022, 2023, 2024, 2025]
DEFAULT_FACILITIES: list[str] = [
    # "샤롯데씨어터",
    # "충무아트센터 대극장",
]
DEFAULT_REGION = None  # None이면 전국 대상. REGION_CODE의 key(예: "seoul") 사용 가능.

# prfstsPrfByFct 엔드포인트는 조회기간이 최대 31일로 제한되어 있어
# 연 단위 조회 시 월 단위로 쪼개서 호출해야 함 (실제 호출로 확인됨).
MAX_STATS_RANGE_DAYS = 31
