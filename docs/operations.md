# 運用手順

## 1. 環境構築

### 1.1 PostgreSQL 16

どちらかを使う。

**Docker がある場合**

```bash
docker compose up -d db          # localhost:55432, user postgres / password は .env の POSTGRES_PASSWORD
```

**Docker が無い Windows 開発機**（インストーラ・管理者権限が不要）

```powershell
./scripts/dev-postgres.ps1 setup   # EDB 配布の PostgreSQL 16 バイナリを .devdb/ に展開し initdb
./scripts/dev-postgres.ps1 start   # localhost:55432 で起動（trust 認証、開発専用）
./scripts/dev-postgres.ps1 stop
```

### 1.2 アプリケーション

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Linux/macOS は .venv/bin/python
cp .env.example .env                                  # 接続先などを編集
cp config/app.example.yaml config/app.yaml            # 対象店舗・優先度などを編集
slot-data init-db                                     # マイグレーション適用＋取得元登録
```

### 1.3 環境変数

| 変数 | 既定値 | 内容 |
|---|---|---|
| `SLOT_DATA_DATABASE_URL` | `postgresql+psycopg://postgres@localhost:55432/slot_data` | 接続先（パスワードはここか秘密管理で渡す） |
| `SLOT_DATA_RAW_STORE_ROOT` | `var/rawstore` | 原本ストアのルート |
| `SLOT_DATA_CONFIG_PATH` | `config/app.yaml`（無ければ example） | 運用設定 YAML |
| `SLOT_DATA_LOG_LEVEL` | `INFO` | ログレベル |
| `SLOT_DATA_LOG_JSON` | `true` | 構造化（JSON）ログ。`--text-log` で人間向け |
| `SLOT_DATA_PG_BIN_DIR` | なし | pg_dump / pg_restore の場所（PATH に無い場合） |
| `SLOT_DATA_TEST_DATABASE_URL` | `...:55432/slot_data_test` | テスト用 DB（テストは毎回スキーマを作り直す） |

## 2. 日常運用

### 2.1 店舗の登録

```bash
slot-data hall add H001 "店舗名" --cutoff 05:00 --closing 22:45
slot-data hall map-source H001 pscube <取得元の店舗ID>    # 自動取得が許可された場合に使う
```

### 2.2 データの取り込み（手動インポート）

```bash
slot-data import-observations data/2026-10-20_H001.json --source pscube --hall H001
slot-data import-observations data/*.csv --source daidata_online --hall H001
```

`--source` は値をどのサイトで見たかを表す。同じファイルを再投入しても二重には取り込まない。形式は `source_contracts.md` §4 を参照。

### 2.3 機種名のレビュー

未知の機種名は自動作成せず、レビュー待ちにする。

```bash
slot-data mapping list --pending
slot-data mapping resolve <mapping_id> --create-model "正式機種名"   # 新規機種
slot-data mapping resolve <mapping_id> --model-id <既存機種ID>       # 既存機種に対応付け
slot-data reconcile --hall H001 --from 2026-10-01 --to 2026-10-20    # 反映
```

### 2.4 配置の登録・更新

```bash
slot-data validate-layout layouts/H001_2026-10-21.csv           # 検証のみ
slot-data import-layout layouts/H001_2026-10-21.csv --by 担当者  # draft 作成＋差分プレビュー
slot-data layout-relation <draft_version_id> 303 401 --relation across_aisle --notes "通路を挟んで対面"
slot-data publish-layout <import_id> --by 確認者 --yes           # 公開（前版の valid_to を確定）
slot-data reject-layout <import_id>                              # 取り下げ
slot-data layout-show H001 --date 2026-10-20                     # その日の配置特徴
```

- 機種の入れ替えだけなら配置版は作らない（設置期間 `machine_installations` が自動で区切られる）。
- 台移動・台番号変更・島の再編では、新しい `layout_valid_from` の CSV を作って新版を公開する。
- 公開済みの版は変更できない。誤りがあれば次の版で直す。

### 2.5 手動訂正

```bash
slot-data correction add --hall H001 --date 2026-10-20 --machine 301 --field bb_count --value 3 --reason "店内掲示" --by 担当者
slot-data correction revoke <correction_id> --by 担当者
```

### 2.6 出力

