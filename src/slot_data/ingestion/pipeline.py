"""収集パイプライン: fetch -> raw persist -> parse -> validate -> normalize -> reconcile -> publish。

トランザクション境界:
1. 原本: ファイル保存（失敗したらそこで止める）-> raw_payloads 行をコミット
2. 解析: parse_runs・観測・系列を 1 トランザクションでコミット（解析失敗時も parse_run とエラーは残す）
3. 照合: 店舗＋営業日＋台番号ごとに 1 トランザクション（advisory lock）
各段階がコミット済みなので、途中で落ちても ``reparse --pending`` / ``collect --resume`` で再開できる。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from slot_data.adapters.base import FetchRequest, FetchResult, ParseContext, Parser, SourceAdapter
from slot_data.config import CollectTarget
from slot_data.domain.enums import CaptureStatus, ParseStatus, RunStatus, Severity
from slot_data.domain.ids import uuid7
from slot_data.domain.timeutil import utcnow
from slot_data.ingestion.http import FetchFailure
from slot_data.ingestion.parser_registry import get_parser
from slot_data.models import CollectionRun, Hall, ParseRun, RawPayload
from slot_data.normalization.persist import ObservationKey, persist_parse_result
from slot_data.observability.logging import get_logger, log_context
from slot_data.quality.issues import record_issue
from slot_data.reconciliation.reconcile import reconcile_key
from slot_data.services import AppContext
from slot_data.services.masters import ensure_source, get_hall

log = get_logger(__name__)


@dataclass
class ParseOutcome:
    parse_run_id: uuid.UUID | None
    status: str
    keys: list[ObservationKey] = field(default_factory=list)
    skipped: str | None = None


@dataclass
class RunSummary:
    run_id: uuid.UUID
    status: str
    counts: dict[str, Any]
    errors: list[dict[str, Any]] = field(default_factory=list)


class IngestionPipeline:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    # ---- collection run -------------------------------------------------
    def start_run(
        self,
        source_code: str,
        hall_code: str | None,
        requested_range: dict[str, Any],
        adapter_version: str,
        resumed_from_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        with self.ctx.session() as s:
            source = ensure_source(s, source_code)
            hall = get_hall(s, hall_code) if hall_code else None
            run = CollectionRun(
                id=uuid7(),
                source_id=source.id,
                hall_id=hall.id if hall else None,
                started_at=utcnow(),
                status=RunStatus.RUNNING,
                requested_range=requested_range,
                adapter_version=adapter_version,
                resumed_from_id=resumed_from_id,
            )
            s.add(run)
            return run.id

    def finish_run(self, run_id: uuid.UUID, counts: dict[str, Any], errors: list[dict[str, Any]]) -> str:
        failures = counts.get("fetch_failed", 0) + counts.get("parse_failed", 0) + counts.get("raw_failed", 0)
        successes = counts.get("raw_stored", 0) + counts.get("raw_duplicate", 0)
        if failures == 0:
            status = RunStatus.SUCCEEDED
        elif successes > 0:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.FAILED
        with self.ctx.session() as s:
            run = s.get(CollectionRun, run_id)
            run.finished_at = utcnow()
            run.status = status
            run.counts = counts
            run.error_summary = (
                "\n".join(f"{e.get('stage')}: {e.get('message')}" for e in errors[:50]) or None
            )
        return status

    # ---- raw persist ------------------------------------------------------
    def persist_raw(
        self,
        *,
        run_id: uuid.UUID,
        source_code: str,
        hall_code: str | None,
        request_fingerprint: str,
        result: FetchResult,
        parser_name: str,
        manual: bool = False,
    ) -> tuple[uuid.UUID, bool]:
        """原本ファイルを保存してから raw_payloads を記録する。戻り値は (raw_id, 解析対象か)。

        ファイル保存に失敗した場合は例外を送出し、DB 登録・正規化には進まない。
        """
        stored = self.ctx.raw_store.put(result.content)
        with self.ctx.session() as s:
            source = ensure_source(s, source_code)
            hall = get_hall(s, hall_code) if hall_code else None
            # 同一内容の並行登録で重複判定が競合しないよう、内容ハッシュ単位で直列化する
            s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"raw:{stored.sha256}"}
            )
            original = s.scalar(
                select(RawPayload)
                .where(
                    RawPayload.sha256 == stored.sha256,
                    RawPayload.source_id == source.id,
                    RawPayload.hall_id == (hall.id if hall else None),
                    RawPayload.duplicate_of_id.is_(None),
                )
                .order_by(RawPayload.fetched_at)
                .limit(1)
            )
            if original is not None:
                capture = CaptureStatus.DUPLICATE_CONTENT
            else:
                capture = CaptureStatus.MANUAL_IMPORT if manual else CaptureStatus.STORED
            raw = RawPayload(
                id=uuid7(),
                collection_run_id=run_id,
                source_id=source.id,
                hall_id=hall.id if hall else None,
                fetched_at=result.fetched_at,
                source_reported_at=result.source_reported_at,
                request_fingerprint=request_fingerprint,
                resource_locator=result.resource_locator,
                content_type=result.content_type,
                storage_uri=stored.uri,
                sha256=stored.sha256,
                byte_size=stored.byte_size,
                response_metadata={**result.response_metadata, "parser": parser_name},
                capture_status=capture,
                duplicate_of_id=original.id if original is not None else None,
            )
            s.add(raw)
            return raw.id, original is None

    # ---- parse + normalize ---------------------------------------------
    def parse_raw(self, raw_id: uuid.UUID, parser: Parser | None = None) -> ParseOutcome:
        with self.ctx.session() as s:
            raw = s.get(RawPayload, raw_id)
            if raw is None:
                raise LookupError(f"raw payload not found: {raw_id}")
            if raw.duplicate_of_id is not None:
                return ParseOutcome(None, "skipped", skipped="duplicate_content")
            parser = parser or get_parser(raw.response_metadata.get("parser", ""))
            exists = s.scalar(
                select(ParseRun.id).where(
                    ParseRun.raw_payload_id == raw.id,
                    ParseRun.parser_name == parser.name,
                    ParseRun.parser_version == parser.version,
                )
            )
            if exists is not None:
                return ParseOutcome(exists, "skipped", skipped="already_parsed")
            hall = s.get(Hall, raw.hall_id) if raw.hall_id else None
            if hall is None:
                raise ValueError(f"raw payload {raw_id} has no hall; cannot parse")
            source_code = raw.response_metadata.get("source_code") or ""
            data = self.ctx.raw_store.get(raw.storage_uri)

        with log_context(raw_id=raw_id, hall=hall.code, stage="parse"), self.ctx.session() as s:
            raw = s.get(RawPayload, raw_id)
            hall = s.get(Hall, raw.hall_id)
            parse_run = ParseRun(
                id=uuid7(),
                raw_payload_id=raw.id,
                parser_name=parser.name,
                parser_version=parser.version,
                started_at=utcnow(),
                status=ParseStatus.RUNNING,
            )
            s.add(parse_run)
            try:
                s.flush()
            except IntegrityError:
                # 並行実行で同じ原本・同じ版の解析が先に登録された
                s.rollback()
                return ParseOutcome(None, "skipped", skipped="already_parsed")
            ctx = ParseContext(
                source_code=source_code,
                hall_code=hall.code,
                timezone=hall.timezone,
                business_day_cutoff=hall.business_day_cutoff,
                fetched_at=raw.fetched_at,
                resource_locator=raw.resource_locator,
                response_metadata=raw.response_metadata,
            )
            try:
                result = parser.parse(data, ctx)
            except Exception as exc:  # パーサーの想定外例外も原本とエラーを残して継続
                log.exception("parser crashed")
                parse_run.status = ParseStatus.FAILED
                parse_run.finished_at = utcnow()
                parse_run.issues = [{"code": "PARSER_EXCEPTION", "severity": "error", "message": repr(exc)}]
                self._record_parse_failure(s, raw, parse_run, repr(exc))
                return ParseOutcome(parse_run.id, ParseStatus.FAILED)

            fatal = [i for i in result.issues if i.severity in (Severity.ERROR, Severity.CRITICAL)]
            failed = bool(fatal) and not result.observations
            keys: list[ObservationKey] = []
            if not failed:
                keys = persist_parse_result(
                    s,
                    raw=raw,
                    parse_run=parse_run,
                    result=result,
                    hall=hall,
                    warn_abs_limits=self.ctx.config.quality.warn_abs_limits,
                )
            for issue in fatal:
                if not failed:
                    record_issue(
                        s,
                        "PARSE_RECORD_REJECTED",
                        entity_type="parse_run",
                        entity_id=parse_run.id,
                        hall_id=hall.id,
                        dedup_key=f"{parse_run.id}:{issue.location}:{issue.code}",
                        details=issue.model_dump(mode="json"),
                    )
            parse_run.status = ParseStatus.FAILED if failed else ParseStatus.SUCCEEDED
            parse_run.finished_at = utcnow()
            parse_run.issues = [i.model_dump(mode="json") for i in result.issues]
            if failed:
                self._record_parse_failure(s, raw, parse_run, "; ".join(i.message for i in fatal))
            log.info(
                "parsed",
                extra={"fields": {"observations": len(result.observations), "status": parse_run.status}},
            )
            return ParseOutcome(parse_run.id, parse_run.status, keys)

    @staticmethod
    def _record_parse_failure(s, raw: RawPayload, parse_run: ParseRun, message: str) -> None:
        record_issue(
            s,
            "PARSE_FAILED",
            entity_type="parse_run",
            entity_id=parse_run.id,
            hall_id=raw.hall_id,
            dedup_key=str(parse_run.id),
            details={"raw_payload_id": str(raw.id), "message": message[:2000]},
        )

    # ---- reconcile ------------------------------------------------------
    def reconcile_keys(self, keys: list[ObservationKey]) -> dict[str, int]:
        stats = {"reconciled": 0, "revised": 0}
        for key in sorted(set(keys), key=lambda k: (str(k.hall_id), k.business_date, k.machine_number)):
            with (
                log_context(
                    business_date=key.business_date, machine_number=key.machine_number, stage="reconcile"
                ),
                self.ctx.session() as s,
            ):
                hall = s.get(Hall, key.hall_id)
                outcome = reconcile_key(s, self.ctx.config, hall, key.business_date, key.machine_number)
                stats["reconciled"] += 1
                stats["revised"] += int(outcome.revised)
        return stats

    # ---- entry points ---------------------------------------------------
    def import_file(self, source_code: str, hall_code: str, path: Path) -> RunSummary:
        """手動インポート: ファイルを原本として保存し、標準パイプラインで取り込む。"""
        from slot_data.adapters.manual import parser_for_file

        parser, content_type = parser_for_file(path.name)
        content = path.read_bytes()
        run_id = self.start_run(
            source_code,
            hall_code,
            {"kind": "manual_import", "file": path.name},
            f"manual/{parser.name}@{parser.version}",
        )
        counts: dict[str, Any] = {
            "raw_stored": 0,
            "raw_duplicate": 0,
            "parse_failed": 0,
            "observations_keys": 0,
        }
        errors: list[dict[str, Any]] = []
        with log_context(job_id=run_id, source=source_code, hall=hall_code):
            raw_id, parse_needed = self.persist_raw(
                run_id=run_id,
                source_code=source_code,
                hall_code=hall_code,
                request_fingerprint=f"manual:{path.name}",
                result=FetchResult(
                    content=content,
                    content_type=content_type,
                    fetched_at=utcnow(),
                    resource_locator=f"file:{path.name}",
                    response_metadata={"source_code": source_code, "original_filename": path.name},
                ),
                parser_name=parser.name,
                manual=True,
            )
            counts["raw_stored" if parse_needed else "raw_duplicate"] += 1
            counts["raw_payload_id"] = str(raw_id)
            if parse_needed:
                outcome = self.parse_raw(raw_id, parser)
                if outcome.status == ParseStatus.FAILED:
                    counts["parse_failed"] += 1
                    errors.append({"stage": "parse", "message": f"raw {raw_id} failed to parse"})
                counts["observations_keys"] = len(outcome.keys)
                counts.update(self.reconcile_keys(outcome.keys))
            status = self.finish_run(run_id, counts, errors)
        return RunSummary(run_id, status, counts, errors)

    def collect(
        self,
        adapter: SourceAdapter,
        target: CollectTarget,
        business_dates: list[date],
        client: Any,
        resume_run_id: uuid.UUID | None = None,
    ) -> RunSummary:
        """自動収集。アダプターの許可ゲートを最初に確認する（禁止されていれば何も取得しない）。"""
        adapter.ensure_automated_fetch_allowed()
        requests = adapter.plan(target, business_dates)
        done = self._completed_fingerprints(resume_run_id) if resume_run_id else set()
        run_id = self.start_run(
            adapter.source_code,
            target.hall_code,
            {
                "kind": "collect",
                "dates": [d.isoformat() for d in business_dates],
                "machines": target.machines,
            },
            adapter.adapter_version,
            resumed_from_id=resume_run_id,
        )
        counts: dict[str, Any] = {
            "planned": len(requests),
            "skipped_done": 0,
            "raw_stored": 0,
            "raw_duplicate": 0,
            "fetch_failed": 0,
            "raw_failed": 0,
            "parse_failed": 0,
        }
        errors: list[dict[str, Any]] = []
        keys: list[ObservationKey] = []
        with log_context(job_id=run_id, source=adapter.source_code, hall=target.hall_code):
            for request in requests:
                fingerprint = request.fingerprint()
                if fingerprint in done:
                    counts["skipped_done"] += 1
                    continue
                keys.extend(
                    self._collect_one(run_id, adapter, target, request, fingerprint, client, counts, errors)
                )
            counts.update(self.reconcile_keys(keys))
            status = self.finish_run(run_id, counts, errors)
        return RunSummary(run_id, status, counts, errors)

    def _collect_one(
        self, run_id, adapter, target, request: FetchRequest, fingerprint, client, counts, errors
    ):
        with log_context(
            business_date=request.business_date, machine_number=request.machine_number, stage="fetch"
        ):
            try:
                result = adapter.fetch(request, client)
            except FetchFailure as exc:
                counts["fetch_failed"] += 1
                errors.append(
                    {"stage": "fetch", "fingerprint": fingerprint, "kind": exc.kind, "message": str(exc)}
                )
                self._record_fetch_failure(run_id, target, request, fingerprint, str(exc), exc.kind)
                log.warning("fetch failed: %s", exc)
                return []
            parser = adapter.parser_for(request.kind, result.content_type)
            result.response_metadata.setdefault("source_code", adapter.source_code)
            try:
                raw_id, parse_needed = self.persist_raw(
                    run_id=run_id,
                    source_code=adapter.source_code,
                    hall_code=target.hall_code,
                    request_fingerprint=fingerprint,
                    result=result,
                    parser_name=parser.name,
                )
            except Exception as exc:
                counts["raw_failed"] += 1
                errors.append({"stage": "raw_persist", "fingerprint": fingerprint, "message": repr(exc)})
                log.exception("raw persist failed; normalization skipped")
                return []
            counts["raw_stored" if parse_needed else "raw_duplicate"] += 1
            if not parse_needed:
                return []
            outcome = self.parse_raw(raw_id, parser)
            if outcome.status == ParseStatus.FAILED:
                counts["parse_failed"] += 1
                errors.append(
                    {"stage": "parse", "fingerprint": fingerprint, "message": f"raw {raw_id} failed"}
                )
            return outcome.keys

    def _record_fetch_failure(
        self, run_id, target, request: FetchRequest, fingerprint, message, kind
    ) -> None:
        with self.ctx.session() as s:
            hall = get_hall(s, target.hall_code)
            record_issue(
                s,
                "FETCH_FAILED",
                entity_type="collection_run",
                entity_id=run_id,
                hall_id=hall.id,
                business_date=request.business_date,
                machine_number=request.machine_number,
                dedup_key=f"{run_id}:{fingerprint}",
                details={"kind": str(kind), "message": message, "url": request.url},
            )

    def _completed_fingerprints(self, run_id: uuid.UUID) -> set[str]:
        """再開元ジョブ（とその再開元の連鎖）で原本保存まで完了した取得要求。"""
        done: set[str] = set()
        with self.ctx.session() as s:
            current: uuid.UUID | None = run_id
            while current is not None:
                done.update(
                    s.scalars(
                        select(RawPayload.request_fingerprint).where(RawPayload.collection_run_id == current)
                    )
                )
                current = s.scalar(select(CollectionRun.resumed_from_id).where(CollectionRun.id == current))
        return done

    # ---- reparse / recovery --------------------------------------------
    def reparse(
        self,
        raw_ids: list[uuid.UUID] | None = None,
        *,
        pending_only: bool = False,
        hall_code: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, int]:
        """原本から再解析する。同一原本・同一パーサー版は再解析しない（冪等）。

        pending_only: 一度も成功解析されていない原本（途中失敗からの復旧）だけを対象にする。
        """
        with self.ctx.session() as s:
            stmt = select(RawPayload.id, RawPayload.fetched_at).where(RawPayload.duplicate_of_id.is_(None))
            if raw_ids:
                stmt = stmt.where(RawPayload.id.in_(raw_ids))
            if hall_code:
                stmt = stmt.where(RawPayload.hall_id == get_hall(s, hall_code).id)
            if pending_only:
                succeeded = select(ParseRun.raw_payload_id).where(ParseRun.status == ParseStatus.SUCCEEDED)
                stmt = stmt.where(RawPayload.id.not_in(succeeded))
            targets = [row.id for row in s.execute(stmt.order_by(RawPayload.fetched_at))]
        stats = {"targets": len(targets), "parsed": 0, "skipped": 0, "failed": 0}
        keys: list[ObservationKey] = []
        for raw_id in targets:
            outcome = self.parse_raw(raw_id)
            if outcome.skipped:
                stats["skipped"] += 1
            elif outcome.status == ParseStatus.FAILED:
                stats["failed"] += 1
            else:
                stats["parsed"] += 1
            keys.extend(
                k
                for k in outcome.keys
                if (date_from is None or k.business_date >= date_from)
                and (date_to is None or k.business_date <= date_to)
            )
        stats.update(self.reconcile_keys(keys))
        return stats

    def reconcile_range(self, hall_code: str, date_from: date, date_to: date) -> dict[str, int]:
        from slot_data.models import SourceObservation

        with self.ctx.session() as s:
            hall = get_hall(s, hall_code)
            rows = s.execute(
                select(SourceObservation.business_date, SourceObservation.machine_number)
                .where(
                    SourceObservation.hall_id == hall.id,
                    SourceObservation.business_date >= date_from,
                    SourceObservation.business_date <= date_to,
                )
                .distinct()
            ).all()
            keys = [ObservationKey(hall.id, r.business_date, r.machine_number) for r in rows]
        return self.reconcile_keys(keys)
