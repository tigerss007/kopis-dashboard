"""시설명 표기 차이(공백, 구관/신관/폐관 표기, 지역 접미사 등)를 흡수하는 느슨한 매칭."""
import re

# 시설명 끝에 붙는 부가 정보성 괄호/대괄호 표기. 매칭용 정규화에서만 제거하고
# 원본 표시용 이름은 그대로 유지한다.
_TRAILING_BRACKETS = re.compile(r"[\(\[][^()\[\]]*[\)\]]\s*$")
_WHITESPACE = re.compile(r"\s+")


def normalize_facility_name(name: str) -> str:
    """비교용으로만 쓰는 정규화 문자열. 원본 표시에는 사용하지 않는다."""
    if not name:
        return ""
    n = name.strip()
    # 끝에 붙는 괄호 표기를 반복적으로 제거 (예: "OO극장 (구관) [서울]" -> "OO극장")
    while True:
        stripped = _TRAILING_BRACKETS.sub("", n).strip()
        if stripped == n:
            break
        n = stripped
    n = _WHITESPACE.sub("", n)  # 내부 공백 차이도 흡수
    return n.lower()


def find_matches(target_name: str, candidate_names: list[str]) -> list[str]:
    """target_name과 느슨하게 일치하는 candidate_names 원본 문자열 목록을 반환.

    일치 기준: 정규화 후 완전히 같거나, 한쪽이 다른 쪽을 포함(부분 문자열)하는 경우.
    포함 매칭은 오탐 가능성이 있으므로, 호출부(collect.py)에서 결과를 사용자에게
    로그로 보여주고 확인하도록 한다.
    """
    norm_target = normalize_facility_name(target_name)
    if not norm_target:
        return []

    exact, partial = [], []
    for cand in candidate_names:
        norm_cand = normalize_facility_name(cand)
        if not norm_cand:
            continue
        if norm_cand == norm_target:
            exact.append(cand)
        elif norm_target in norm_cand or norm_cand in norm_target:
            partial.append(cand)

    return exact if exact else partial
