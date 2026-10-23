"""監査: 原本参照切れ・ハッシュ不一致・孤立ファイル・未解析原本・改訂番号の整合を検査する。

結果は data_quality_issues に記録し（同一内容は重複起票しない）、集計を返す。
原本保存と DB 登録の間の不整合（ファイルはあるが行が無い / 行はあるが解析されていない）を検出し、
後者は ``reparse --pending`` で復旧できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from slot_data.domain.enums import ParseStatus
from slot_data.models import DailyMachineStat, DailyStatRevision, ParseRun, RawPayload
from slot_data.quality.issues import record_issue
from slot_data.services import AppContext


@dataclass
class AuditReport:
    raw_checked: int = 0
    raw_missing: list[str] = field(default_factory=list)
    raw_hash_mismatch: list[str] = field(default_factory=list)
    orphan_files: list[str] = field(default_factory=list)
    unparsed_raw: list[str] = field(default_factory=list)
    revision_mismatch: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.raw_missing or self.raw_hash_mismatch or self.revision_mismatch)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "raw_checked": self.raw_checked,
            "raw_missing": len(self.raw_missing),
            "raw_hash_mismatch": len(self.raw_hash_mismatch),
            "orphan_files": len(self.orphan_files),
            "unparsed_raw": len(self.unparsed_raw),
            "revision_mismatch": len(self.revision_mismatch),
        }


def run_audit(ctx: AppContext, verify_hash: bool = True) -> AuditReport:
    report = AuditReport()
    with ctx.session() as s:
        objects = s.execute(select(RawPayload.storage_uri, RawPayload.sha256).distinct()).all()
        referenced = set()
        for uri, digest in objects:
            report.raw_checked += 1
            referenced.add(uri)
            if not ctx.raw_store.exists(uri):
                report.raw_missing.append(uri)
                record_issue(s, "RAW_MISSING", entity_type="raw_object", dedup_key=uri, details={"uri": uri})
            elif verify_hash and not ctx.raw_store.verify(uri, digest):
                report.raw_hash_mismatch.append(uri)
                record_issue(
                    s, "RAW_HASH_MISMATCH", entity_type="raw_object", dedup_key=uri, details={"uri": uri}
                )

        for uri in ctx.raw_store.iter_uris():
            if uri not in referenced:
                report.orphan_files.append(uri)
                record_issue(
                    s, "RAW_ORPHAN_FILE", entity_type="raw_object", dedup_key=uri, details={"uri": uri}
                )

        succeeded = select(ParseRun.raw_payload_id).where(ParseRun.status == ParseStatus.SUCCEEDED)
        attempted = select(ParseRun.raw_payload_id)
        unparsed = s.scalars(
            select(RawPayload.id).where(
                RawPayload.duplicate_of_id.is_(None),
                RawPayload.id.not_in(succeeded),
                RawPayload.id.not_in(attempted),
            )
        ).all()
        for raw_id in unparsed:
            report.unparsed_raw.append(str(raw_id))
            record_issue(
                s, "RAW_UNPARSED", entity_type="raw_payload", entity_id=raw_id, dedup_key=str(raw_id)
            )

        rows = s.execute(
            select(
                DailyMachineStat.id,
                DailyMachineStat.revision_number,
                func.max(DailyStatRevision.revision_number),
            )
            .outerjoin(DailyStatRevision, DailyStatRevision.daily_stat_id == DailyMachineStat.id)
            .group_by(DailyMachineStat.id, DailyMachineStat.revision_number)
        ).all()
        for stat_id, current, latest in rows:
            if latest != current:
                report.revision_mismatch.append(str(stat_id))
    return report
