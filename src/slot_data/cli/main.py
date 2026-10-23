"""コマンドラインインターフェース。``slot-data --help`` で一覧、各コマンドの ``--help`` で引数を表示する。"""

from __future__ import annotations

import json
import time as time_mod
import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from slot_data.config import get_settings
from slot_data.observability.logging import configure_logging, get_logger

app = typer.Typer(
    help="スロットデータ収集・蓄積システム", no_args_is_help=True, pretty_exceptions_show_locals=False
)
hall_app = typer.Typer(help="店舗マスター", no_args_is_help=True)
mapping_app = typer.Typer(help="取得元 ID・機種名の対応（レビュー）", no_args_is_help=True)
correction_app = typer.Typer(help="手動訂正（元観測は改変しない）", no_args_is_help=True)
app.add_typer(hall_app, name="hall")
app.add_typer(mapping_app, name="mapping")
app.add_typer(correction_app, name="correction")

log = get_logger("cli")


def _ctx():
    from slot_data.services import build_context

    return build_context()


def _echo(data) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _dates(day: date | None, date_from: date | None, date_to: date | None) -> list[date]:
    if day:
        return [day]
    if not (date_from and date_to):
        raise typer.BadParameter("--date または --from/--to を指定してください")
    if date_to < date_from:
        raise typer.BadParameter("--to は --from 以降にしてください")
    return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]


@app.callback()
def main(
    log_level: Annotated[str, typer.Option(help="ログレベル")] = "",
    text_log: Annotated[bool, typer.Option("--text-log", help="JSON ではなく人間向けのログ")] = False,
) -> None:
    settings = get_settings()
    configure_logging(log_level or settings.log_level, json_format=settings.log_json and not text_log)


# ---- DB -------------------------------------------------------------------
@app.command("init-db")
def init_db() -> None:
    """マイグレーションを最新まで適用し、設定ファイルの取得元を登録する。"""
    from alembic.config import Config

    from alembic import command
    from slot_data.services.masters import ensure_source

    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
    ctx = _ctx()
    with ctx.session() as s:
        for source in ctx.config.sources:
            ensure_source(s, source.code, source.name)
        ensure_source(s, "manual", "手動入力")
    typer.echo("database is up to date")


# ---- masters --------------------------------------------------------------
@hall_app.command("add")
def hall_add(
    code: str,
    name: str,
    address: Annotated[str | None, typer.Option()] = None,
    cutoff: Annotated[str | None, typer.Option(help="営業日境界 HH:MM（例 05:00）")] = None,
    closing: Annotated[str | None, typer.Option(help="閉店時刻 HH:MM（例 22:45）")] = None,
    timezone: Annotated[str, typer.Option()] = "Asia/Tokyo",
) -> None:
    from slot_data.services.masters import create_hall

    with _ctx().session() as s:
        hall = create_hall(
            s,
            code,
            name,
            address,
            timezone,
            time.fromisoformat(cutoff) if cutoff else None,
            time.fromisoformat(closing) if closing else None,
        )
        typer.echo(f"hall {hall.code} registered ({hall.id})")


@hall_app.command("list")
def hall_list() -> None:
    from slot_data.models import Hall

    with _ctx().session() as s:
        _echo(
            [
                {
                    "code": h.code,
                    "name": h.name,
                    "cutoff": h.business_day_cutoff,
                    "closing": h.closing_time,
                    "tz": h.timezone,
                }
                for h in s.scalars(select(Hall).order_by(Hall.code))
            ]
        )


@hall_app.command("map-source")
def hall_map_source(
    hall_code: str, source: str, external_id: str, external_name: Annotated[str | None, typer.Option()] = None
) -> None:
    """取得元の店舗 ID を内部店舗に対応付ける。"""
    from slot_data.services.masters import ensure_source, get_hall, map_hall

    with _ctx().session() as s:
        map_hall(s, ensure_source(s, source), external_id, get_hall(s, hall_code), external_name)
    typer.echo("mapped")


