"""KOPIS Open API 클라이언트.

Base URL / 엔드포인트 / 파라미터 / 응답 필드는 공식 문서(JS 렌더링이라
자동 수집 불가)가 아니라, 실제 서비스키로 라이브 호출한 결과를 근거로 확정했다.
확정 근거는 README.md의 "API 스펙 확정 근거" 절 참고.
"""
import logging
import time
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import BASE_URL, KOPIS_SERVICE_KEY

logger = logging.getLogger(__name__)

# 페이지네이션 무한루프 방지용 안전장치.
# 실제 호출 결과 cpage당 반환 건수가 'rows' 파라미터와 무관하게 들쭉날쭉했고
# (요청한 rows보다 훨씬 많이 오기도 함), 결과가 0건인 페이지가 나올 때까지
# 순회하는 방식만 신뢰할 수 있었다. 그래도 혹시 모를 무한루프에 대비해 상한을 둔다.
MAX_PAGES = 500


class KopisApiError(Exception):
    pass


class KopisClient:
    def __init__(self, service_key: str | None = None, max_retries: int = 5, backoff_base: float = 1.0):
        self.service_key = service_key or KOPIS_SERVICE_KEY
        if not self.service_key:
            raise RuntimeError(
                "KOPIS_SERVICE_KEY가 설정되지 않았습니다. .env 파일에 서비스키를 넣어주세요."
            )
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self.session = requests.Session()
        # 네트워크/서버 오류(5xx)에 대한 저수준 재시도. KOPIS 자체 에러코드(returncode)는
        # HTTP 200으로 오므로 별도로 _get()에서 처리한다.
        retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5, status_forcelist=(500, 502, 503, 504))
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _get(self, path: str, params: dict) -> str:
        url = BASE_URL + path
        query = {k: v for k, v in params.items() if v is not None}
        query["service"] = self.service_key

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=query, timeout=20)
                resp.raise_for_status()
                resp.encoding = "utf-8"
                return resp.text
            except requests.RequestException as exc:
                last_exc = exc
                wait = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "KOPIS 호출 실패 (%s/%s) %s: %s -> %.1fs 후 재시도",
                    attempt, self.max_retries, path, exc, wait,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)
        raise KopisApiError(f"{path} 호출을 {self.max_retries}회 재시도했지만 실패했습니다: {last_exc}")

    @staticmethod
    def _item_tag(endpoint: str) -> str:
        base = endpoint.split("/")[0]
        if base == "boxoffice":
            return "boxof"
        if base.startswith("prfsts"):
            return "prfst"
        return "db"

    def _parse(self, endpoint: str, xml_text: str) -> list[dict]:
        # 페이지네이션 마지막 페이지에서 KOPIS가 빈 XML이 아니라 완전히
        # 빈 응답 본문(길이 0)을 내려주는 경우가 실제 호출로 확인됨.
        if not xml_text.strip():
            return []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            # 실제로 확인된 사례: 일부 공연 상세 응답에 이스케이프 안 된 특수문자/제어문자가
            # 섞여 XML 자체가 깨져서 온다(2026-08-07). 재시도해도 서버가 같은 응답을 주므로
            # KopisApiError로 바꿔 호출부의 기존 실패 처리(캐시에 None 기록 후 건너뜀)를 탄다.
            raise KopisApiError(f"{endpoint} 응답 XML 파싱 실패: {exc}") from exc

        # KOPIS 에러 응답은 정상 응답의 루트/아이템 태그와 무관하게 항상
        # <dbs><db><returncode>..</returncode><errmsg>..</errmsg></db></dbs> 형태로 온다.
        # (실제 호출로 확인: prfstsPrfByFct에 31일 초과 기간을 주면 이 형태로 에러가 옴)
        err_item = root.find("db")
        if err_item is not None and err_item.find("returncode") is not None:
            code = err_item.findtext("returncode")
            msg = err_item.findtext("errmsg")
            raise KopisApiError(f"{endpoint} API 오류 [{code}]: {msg}")

        item_tag = self._item_tag(endpoint)
        items = root.findall(item_tag)

        records = []
        for item in items:
            record = {child.tag: (child.text or "").strip() for child in item if child.tag != "mt13s"}
            mt13s = item.find("mt13s")
            if mt13s is not None:
                record["mt13s"] = [
                    {c.tag: (c.text or "").strip() for c in mt13}
                    for mt13 in mt13s.findall("mt13")
                ]
            records.append(record)
        return records

    def fetch_list(self, endpoint: str, params: dict) -> list[dict]:
        """cpage를 늘려가며, 빈 페이지가 나올 때까지 목록형 엔드포인트를 순회한다."""
        all_records: list[dict] = []
        cpage = 1
        while cpage <= MAX_PAGES:
            page_params = {**params, "cpage": cpage, "rows": params.get("rows", 100)}
            xml_text = self._get(endpoint, page_params)
            records = self._parse(endpoint, xml_text)
            if not records:
                break
            all_records.extend(records)
            cpage += 1
        else:
            logger.warning("%s: 페이지네이션이 MAX_PAGES(%s)에 도달해 중단했습니다.", endpoint, MAX_PAGES)
        return all_records

    def fetch_detail(self, endpoint: str, item_id: str) -> dict | None:
        xml_text = self._get(f"{endpoint}/{item_id}", {})
        records = self._parse(endpoint, xml_text)
        return records[0] if records else None
