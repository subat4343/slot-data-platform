# slot-data-platform

店舗・機種・台番号・営業日ごとのスロットデータを、**取得した原本を失わずに**蓄積するデータ基盤です。
原本保存、正規化、日次採用値と改訂履歴、当たり履歴、スランプグラフの全点、営業中スナップショット、台配置の版管理、データ品質、再取得・再解析、CSV/Excel/Parquet 出力を備えています。

> **収集元について（重要）**
> 初期対象の P's CUBE と台データオンラインは、2026-10-21 時点の利用約款・robots.txt で自動取得が認められていません（[docs/source_contracts.md](docs/source_contracts.md)）。
> このため、両サイトのアダプターは許可ゲートで停止させています。現在の取り込み経路は手動インポート（人が記録した JSON/CSV）です。
> 運営者の許可を得て設定に記録すれば、同じパイプラインで自動収集を追加できます。

## クイックスタート

```bash
# 1. PostgreSQL 16（どちらか）
docker compose up -d db                     # Docker がある場合（.env に POSTGRES_PASSWORD）
./scripts/dev-postgres.ps1 setup            # Docker が無い Windows（.devdb/ に展開、localhost:55432）

# 2. アプリ
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env
slot-data init-db

# 3. サンプルで一通り
slot-data hall add H001 "サンプル店" --cutoff 05:00 --closing 22:45
slot-data import-observations examples/observations_H001_2026-10-20.json --source pscube --hall H001
slot-data import-observations examples/observations_H001_2026-10-20.csv --source daidata_online --hall H001
slot-data import-layout examples/layout_H001_2026-10-01.csv --by me      # 差分プレビュー
slot-data publish-layout <import_id> --by me --yes
slot-data export daily --out out/daily.xlsx --format xlsx --hall H001 --with-layout
slot-data issues        # 例: 2 サイトの値の食い違い（SOURCE_MISMATCH）、機種名のレビュー待ち
slot-data audit
```

## コマンド

| コマンド | 内容 |
|---|---|
| `init-db` | マイグレーション適用、取得元登録 |
| `hall add / list / map-source` | 店舗マスター、取得元店舗 ID の対応 |
| `import-observations FILE... --source --hall` | 手動インポート（原本保存 → 解析 → 採用値更新） |
| `collect` / `backfill` / `schedule --once` | 自動収集（許可された取得元のみ）、過去日、定期実行。`--resume <run_id>` で再開 |
| `reparse [--pending] [--raw-id] [--hall]` | 原本から現行パーサー版で再解析（途中失敗の復旧にも使う） |
| `reconcile --hall --from --to` | 採用値の再計算 |
| `mapping list / resolve` | 機種名の対応付けレビュー |
| `correction add / revoke` | 手動訂正（元観測は改変しない） |
| `validate-layout` / `import-layout` / `publish-layout` / `reject-layout` | 配置 CSV/Excel の検証 → プレビュー → 確認 → 公開 |
| `layout-relation` / `layout-show` | 位置関係の手動補正（draft 版）、日付別の配置特徴 |
| `export daily / bonus / slump` | CSV / Excel / Parquet（店舗・日付・機種・台番号で絞り込み） |
| `issues` / `audit` | 品質課題の一覧、原本・改訂の整合監査 |
| `backup --out` / `restore --from` | DB と原本ストアの一貫バックアップ／復元 |

引数の詳細は `slot-data <command> --help` で確認できます。運用手順は [docs/operations.md](docs/operations.md) にあります。

## 設計の要点

- **原本は不変**: 取得したバイト列は gzip で内容アドレス保存（SHA-256）し、DB には URI・ハッシュ・サイズを記録します。原本の保存に失敗したら正規化しません。
- **追記専用**: 原本・観測・当たり系列・グラフ点・改訂は DB トリガーで UPDATE/DELETE を禁止しています。更新するのは最新採用値 `daily_machine_stats` だけで、値が変わったときだけ `daily_stat_revisions` に旧値・新値・理由・根拠を追記します。
- **欠損は 0 にしない**: 未公開・取得失敗・解析不能・未確定・該当なし・真のゼロを区別して保持し、出力にも `<指標>__status` 列で出します。
- **冪等**: 同一内容の再投入は取得イベントだけを記録します。同一原本・同一パーサー版は再解析しません。照合は店舗＋営業日＋台番号の単位で advisory lock を取ります。
- **確定段階**: `provisional → final_candidate → final` を根拠に基づいて遷移させます。後日の訂正は `is_corrected` と改訂履歴に残します。
- **配置は版管理**: 公開済みの版は不変で、期間の重複は排他制約で禁止しています。過去の営業日にはその日に有効だった版を使います。

詳細は以下の文書にあります。

| 文書 | 内容 |
|---|---|
| [docs/spec_review.md](docs/spec_review.md) | 要求仕様の整合性レビューと未決定事項 |
| [docs/plan.md](docs/plan.md) | Phase 0〜6 の計画・進捗・必須テストとの対応 |
| [docs/architecture.md](docs/architecture.md) | データフロー、シーケンス図、更新・改訂・冪等性の仕様 |
| [docs/erd.md](docs/erd.md) / [docs/schema_dictionary.md](docs/schema_dictionary.md) | ER 図 / テーブル定義書（自動生成） |
| [docs/source_contracts.md](docs/source_contracts.md) | 収集元の利用条件、手動インポート形式 |
| [docs/data_quality.md](docs/data_quality.md) | 品質ステータス、理由コード、監査 |
| [docs/operations.md](docs/operations.md) | 環境構築・日常運用・障害対応・バックアップ |
| [docs/decisions/](docs/decisions/README.md) | ADR |

## 開発

```bash
.venv/Scripts/python -m pytest          # 72 テスト（統合テストは SLOT_DATA_TEST_DATABASE_URL の PostgreSQL を使う）
.venv/Scripts/ruff check src tests
alembic check                           # モデルとマイグレーションの差分確認
```

Python 3.12 以上、PostgreSQL 16 以上（`btree_gist` 拡張を使用）が必要です。