@mapping_app.command("list")
def mapping_list(
    pending: Annotated[bool, typer.Option("--pending", help="レビュー待ちのみ")] = False,
) -> None:
    from slot_data.models import Source, SourceMapping

    with _ctx().session() as s:
        stmt = select(SourceMapping, Source.code).join(Source, Source.id == SourceMapping.source_id)
        if pending:
            stmt = stmt.where(SourceMapping.review_status == "pending")
        _echo(
            [
                {
                    "id": m.id,
                    "source": code,
                    "entity_type": m.entity_type,
                    "external_id": m.external_id,
                    "external_name": m.external_name,
                    "internal_entity_id": m.internal_entity_id,
                    "review_status": m.review_status,
                }
                for m, code in s.execute(stmt.order_by(SourceMapping.created_at))
            ]
        )


@mapping_app.command("resolve")
def mapping_resolve(
    mapping_id: uuid.UUID,
    model_id: Annotated[uuid.UUID | None, typer.Option(help="既存機種に対応付ける")] = None,
    create_model: Annotated[str | None, typer.Option(help="新しい機種を作成して対応付ける（正式名）")] = None,
) -> None:
    """レビュー待ちの対応を確定する。反映には対象期間の `slot-data reconcile` を実行する。"""
    from slot_data.services.masters import resolve_pending_mapping

    with _ctx().session() as s:
        m = resolve_pending_mapping(s, mapping_id, model_id, create_model)
        typer.echo(f"mapping {m.id} -> {m.internal_entity_id} (confirmed)")


# ---- ingestion ------------------------------------------------------------
@app.command("import-observations")
def import_observations(
    files: Annotated[
        list[Path], typer.Argument(exists=True, dir_okay=False, help="manual-v1 JSON または CSV")
    ],
    source: Annotated[str, typer.Option(help="データの取得元（pscube / daidata_online / manual 等）")],
    hall: Annotated[str, typer.Option(help="店舗コード")],
) -> None:
    """人が記録したファイルを原本として保存し、取り込む（自動取得できない取得元の代替経路）。"""
    from slot_data.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(_ctx())
    results = []
    for path in files:
        summary = pipeline.import_file(source, hall, path)
        results.append(
            {"file": str(path), "run_id": summary.run_id, "status": summary.status, "counts": summary.counts}
        )
    _echo(results)
    if any(r["status"] == "failed" for r in results):
        raise typer.Exit(1)


@app.command()
def collect(
    source: Annotated[str, typer.Option()],
    hall: Annotated[str, typer.Option()],
    day: Annotated[str | None, typer.Option("--date")] = None,
    date_from: Annotated[str | None, typer.Option("--from")] = None,
    date_to: Annotated[str | None, typer.Option("--to")] = None,
    resume: Annotated[uuid.UUID | None, typer.Option(help="途中で止まった収集ジョブ ID から再開")] = None,
) -> None:
    """自動収集。取得元の access_policy が permitted でなければ何も取得せず終了する。"""
    from slot_data.adapters import get_adapter
    from slot_data.adapters.base import AccessProhibitedError, SourceNotImplementedError
    from slot_data.ingestion.http import PoliteHttpClient
    from slot_data.ingestion.pipeline import IngestionPipeline

    ctx = _ctx()
    config = ctx.config.source(source)
    if config is None:
        raise typer.BadParameter(f"source {source} is not configured")
    target = next((t for t in ctx.config.targets if t.source == source and t.hall_code == hall), None)
    if target is None:
        raise typer.BadParameter(f"no collect target for {source}/{hall} in config")
    dates = _dates(
        date.fromisoformat(day) if day else None,
        date.fromisoformat(date_from) if date_from else None,
        date.fromisoformat(date_to) if date_to else None,
    )
    adapter = get_adapter(config)
    client = PoliteHttpClient(config)
    try:
        summary = IngestionPipeline(ctx).collect(adapter, target, dates, client, resume_run_id=resume)
    except (AccessProhibitedError, SourceNotImplementedError) as exc:
        typer.echo(f"収集を中止しました: {exc}", err=True)
        raise typer.Exit(2) from exc
    finally:
        client.close()
    _echo(
        {
            "run_id": summary.run_id,
            "status": summary.status,
            "counts": summary.counts,
            "errors": summary.errors,
        }
    )


