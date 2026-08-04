"""KOPIS API 원본 응답 캐시. 서비스키는 캐시 키/파일에 포함하지 않는다."""
import hashlib
import json
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, endpoint: str, params: dict) -> Path:
        raw = endpoint + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        safe_endpoint = endpoint.replace("/", "_")
        return self.cache_dir / f"{safe_endpoint}_{key[:16]}.json"

    def get(self, endpoint: str, params: dict) -> Any | None:
        path = self._path(endpoint, params)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def set(self, endpoint: str, params: dict, records: Any) -> None:
        path = self._path(endpoint, params)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
