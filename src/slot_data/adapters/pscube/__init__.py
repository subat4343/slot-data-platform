"""P's CUBE アダプター。

2026-10-21 時点の調査（docs/source_contracts.md）:
- 約款 第5条(1)「当社から明示的な許可を得ることなく、自動化された手段で本サイト上の情報を取得すること」
- 約款 第5条(2)「ウェブページのHTMLデータを取得して…特定のトピックに関わるデータを抽出、整形しなおすこと」
が禁止事項のため、自動取得と HTML 解析は実装していない。運営者の明示的許可を得て設定に
``access_policy.automated_fetch: permitted`` と ``permission_reference`` を記録した場合のみ、
許可された方式（提供 API・データ提供契約等）に合わせて plan/fetch/parser を実装する。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from slot_data.adapters.base import (
    FetchRequest,
    FetchResult,
    Parser,
    SourceAdapter,
    SourceNotImplementedError,
)
from slot_data.config import CollectTarget


class PsCubeAdapter(SourceAdapter):
    source_code = "pscube"
    adapter_version = "0.1.0-gated"

    def plan(self, target: CollectTarget, business_dates: list[date]) -> list[FetchRequest]:
        self.ensure_automated_fetch_allowed()
        raise SourceNotImplementedError(
            "P's CUBE: 許可された取得方式・エンドポイントが未確定のため未実装です（docs/source_contracts.md）"
        )

    def fetch(self, request: FetchRequest, client: Any) -> FetchResult:
        self.ensure_automated_fetch_allowed()
        raise SourceNotImplementedError("P's CUBE: fetch は未実装です")

    def parser_for(self, request_kind: str, content_type: str) -> Parser:
        raise SourceNotImplementedError(
            "P's CUBE: サイト形式のパーサーは未実装です。手動インポート形式を使ってください"
        )