@app.command()
def backfill(
    source: Annotated[str, typer.Option()],
    hall: Annotated[str, typer.Option()],
    date_from: Annotated[str, typer.Option("--from")],
    date_to: Annotated[str, typer.Option("--to")],
) -> None:
    """過去日の収集（公開範囲・権限内のみ）。取得不能日は FETCH_FAILED として欠損理由を残す。"""
    collect(source=source, hall=hall, day=None, date_from=date_from, date_to=date_to, resume=None)


@app.command()
def reparse(
    raw_id: Annotated[
        list[uuid.UUID] | None, typer.Option("--raw-id", help="対象の原本 ID（複数可）")
    ] = None,
    pending: Annotated[
        bool, typer.Option("--pending", help="未解析・解析失敗の原本だけ（途中失敗からの復旧）")
    ] = False,
    hall: Annotated[str | None, typer.Option()] = None,
) -> None:
    """原本から現行パーサー版で再解析する（同じ版で解析済みならスキップ）。"""
    from slot_data.ingestion.pipeline import IngestionPipeline

    _echo(IngestionPipeline(_ctx()).reparse(raw_id or None, pending_only=pending, hall_code=hall))


@app.command()
def reconcile(
    hall: Annotated[str, typer.Option()],
    date_from: Annotated[str, typer.Option("--from")],
    date_to: Annotated[str, typer.Option("--to")],
) -> None:
    """採用値を再計算する（機種対応の解決後・手動訂正後・設定変更後など）。"""
    from slot_data.ingestion.pipeline import IngestionPipeline

    _echo(
        IngestionPipeline(_ctx()).reconcile_range(
            hall, date.fromisoformat(date_from), date.fromisoformat(date_to)
        )
    )


@app.command()
def schedule(
    once: Annotated[
        bool, typer.Option("--once", help="1 周だけ実行して終了（cron / タスクスケジューラ向け）")
    ] = False,
    days_back: Annotated[int, typer.Option(help="何日前まで再取得するか")] = 1,
) -> None:
    """設定の targets を schedule_interval_minutes 間隔で収集する。禁止・未実装の取得元はスキップして記録する。"""
    ctx = _ctx()
    while True:
        today = date.today()
        for target in ctx.config.targets:
            try:
                collect(
                    source=target.source,
                    hall=target.hall_code,
                    day=None,
                    date_from=(today - timedelta(days=days_back)).isoformat(),
                    date_to=today.isoformat(),
                    resume=None,
                )
            except typer.Exit as exc:
                log.warning("target skipped: %s/%s (exit %s)", target.source, target.hall_code, exc.exit_code)
        if once:
            return
        time_mod.sleep(ctx.config.schedule_interval_minutes * 60)


