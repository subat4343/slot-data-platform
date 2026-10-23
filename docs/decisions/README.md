# ADR（Architecture Decision Records）

| # | タイトル | 状態 |
|---|---|---|
| [0001](0001-stack-and-layout.md) | 技術構成とディレクトリ構成 | 採用 |
| [0002](0002-gated-site-adapters-and-manual-import.md) | サイトアダプターの許可ゲートと手動インポート | 採用（Q1 の回答待ち） |
| [0003](0003-uuidv7-app-side.md) | UUIDv7 をアプリ側で生成 | 採用 |
| [0004](0004-raw-store-content-addressed.md) | 原本ストアは内容アドレス方式・gzip・不変 | 採用 |
| [0005](0005-hall-code.md) | 店舗コード列の追加 | 採用 |
| [0006](0006-field-status.md) | 値の状態（NULL の理由）の保持 | 採用 |
| [0007](0007-data-status-transitions.md) | 確定段階の遷移規則 | 採用（仮定: settle_hours=24） |
| [0008](0008-selection-rule-v1.md) | 採用値の選択ルール v1 | 採用（仮定: 優先度） |
| [0009](0009-layout-geometry.md) | 配置の位置関係・角台の算出規則 | 採用（仮定） |
| [0010](0010-append-only-enforcement.md) | 追記専用・不変性の DB での担保 | 採用 |
| [0011](0011-pscube-browser-crawl.md) | P's CUBE はぱちタウン経由・画面ありのブラウザで巡回する | 採用（ユーザー判断 2026-10-23） |