```bash
slot-data export daily --out out/daily.xlsx --format xlsx --hall H001 --from 2026-10-01 --to 2026-10-31 --with-layout
slot-data export bonus --out out/bonus.parquet --format parquet --hall H001
slot-data export slump --out out/slump.parquet --format parquet --machine 301 --machine 302
```

`--model` は機種名の部分一致で絞り込む。欠損は空欄のまま出力し、`<指標>__status` 列に理由を、`source_code` 列に出典を出す。

### 2.7 品質の確認

```bash
slot-data issues                    # 未解決の品質課題
slot-data issues --code SOURCE_MISMATCH
slot-data audit                     # 原本の参照切れ・ハッシュ・孤立・未解析・改訂番号
```

## 3. 自動収集とスケジューリング

自動収集は、取得元の `access_policy.automated_fetch` が `permitted` で、`permission_reference` が記録されている場合だけ動く。2026-10-21 時点で、P's CUBE と台データオンラインはどちらも `prohibited`（`source_contracts.md`）。`collect` を実行してもリクエストは送らず、終了コード 2 で終わる。

```bash
slot-data collect --source <code> --hall H001 --date 2026-10-20
slot-data backfill --source <code> --hall H001 --from 2026-09-01 --to 2026-09-30
slot-data collect --source <code> --hall H001 --from ... --to ... --resume <run_id>   # 途中失敗からの再開
slot-data schedule --once            # targets を 1 周（cron / タスクスケジューラから呼ぶ）
```

- レート上限（`rate_limit.min_interval_seconds`）・再試行（`retry.*`）・タイムアウトは取得元 YAML で設定する。
- 5xx・タイムアウト・ネットワーク障害は指数バックオフ（ジッター付き）で再試行する。401/403/429・robots の Disallow では再試行せずに止める。
- Windows タスクスケジューラ例: 操作 `D:\path\.venv\Scripts\slot-data.exe`、引数 `schedule --once`、開始 `D:\path`。
- cron 例: `15 * * * * cd /srv/slot-data && .venv/bin/slot-data schedule --once >> var/log/schedule.log 2>&1`

## 4. 障害対応

| 症状 | 対応 |
|---|---|
| 収集ジョブが partial / failed | `slot-data issues --code FETCH_FAILED` で欠損理由を確認し、`collect --resume <run_id>` |
| 原本は保存されたが解析されていない | `slot-data reparse --pending` |
| パーサーを修正した | パーサーの `version` を上げてから `slot-data reparse --hall H001`（旧版の観測も残る） |
| 採用ルール・優先度・訂正を変えた | `slot-data reconcile --hall H001 --from ... --to ...` |
| 監査で RAW_MISSING / RAW_HASH_MISMATCH | バックアップから該当ファイルを戻す（ファイル名 = SHA-256 なので照合できる） |
| 追記専用テーブルの行を法令・利用条件の都合で削除する必要がある | 保守手順として `SET slot_data.allow_destructive = 'on'` を同一トランザクションで実行してから削除し、作業記録を残す |

## 5. バックアップと復元

```bash
slot-data backup --out backups/2026-10-21                     # pg_dump → 原本コピー → manifest.json
slot-data restore --from backups/2026-10-21 --database-url postgresql+psycopg://postgres@host:5432/slot_data_restore
```

- 順序は pg_dump が先、原本コピーが後。原本は DB 登録より先に書かれ、以後変更されないので、ダンプが参照する原本は必ずコピーに含まれる。
- 復元先は空の DB にする（`createdb` で作成）。原本はストアに無いものだけコピーし、manifest の SHA-256 で全件を検証する。
- 復元試験はテスト `tests/integration/test_ops.py::test_backup_and_restore_roundtrip` が自動で行う。本番では月 1 回、別 DB への復元＋`audit` を推奨する。

## 6. 開発

```bash
.venv/Scripts/python -m pytest              # 全テスト（PostgreSQL が無ければ統合テストは skip）
.venv/Scripts/python -m pytest tests/unit   # DB 不要の単体テスト
.venv/Scripts/ruff check src tests && .venv/Scripts/ruff format src tests
alembic revision --autogenerate -m "..."    # モデル変更後。生成物を必ず確認し、トリガー等は手で追記
alembic check                               # モデルと DB の差分が無いことの確認
python scripts/gen_schema_dictionary.py > docs/schema_dictionary.md
```
