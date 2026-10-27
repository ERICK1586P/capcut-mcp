"""Small FetchLayer TikTok client for the CapCut B-roll workflow.

Credentials are read at runtime from FETCHLAYER_API_KEY in the process
environment or the repository's ignored .env file. Never log the key.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.fetchlayer.dev/tiktok"
DEFAULT_CACHE = Path(os.environ.get(
    "FETCHLAYER_CACHE_DIR",
    str(Path(os.environ.get("VECTCUT_DRAFTS_DIR", "/drafts")) / ".capcut-mcp-broll-cache"),
))
MAX_RESPONSE_BYTES = 200 * 1024 * 1024


class FetchLayerError(RuntimeError):
    """An API or download failure with credentials excluded from the message."""


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE entries without overriding process environment variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"\d{8,30}", str(value)):
        raise ValueError("TikTok video id must contain 8-30 digits")
    return str(value)


class FetchLayerTikTok:
    """Search TikTok videos and cache downloadable MP4 media."""

    def __init__(self, api_key: str | None = None, cache_dir: Path | str | None = None):
        repo_root = Path(__file__).resolve().parents[1]
        load_env_file(repo_root / ".env")
        self.api_key = (api_key or os.environ.get("FETCHLAYER_API_KEY", "")).strip()
        if not self.api_key:
            raise FetchLayerError("FETCHLAYER_API_KEY is missing from environment/.env")
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE

    def _request(self, url: str, *, method: str = "GET", body: dict | None = None,
                 timeout: int = 60) -> tuple[bytes, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json, video/mp4"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace")
            raise FetchLayerError(f"FetchLayer HTTP {exc.code}: {detail[:500]}") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise FetchLayerError(f"FetchLayer request failed: {type(exc).__name__}") from None
        if len(payload) > MAX_RESPONSE_BYTES:
            raise FetchLayerError("FetchLayer response exceeded the 200 MB safety limit")
        return payload, content_type

    def available_credits(self) -> int:
        payload, _ = self._request("https://api.fetchlayer.dev/fetchlayer/me/credits")
        try:
            credits = json.loads(payload).get("credits")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            raise FetchLayerError("FetchLayer returned an invalid credit balance") from None
        if isinstance(credits, bool) or not isinstance(credits, (int, float)):
            raise FetchLayerError("FetchLayer credit balance was not numeric")
        return int(credits)

    def cached_path(self, video_id: str) -> Path:
        return self.cache_dir / f"tiktok_{_safe_id(video_id)}.mp4"

    def search(self, query: str, limit: int = 12) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 36:
            raise ValueError("limit must be between 1 and 36")
        payload, _ = self._request(
            f"{API_ROOT}/search-videos", method="POST", body={"query": query, "limit": limit}
        )
        try:
            data = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FetchLayerError("FetchLayer returned invalid JSON for TikTok search") from None
        candidates = []
        for video in data.get("videos", []):
            if video.get("isPhotoPost") or not video.get("id"):
                continue
            assets = [asset for asset in video.get("assets", []) if asset.get("kind") == "video"]
            if assets and assets[0].get("downloadUrl"):
                candidates.append(video)
        data["videos"] = candidates
        return data

    def download(self, video: dict[str, Any]) -> Path:
        video_id = _safe_id(video.get("id", ""))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cached_path(video_id)
        if target.is_file() and target.stat().st_size > 0:
            return target
        assets = [asset for asset in video.get("assets", []) if asset.get("kind") == "video"]
        if not assets or not assets[0].get("downloadUrl"):
            raise FetchLayerError(f"TikTok video {video_id} has no downloadable video asset")
        # Build the endpoint from the validated id; never trust an arbitrary URL.
        url = f"{API_ROOT}/media?{urlencode({'video': video_id})}"
        payload, content_type = self._request(url, timeout=120)
        if not payload or "json" in content_type.lower() or b"ftyp" not in payload[:32]:
            excerpt = payload[:300].decode("utf-8", "replace")
            raise FetchLayerError(f"TikTok media download did not return an MP4 file: {excerpt}")
        temporary = target.with_suffix(f".mp4.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_bytes(payload)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def download_id(self, video_id: str) -> Path:
        """Download by a TikTok video id returned from search()."""
        safe_id = _safe_id(video_id)
        return self.download({
            "id": safe_id,
            "assets": [{"kind": "video", "downloadUrl": f"{API_ROOT}/media?video={safe_id}"}],
        })


__all__ = ["FetchLayerError", "FetchLayerTikTok", "load_env_file"]
