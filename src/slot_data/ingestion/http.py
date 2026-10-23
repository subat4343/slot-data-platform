"""取得元へのアクセス制御付き HTTP クライアント。

- 取得元ごとの最小リクエスト間隔（レート上限）
- robots.txt の遵守（設定で無効化不可の取得元もある）
- タイムアウト、指数バックオフ＋ジッター付き再試行、失敗理由の分類
認証回避・CAPTCHA 回避・アクセス制限回避は実装しない。403/429 は再試行せず停止理由として返す。
"""

from __future__ import annotations

import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

import httpx

from slot_data.adapters.base import FetchRequest, FetchResult
from slot_data.config import SourceConfig
from slot_data.domain.timeutil import utcnow


class FailureKind(StrEnum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    FORBIDDEN = "forbidden"  # 401/403: 認証・アクセス制限。回避しない
    RATE_LIMITED = "rate_limited"  # 429: 停止して間隔を見直す
    ROBOTS_DISALLOWED = "robots_disallowed"


RETRYABLE = {FailureKind.TIMEOUT, FailureKind.NETWORK, FailureKind.HTTP_5XX}


@dataclass
class FetchFailure(Exception):
    kind: FailureKind
    message: str
    attempts: int
    status_code: int | None = None

    def __str__(self) -> str:
        return f"{self.kind}: {self.message} (attempts={self.attempts})"


class RateLimiter:
    def __init__(self, min_interval_seconds: float, clock=time.monotonic, sleep=time.sleep) -> None:
        self.min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next_at:
                self._sleep(self._next_at - now)
                now = self._next_at
            self._next_at = now + self.min_interval


def classify_status(status_code: int) -> FailureKind | None:
    if status_code in (401, 403):
        return FailureKind.FORBIDDEN
    if status_code == 429:
        return FailureKind.RATE_LIMITED
    if 400 <= status_code < 500:
        return FailureKind.HTTP_4XX
    if status_code >= 500:
        return FailureKind.HTTP_5XX
    return None


class PoliteHttpClient:
    def __init__(
        self,
        config: SourceConfig,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.limiter = RateLimiter(config.rate_limit.min_interval_seconds, sleep=sleep)
        self.client = httpx.Client(
            transport=transport,
            timeout=config.retry.timeout_seconds,
            headers={"User-Agent": config.user_agent},
            follow_redirects=True,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def close(self) -> None:
        self.client.close()

    def _robots_allows(self, url: str) -> bool:
        if not self.config.access_policy.respect_robots_txt:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(base)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                self.limiter.wait()
                resp = self.client.get(base + "/robots.txt")
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except httpx.HTTPError:
                # robots を確認できない場合は安全側（取得しない）
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[base] = parser
        return parser.can_fetch(self.config.user_agent, url)

    def backoff_delay(self, attempt: int) -> float:
        retry = self.config.retry
        delay = min(retry.max_delay_seconds, retry.base_delay_seconds * (2 ** (attempt - 1)))
        return delay * (0.5 + self._rng.random() / 2)

    def fetch(self, request: FetchRequest) -> FetchResult:
        if not self._robots_allows(request.url):
            raise FetchFailure(FailureKind.ROBOTS_DISALLOWED, request.url, attempts=0)
        attempts = self.config.retry.max_attempts
        last: FetchFailure | None = None
        for attempt in range(1, attempts + 1):
            self.limiter.wait()
            try:
                resp = self.client.request(request.method, request.url, params=list(request.params) or None)
            except httpx.TimeoutException as exc:
                last = FetchFailure(FailureKind.TIMEOUT, str(exc), attempt)
            except httpx.HTTPError as exc:
                last = FetchFailure(FailureKind.NETWORK, str(exc), attempt)
            else:
                kind = classify_status(resp.status_code)
                if kind is None:
                    return FetchResult(
                        content=resp.content,
                        content_type=resp.headers.get("content-type", "application/octet-stream"),
                        fetched_at=utcnow(),
                        resource_locator=str(resp.url),
                        status_code=resp.status_code,
                        response_metadata={
                            "status_code": resp.status_code,
                            "headers": {
                                k: v
                                for k, v in resp.headers.items()
                                if k.lower() in ("content-type", "last-modified", "etag", "date")
                            },
                            "attempts": attempt,
                        },
                    )
                last = FetchFailure(kind, f"HTTP {resp.status_code}", attempt, resp.status_code)
            if last.kind not in RETRYABLE or attempt == attempts:
                break
            self._sleep(self.backoff_delay(attempt))
        assert last is not None
        raise last