# ---- layout ---------------------------------------------------------------
@app.command("validate-layout")
def validate_layout(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """配置 CSV/Excel を検証だけする（DB に書き込まない）。"""
    from slot_data.layout.importer import read_table, validate_rows

    with _ctx().session() as s:
        report = validate_rows(s, read_table(path))
        s.rollback()
    _echo(report.to_json())
    if not report.ok:
        raise typer.Exit(1)


@app.command("import-layout")
def import_layout(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    by: Annotated[str, typer.Option(help="登録者")],
) -> None:
    """配置を検証して draft 版を作り、現行版との差分プレビューを表示する（まだ公開しない）。"""
    from slot_data.layout.importer import stage_import

    with _ctx().session() as s:
        staged = stage_import(s, path, by, file_uri=str(path))
    _echo(
        {
            "import_id": staged.import_id,
            "draft_version_id": staged.draft_version_id,
            "report": staged.report,
            "preview": staged.preview,
        }
    )
    if staged.draft_version_id is None:
        raise typer.Exit(1)
    typer.echo(f"確認後に公開: slot-data publish-layout {staged.import_id} --by {by} --yes", err=True)


@app.command("publish-layout")
def publish_layout(
    import_id: uuid.UUID,
    by: Annotated[str, typer.Option(help="確認者")],
    yes: Annotated[bool, typer.Option("--yes", help="プレビューを確認済み")] = False,
) -> None:
    from slot_data.layout.importer import publish_import

    if not yes:
        typer.echo("プレビューを確認のうえ --yes を付けて実行してください", err=True)
        raise typer.Exit(1)
    with _ctx().session() as s:
        version = publish_import(s, import_id, by)
        typer.echo(f"published layout version {version.version_number} valid_from {version.valid_from}")


@app.command("reject-layout")
def reject_layout(import_id: uuid.UUID) -> None:
    from slot_data.layout.importer import reject_import

    with _ctx().session() as s:
        reject_import(s, import_id)
    typer.echo("rejected")


@app.command("layout-relation")
def layout_relation(
    version_id: uuid.UUID,
    machine_a: str,
    machine_b: str,
    relation: Annotated[str, typer.Option(help="next_to / across_aisle / back_to_back")],
    remove: Annotated[bool, typer.Option("--remove")] = False,
    notes: Annotated[str | None, typer.Option()] = None,
) -> None:
    """draft 配置版の位置関係を手動で追加・削除する（対面 across_aisle は手動登録のみ）。"""
    from slot_data.layout.importer import set_relation

    with _ctx().session() as s:
        set_relation(s, version_id, machine_a, machine_b, relation, remove=remove, notes=notes)
    typer.echo("ok")


@app.command("layout-show")
def layout_show(hall: str, day: Annotated[str, typer.Option("--date")]) -> None:
    """指定営業日に有効な配置から台ごとの特徴（角・隣・背中合わせ・対面）を表示する。"""
    from dataclasses import asdict

    from slot_data.layout.features import machine_features
    from slot_data.services.masters import get_hall

    with _ctx().session() as s:
        features = machine_features(s, get_hall(s, hall).id, date.fromisoformat(day))
    _echo({k: asdict(v) for k, v in features.items()})


# ---- corrections ----------------------------------------------------------
@correction_app.command("add")
def correction_add(
    hall: Annotated[str, typer.Option()],
    day: Annotated[str, typer.Option("--date")],
    machine: Annotated[str, typer.Option()],
    field: Annotated[str, typer.Option(help="指標名（total_games 等）")],
    reason: Annotated[str, typer.Option()],
    by: Annotated[str, typer.Option()],
    value: Annotated[int | None, typer.Option(help="新しい値。省略時は NULL（--status で理由）")] = None,
    status: Annotated[str, typer.Option(help="value 省略時の値状態")] = "not_published",
) -> None:
    from slot_data.domain.metrics import METRIC_NAMES
    from slot_data.ingestion.pipeline import IngestionPipeline
    from slot_data.models import ManualCorrection
    from slot_data.schemas.parsed import normalize_machine_number
    from slot_data.services.masters import get_hall

    if field not in METRIC_NAMES:
        raise typer.BadParameter(f"field must be one of {METRIC_NAMES}")
    ctx = _ctx()
    with ctx.session() as s:
        s.add(
            ManualCorrection(
                hall_id=get_hall(s, hall).id,
                business_date=date.fromisoformat(day),
                machine_number=normalize_machine_number(machine),
                field_name=field,
                new_value=Decimal(value) if value is not None else None,
                field_status="observed" if value is not None else status,
                reason=reason,
                corrected_by=by,
            )
        )
    _echo(IngestionPipeline(ctx).reconcile_range(hall, date.fromisoformat(day), date.fromisoformat(day)))


@correction_app.command("revoke")
def correction_revoke(correction_id: uuid.UUID, by: Annotated[str, typer.Option()]) -> None:
    from slot_data.domain.timeutil import utcnow
    from slot_data.ingestion.pipeline import IngestionPipeline
    from slot_data.models import Hall, ManualCorrection

    ctx = _ctx()
    with ctx.session() as s:
        corr = s.get(ManualCorrection, correction_id)
        corr.revoked_at, corr.revoked_by = utcnow(), by
        hall_code, day = s.get(Hall, corr.hall_id).code, corr.business_date
    _echo(IngestionPipeline(ctx).reconcile_range(hall_code, day, day))


# ---- output / quality / ops ----------------------------------------------
@app.command()
def export(
    kind: Annotated[str, typer.Argument(help="daily / bonus / slump")],
    out: Annotated[Path, typer.Option(help="出力ファイル")],
    fmt: Annotated[str, typer.Option("--format", help="csv / xlsx / parquet")] = "csv",
    hall: Annotated[list[str] | None, typer.Option(help="店舗コード（複数可）")] = None,
    date_from: Annotated[str | None, typer.Option("--from")] = None,
    date_to: Annotated[str | None, typer.Option("--to")] = None,
    model: Annotated[str | None, typer.Option(help="機種名（部分一致）")] = None,
    machine: Annotated[list[str] | None, typer.Option(help="台番号（複数可）")] = None,
    with_layout: Annotated[bool, typer.Option("--with-layout", help="daily に配置特徴列を付ける")] = False,
) -> None:
    from slot_data.exports.export import EXPORTS, ExportFilter, daily_rows, write_rows

    if kind not in EXPORTS:
        raise typer.BadParameter(f"kind must be one of {list(EXPORTS)}")
    f = ExportFilter(
        hall_codes=hall,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
        model_name=model,
        machine_numbers=machine,
    )
    with _ctx().session() as s:
        rows = daily_rows(s, f, with_layout) if kind == "daily" else EXPORTS[kind](s, f)
        count = write_rows(rows, out, fmt)
    typer.echo(f"{count} rows -> {out}")


@app.command()
def audit(
    no_hash: Annotated[bool, typer.Option("--no-hash", help="ハッシュ検証を省略（存在確認のみ）")] = False,
) -> None:
    """原本参照切れ・ハッシュ不一致・孤立ファイル・未解析原本・改訂番号の整合を検査する。"""
    from slot_data.quality.audit import run_audit

    report = run_audit(_ctx(), verify_hash=not no_hash)
    _echo(report.to_json())
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def issues(
    status: Annotated[str, typer.Option()] = "open",
    code: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option()] = 100,
) -> None:
    """品質課題の一覧。"""
    from slot_data.models import DataQualityIssue

    with _ctx().session() as s:
        stmt = select(DataQualityIssue).where(DataQualityIssue.status == status)
        if code:
            stmt = stmt.where(DataQualityIssue.issue_code == code)
        _echo(
            [
                {
                    "id": i.id,
                    "detected_at": i.detected_at,
                    "code": i.issue_code,
                    "severity": i.severity,
                    "business_date": i.business_date,
                    "machine_number": i.machine_number,
                    "details": i.details,
                }
                for i in s.scalars(stmt.order_by(DataQualityIssue.detected_at.desc()).limit(limit))
            ]
        )


@app.command("backup")
def backup_cmd(out: Annotated[Path, typer.Option(help="新規作成するバックアップディレクトリ")]) -> None:
    """PostgreSQL（pg_dump）と原本ストアを一貫してバックアップする。"""
    from slot_data.storage.backup import backup
    from slot_data.storage.raw_store import LocalRawStore

    settings = get_settings()
    result = backup(settings.database_url, LocalRawStore(settings.raw_store_root), out, settings.pg_bin_dir)
    typer.echo(
        f"backup -> {result.directory} (raw files: {result.raw_files}, dump: {result.dump_bytes} bytes)"
    )


@app.command("restore")
def restore_cmd(
    backup_dir: Annotated[Path, typer.Option("--from", exists=True, file_okay=False)],
    database_url: Annotated[str | None, typer.Option(help="復元先（空の DB）。省略時は設定の DB")] = None,
) -> None:
    from slot_data.storage.backup import restore
    from slot_data.storage.raw_store import LocalRawStore

    settings = get_settings()
    _echo(
        restore(
            backup_dir,
            database_url or settings.database_url,
            LocalRawStore(settings.raw_store_root),
            settings.pg_bin_dir,
        )
    )


if __name__ == "__main__":
    app()
