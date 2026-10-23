"""自動収集の枠組み: 許可ゲート・robots・再試行・途中失敗後の再開。

ここで使う FixtureAdapter はテスト専用（httpx.MockTransport でローカル応答を返す）で、実サイトには接続しない。
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from slot_data.adapters import get_adapter
from slot_data.adapters.base import AccessProhibitedError, FetchRequest, SourceAdapter
from slot_data.adapters.manual import ManualJsonParser
from slot_data.config import AccessPolicy, CollectTarget, RetryConfig, SourceConfig
from slot_data.ingestion.http import FailureKind, FetchFailure, PoliteHttpClient
from slot_data.ingestion.pipeline import IngestionPipeline
from slot_data.models import CollectionRun, DailyMachineStat, DataQualityIssue, RawPayload
from tests.helpers import manual_doc

pytestmark = pytest.mark.integration


class FixtureAdapter(SourceAdapter):
    source_code = "fixture"
    adapter_version = "test-1"

    def plan(self, target, business_dates):
        return [
            FetchRequest(
                kind="hall_daily",
                url=f"https://fixture.test/h/{target.source_hall_id}/{d.isoformat()}",
                hall_code=target.hall_code,
                business_date=d,
            )
            for d in business_dates
        ]

    def fetch(self, request, client: PoliteHttpClient):
        return client.fetch(request)

    def parser_for(self, request_kind, content_type):
        return ManualJsonParser()


def _permitted(**kw) -> SourceConfig:
    return SourceConfig(
        code="fixture",
        name="fixture",
        access_policy=AccessPolicy(automated_fetch="permitted", permission_reference="test-only"),
        retry=RetryConfig(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.02, timeout_seconds=1),
        **kw,
    )


def _transport(fail_dates: set[str], calls: list[str], robots: str = "User-agent: *\nAllow: /\n"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        day = request.url.path.rsplit("/", 1)[-1]
        if day in fail_dates:
            return httpx.Response(503)
        doc = manual_doc(
            [{"machine_number": "301", "fields": {"total_games": 1000}}],
            observed_at=f"{day}T23:30:00+09:00",
            business_date=day,
        )
        return httpx.Response(
            200, content=json.dumps(doc).encode(), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


def test_site_adapters_refuse_without_permission(ctx):
    for code in ("pscube", "daidata_online"):
        adapter = get_adapter(
            SourceConfig(code=code, name=code, access_policy=AccessPolicy(automated_fetch="prohibited"))
        )
        with pytest.raises(AccessProhibitedError):
            adapter.plan(
                CollectTarget(source=code, hall_code="H001", source_hall_id="x"), [date(2026, 10, 20)]
            )
    # permitted でも根拠が無ければ拒否
    adapter = get_adapter(
        SourceConfig(code="pscube", name="p", access_policy=AccessPolicy(automated_fetch="permitted"))
    )
    with pytest.raises(AccessProhibitedError, match="permission_reference"):
        adapter.ensure_automated_fetch_allowed()


def test_collect_refuses_before_any_request(ctx, hall):
    calls: list[str] = []
    config = SourceConfig(code="fixture", name="f", access_policy=AccessPolicy(automated_fetch="prohibited"))
    client = PoliteHttpClient(config, transport=_transport(set(), calls), sleep=lambda s: None)
    with pytest.raises(AccessProhibitedError):
        IngestionPipeline(ctx).collect(
            FixtureAdapter(config),
            CollectTarget(source="fixture", hall_code="H001", source_hall_id="1"),
            [date(2026, 10, 20)],
            client,
        )
    assert calls == []


def test_robots_disallow_stops_fetch(ctx):
    calls: list[str] = []
    client = PoliteHttpClient(
        _permitted(),
        transport=_transport(set(), calls, robots="User-agent: *\nDisallow: /h/\n"),
        sleep=lambda s: None,
    )
    with pytest.raises(FetchFailure) as exc:
        client.fetch(FetchRequest(kind="x", url="https://fixture.test/h/1/2026-10-20"))
    assert exc.value.kind == FailureKind.ROBOTS_DISALLOWED
    assert calls == ["/robots.txt"]


def test_retry_with_backoff_then_classify(ctx):
    calls: list[str] = []
    sleeps: list[float] = []
    client = PoliteHttpClient(_permitted(), transport=_transport({"2026-10-20"}, calls), sleep=sleeps.append)
    with pytest.raises(FetchFailure) as exc:
        client.fetch(FetchRequest(kind="x", url="https://fixture.test/h/1/2026-10-20"))
    assert exc.value.kind == FailureKind.HTTP_5XX and exc.value.attempts == 3
    assert calls.count("/h/1/2026-10-20") == 3


def test_forbidden_is_not_retried(ctx):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403)

    calls = []
    client = PoliteHttpClient(
        _permitted(),
        transport=httpx.MockTransport(lambda r: calls.append(1) or handler(r)),
        sleep=lambda s: None,
    )
    with pytest.raises(FetchFailure) as exc:
        client.fetch(FetchRequest(kind="x", url="https://fixture.test/a"))
    assert exc.value.kind == FailureKind.FORBIDDEN and exc.value.attempts == 1


def test_collect_partial_failure_then_resume(ctx, hall):
    target = CollectTarget(source="fixture", hall_code="H001", source_hall_id="1")
    dates = [date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 20)]
    calls: list[str] = []
    pipeline = IngestionPipeline(ctx)
    config = _permitted()

    client = PoliteHttpClient(config, transport=_transport({"2026-10-19"}, calls), sleep=lambda s: None)
    first = pipeline.collect(FixtureAdapter(config), target, dates, client)
    assert first.status == "partial"
    assert first.counts["raw_stored"] == 2 and first.counts["fetch_failed"] == 1
    with ctx.session() as s:
        missing = s.scalar(select(DataQualityIssue).where(DataQualityIssue.issue_code == "FETCH_FAILED"))
        assert missing.business_date == date(2026, 10, 19)  # 取得不能日の欠損理由

    calls.clear()
    client = PoliteHttpClient(config, transport=_transport(set(), calls), sleep=lambda s: None)
    resumed = pipeline.collect(FixtureAdapter(config), target, dates, client, resume_run_id=first.run_id)
    assert resumed.status == "succeeded"
    assert resumed.counts["skipped_done"] == 2 and resumed.counts["raw_stored"] == 1
    assert [c for c in calls if c != "/robots.txt"] == ["/h/1/2026-10-19"]
    with ctx.session() as s:
        assert s.scalar(select(func.count()).select_from(DailyMachineStat)) == 3
        assert s.scalar(select(func.count()).select_from(RawPayload)) == 3
        run = s.get(CollectionRun, resumed.run_id)
        assert run.resumed_from_id == first.run_id and run.finished_at is not None


def test_pending_raw_recovered_by_reparse(ctx, hall):
    """原本保存後・解析前に落ちたケース: reparse --pending で復旧できる。"""
    from slot_data.adapters.base import FetchResult
    from slot_data.domain.timeutil import utcnow

    pipeline = IngestionPipeline(ctx)
    run_id = pipeline.start_run("fixture", "H001", {}, "test")
    doc = manual_doc([{"machine_number": "301", "fields": {"total_games": 5}}])
    pipeline.persist_raw(
        run_id=run_id,
        source_code="fixture",
        hall_code="H001",
        request_fingerprint="fp",
        result=FetchResult(
            content=json.dumps(doc).encode(), content_type="application/json", fetched_at=utcnow()
        ),
        parser_name="manual_json",
    )
    # ここでプロセスが落ちた想定 -> 復旧
    stats = pipeline.reparse(pending_only=True)
    assert stats["parsed"] == 1 and stats["revised"] == 1
    assert pipeline.reparse(pending_only=True)["targets"] == 0
