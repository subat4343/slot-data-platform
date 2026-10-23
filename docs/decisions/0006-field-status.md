# ADR-0006 値の状態（NULL の理由）の保持

## 状況
仕様 §3.2 は未公開・取得失敗・解析不能・未確定・真のゼロの区別を求めるが、`daily_machine_stats` の数値列だけでは NULL の理由を表せない。

## 決定
- 状態の値は `observed / not_published / fetch_failed / unparseable / unconfirmed / not_applicable` とする。0 は `observed` の真のゼロ。
- 観測: `source_observations.field_values`（正規化名 → 値）と `field_metadata`（正規化名 → 状態・原文・表示ラベル・正規化カテゴリ・単位・出自・定義）。入力に無い指標は `not_published`（implicit）とする。
- 日次: `daily_machine_stats.field_status JSONB`（指標名 → 状態）。出力では `<指標>__status` 列にする。
- パーサーの中間表現 `ParsedField` は「observed なら値必須、それ以外なら値は None」を型で強制する。
- 出自は `displayed / calculated / graph_estimated / manual` で区別する。設定値の推測は観測に入れず、`analysis_results` に置く。
