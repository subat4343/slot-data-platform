# スロットデータ収集・蓄積システム 統合開発指示書

## 0. Claude Codeへの指示
あなたはシニアソフトウェアアーキテクト兼Pythonバックエンドエンジニアです。本書を唯一の初期要求仕様として、設計の整合性を検証し、実装計画を作成してから、段階的に動作するシステムを構築してください。未確認のサイト仕様を推測で固定しないこと。重要な不明点は選択肢・推奨案・影響を示し、ブロッカーでない事項は仮定をADRに記録して進めてください。コード・マイグレーション・テスト・README・運用手順を納品してください。

## 1. 目的と対象範囲
初期収集対象はP's CUBEと台データオンライン。店舗、機種、台番号、営業日ごとのスロットデータを収集し、取得可能な履歴を**省略せず保存**する。将来の店舗傾向、曜日・特定日、機種、物理配置（角・並び・島・向かい）、設定推測に使える再現性のあるデータ基盤を構築する。

初期フェーズの必須範囲は、データモデル、原本保存、正規化、日次集計、当たり履歴、スランプ系列全点、スナップショット、台配置履歴、データ品質、再取得・再解析、CSV/Excel出力、CLI、テスト。予測モデル・高度なGUIは後続フェーズ。

サイトへのアクセス手法（HTTP、ブラウザ等）、実際のエンドポイント、認証、HTML/JSON形式、利用可能な過去期間は実装時に調査する。公開APIが存在することや特定項目が取得可能なことを前提にしない。利用規約・robots・アクセス制限・著作権・個人情報保護等を確認し、認証回避、CAPTCHA回避、アクセス制限回避を実装しない。利用不可の場合は収集を止め、手動インポートを代替とする。

## 2. 技術構成
- Python 3.12以上、PostgreSQL 16以上、SQLAlchemy 2、Alembic、Pydantic 2、pytest。
- アダプターはサイト別に分離し、`fetch -> raw persist -> parse -> validate -> normalize -> reconcile -> publish` の段階を明確にする。
- API/UI非依存のドメインサービス。設定は環境変数＋YAML、秘密情報は環境変数/秘密管理に置く。
- UTCの`TIMESTAMPTZ`で保存し、表示・営業日計算はAsia/Tokyo。営業日境界は店舗別に設定可能。
- UUID主キー（UUIDv7を利用できる場合に採用、生成ライブラリ・互換性を確認）。金額・率は`NUMERIC`、ゲーム数・差枚・回数は`INTEGER`または上限を考慮した`BIGINT`、識別子は文字列、構造化メタデータは`JSONB`。
- 生データの原本はファイル/オブジェクトストレージにgzip等で圧縮保存し、DBにはURI・SHA-256・サイズ・メディア型を記録。DBのJSONBは検索用メタデータに限定。
- トランザクション、ユニーク制約、FK、CHECK、索引、必要に応じた範囲排他制約を実装する。

## 3. 収集項目と意味論
### 3.1 必須識別情報
取得元サービス、取得元店舗ID、店舗内部ID、営業日、取得元台番号、正規化台番号、機種名（原文）、機種内部ID（同定できる場合）、取得日時、元データID、パーサーバージョン。

### 3.2 日次実績（取得可能なもの）
総ゲーム数、通常時ゲーム数（区別できる場合）、差枚、BB回数、RB回数、AT回数、CZ回数、初当たり回数、最終ゲーム数、最大差枚、最小差枚、最大獲得枚数、稼働状態、データ時点、集計範囲、値の定義・単位。

**未公開・取得失敗・解析不能・未確定・真のゼロを区別する。** 値が不明なときゼロを補完しない。BB/RB/AT/CZはサイトと機種によって意味が異なるため、表示ラベルと正規化カテゴリを併存させる。サイトの表示値、計算値、グラフ画像からの推定値を区別する。設定値は観測事実として保存せず、根拠付き推定結果として別管理する。

### 3.3 当たり・イベント履歴
取得元の順番、イベント時刻（存在する場合）、当選までのゲーム数、累積ゲーム数、イベントラベル原文、分類、獲得枚数、関連元データID、欠損・推定区分。イベントIDがなければ安定した系列キーを設計するが、訂正で順番が変わることを想定し、観測スナップショット単位の完全履歴を保持する。

### 3.4 スランプグラフ
**取得できる全系列・全座標点を保存**。横軸種別（時刻・累積ゲーム数・ピクセル・不明）、横軸の生値と正規化値、縦軸の生値と差枚換算値、系列順序、単位、サンプリング時点、観測/推定区分、変換手法・バージョン、精度/信頼性、元画像/JSON参照。画像のみなら画像原本を保存し、座標推定は別派生系列として記録する。画素座標を確定差枚として扱わない。グラフが更新されたら新しい系列を追記し、古い系列を上書きしない。欠落・重複点を勝手に補間しない。

### 3.5 補助情報
店舗住所・営業日定義、機種メーカー・型式・種別・公表設定別スペック（出典/適用期間付き）、新台導入・撤去、イベント情報（出典と信頼性）、曜日・祝日、実戦で人間が提供する設定示唆や小役等。取得不可ならNULL。

## 4. 正規化データベース（必須テーブル）
以下の論理テーブルを実装し、物理カラム・型・FK・制約・索引をDDLとテーブル定義書で確定する。

### 4.1 マスター
- `sources`: `id UUID PK`, `code TEXT UNIQUE NOT NULL`, `name TEXT`, `enabled BOOLEAN`, `created_at TIMESTAMPTZ`。
- `halls`: `id UUID PK`, `name TEXT NOT NULL`, `address TEXT NULL`, `timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo'`, `business_day_cutoff TIME NULL`, `created_at`, `updated_at`。
- `machine_models`: `id UUID PK`, `canonical_name TEXT NOT NULL`, `manufacturer TEXT NULL`, `model_type TEXT NULL`, `release_date DATE NULL`, `created_at`。機種名だけを唯一識別子にしない。
- `source_mappings`: `id UUID PK`, `source_id FK`, `entity_type TEXT`, `external_id TEXT`, `internal_entity_id UUID`, `external_name TEXT`, `valid_from DATE NULL`, `valid_to DATE NULL`, `confidence NUMERIC NULL`, `review_status TEXT`, `created_at`。同一店舗/機種のサイト別IDを照合し、曖昧な紐付けは人間が確認する。対象エンティティへの参照整合性は型別テーブル分割または明示的検証で担保。
- `machine_installations`: `id UUID PK`, `hall_id FK`, `machine_number TEXT NOT NULL`, `model_id FK NULL`, `valid_from DATE NOT NULL`, `valid_to DATE NULL`, `source_id FK NULL`, `evidence_raw_id FK NULL`。期間は開始含む・終了含まない。店舗＋台番号で設置期間が重ならないことを保証。機種不明を許す。

### 4.2 収集・原本・解析
- `collection_runs`: `id UUID PK`, `source_id FK`, `hall_id FK NULL`, `started_at`, `finished_at NULL`, `status`, `requested_range JSONB`, `counts JSONB`, `error_summary TEXT NULL`, `adapter_version TEXT`。
- `raw_payloads`: `id UUID PK`, `collection_run_id FK`, `source_id FK`, `hall_id FK NULL`, `fetched_at TIMESTAMPTZ`, `source_reported_at TIMESTAMPTZ NULL`, `request_fingerprint TEXT`, `resource_locator TEXT NULL`, `content_type TEXT`, `storage_uri TEXT NOT NULL`, `sha256 CHAR(64) NOT NULL`, `byte_size BIGINT`, `response_metadata JSONB`, `capture_status TEXT`。同一内容でも取得イベントの存在は保持し、ファイル本体のみハッシュで重複排除可能。原本は変更不可。
- `parse_runs`: `id UUID PK`, `raw_payload_id FK`, `parser_name TEXT`, `parser_version TEXT`, `started_at`, `finished_at NULL`, `status`, `issues JSONB`。同じ原本を異なるパーサーバージョンで再解析可能。
- `source_observations`: `id UUID PK`, `parse_run_id FK`, `source_id FK`, `hall_id FK`, `machine_number TEXT`, `business_date DATE`, `observed_at TIMESTAMPTZ`, `source_reported_at TIMESTAMPTZ NULL`, `field_values JSONB`, `field_metadata JSONB`, `quality_status TEXT`。取得元別の観測値を追記保持。

### 4.3 台別実績
- `daily_machine_stats`: `id UUID PK`, `hall_id FK`, `business_date DATE`, `machine_number TEXT`, `installation_id FK NULL`, `model_id FK NULL`, `total_games BIGINT NULL`, `normal_games BIGINT NULL`, `net_medals BIGINT NULL`, `bb_count INTEGER NULL`, `rb_count INTEGER NULL`, `at_count INTEGER NULL`, `cz_count INTEGER NULL`, `first_hit_count INTEGER NULL`, `last_games BIGINT NULL`, `max_net_medals BIGINT NULL`, `min_net_medals BIGINT NULL`, `max_payout_medals BIGINT NULL`, `data_status TEXT`, `quality_status TEXT`, `selected_observation_id FK NULL`, `revision_number INTEGER`, `updated_at`。`UNIQUE(hall_id,business_date,machine_number)`。回数・ゲーム数はNULLまたは0以上。差枚は負値を許す。最大・最小は定義が確かな場合のみ整合チェック。
- `daily_stat_revisions`: `id UUID PK`, `daily_stat_id FK`, `revision_number INTEGER`, `recorded_at TIMESTAMPTZ`, `previous_values JSONB NULL`, `new_values JSONB`, `reason TEXT`, `source_observation_id FK`, `selection_rule_version TEXT`。追記専用、`UNIQUE(daily_stat_id,revision_number)`。
- `machine_snapshots`: `id UUID PK`, `source_observation_id FK`, `hall_id FK`, `business_date DATE`, `machine_number TEXT`, `captured_at TIMESTAMPTZ`, `metrics JSONB`, `completeness_status TEXT`。営業中の各時点の履歴。元観測と一対一または用途に応じたユニーク制約。
- `bonus_event_series`: `id UUID PK`, `source_observation_id FK`, `hall_id FK`, `business_date DATE`, `machine_number TEXT`, `series_status TEXT`。取得時点のイベント一覧全体を識別。
- `bonus_events`: `id UUID PK`, `series_id FK`, `ordinal INTEGER`, `source_event_id TEXT NULL`, `event_time TIMESTAMPTZ NULL`, `hit_games BIGINT NULL`, `cumulative_games BIGINT NULL`, `raw_label TEXT`, `normalized_type TEXT NULL`, `payout_medals BIGINT NULL`, `raw_attributes JSONB`, `quality_status TEXT`。`UNIQUE(series_id,ordinal)`。同じイベントが複数系列に登場しても各観測系列を保持。
- `slump_series`: `id UUID PK`, `source_observation_id FK`, `hall_id FK`, `business_date DATE`, `machine_number TEXT`, `captured_at TIMESTAMPTZ`, `x_axis_type TEXT`, `x_unit TEXT NULL`, `y_axis_type TEXT`, `y_unit TEXT NULL`, `origin_type TEXT`, `derivation_method TEXT NULL`, `derivation_version TEXT NULL`, `raw_payload_id FK`, `series_metadata JSONB`, `quality_status TEXT`。
- `slump_points`: `id UUID PK`, `series_id FK`, `point_index INTEGER`, `x_raw TEXT NULL`, `y_raw TEXT NULL`, `x_numeric NUMERIC NULL`, `x_timestamp TIMESTAMPTZ NULL`, `y_numeric NUMERIC NULL`, `estimated_net_medals NUMERIC NULL`, `point_metadata JSONB`, `quality_status TEXT`。`UNIQUE(series_id,point_index)`、`point_index>=0`。点数を任意の上限で切らない。

### 4.4 物理配置・人手管理
- `hall_layout_versions`: `id UUID PK`, `hall_id FK`, `version_number INTEGER`, `valid_from DATE NOT NULL`, `valid_to DATE NULL`, `status TEXT`, `created_by TEXT`, `created_at TIMESTAMPTZ`, `source_document_uri TEXT NULL`, `notes TEXT NULL`。期間は半開区間。`UNIQUE(hall_id,version_number)`。公開済み版は不変とし、新版を作る。同一店舗の公開済み期間の重複禁止。
- `hall_islands`: `id UUID PK`, `layout_version_id FK`, `island_code TEXT`, `display_name TEXT`, `geometry JSONB NULL`, `notes TEXT NULL`, `UNIQUE(layout_version_id,island_code)`。
- `machine_positions`: `id UUID PK`, `layout_version_id FK`, `island_id FK`, `machine_number TEXT NOT NULL`, `side TEXT`, `sequence_no INTEGER`, `x NUMERIC NULL`, `y NUMERIC NULL`, `rotation_deg NUMERIC NULL`, `position_kind TEXT DEFAULT 'machine'`, `notes TEXT NULL`。`UNIQUE(layout_version_id,machine_number)`、位置の重複制約は同一島/側/順番で設定。島と配置版の整合性を複合FK等で保証。欠番・通路・空きは別位置種別またはジオメトリで表現。
- `machine_adjacencies`: `id UUID PK`, `layout_version_id FK`, `position_a_id FK`, `position_b_id FK`, `relation_type TEXT`, `is_manual_override BOOLEAN`, `notes TEXT NULL`。自己参照禁止、同じ版に属する位置のみ、無向関係はID順を正規化し重複禁止。`relation_type`は`next_to`, `across_aisle`, `back_to_back`等。隣接と対面を混同しない。
- `layout_imports`: `id UUID PK`, `hall_id FK`, `created_at`, `file_uri`, `file_sha256`, `validation_report JSONB`, `status`, `target_layout_version_id FK NULL`。CSV/Excelの取り込みと検証履歴。

**配置管理ルール:** 人間が初回に店舗・島・側・並び順・台番号をCSV/Excelで登録。更新時は差分プレビュー、確認後に新しい配置版を公開。機種入替だけでは配置版を作らず`machine_installations`を更新。台移動・台番号変更・島再編では新版を作る。過去の営業日には当該日に有効な配置版を使う。角・角2は物理的な列端から算出し、途中の空きや通路を考慮する。連番だけで隣接を推測しない。配置が未登録でも実績収集は継続し、配置依存分析のみ保留する。

### 4.5 分析補助
- `hall_events`: 店舗、営業日、イベント名称・分類、根拠、取得元、確認状態。
- `machine_specs`: 機種、設定値、指標名、分子/分母/値、単位、条件、出典、適用期間。公表値と推定値を分離。
- `analysis_results`: 対象、対象期間、手法・モデルバージョン、入力データバージョン、結果JSONB、計算日時、信頼性・サンプル数。設定推測を確定設定として扱わない。

## 5. 正規化・照合の原則
1. 取得元の原文、単位、定義を残す。名称の自動同定が曖昧ならレビュー待ち。
2. 同じ店舗・営業日・台番号を統合キーとし、複数サイトの値は`source_observations`に併存させる。公開する日次実績の採用ソースは、明示した優先順位・鮮度・完全性・品質・確認状態に基づいて決定し、ルールの版を記録する。異なるサイトの指標を無条件に混ぜない。
3. 値が食い違えば上書きして隠さず、差異を記録し品質レビュー対象とする。
4. 原本・観測系列・改訂履歴は追記専用。`daily_machine_stats`のみトランザクションで最新採用値を更新。
5. 再取得と再解析は冪等にする。取り込みイベントの記録は保持し、同一原本・同一パーサー版の重複解析は一意制約等で防ぐ。
6. `source_reported_at`、`fetched_at`、`observed_at`、`business_date`を混同しない。
7. 当たり履歴とスランプ点はスナップショットごとに全件保持。古い系列の削除・破壊的差分更新は禁止。

## 6. 更新・訂正アルゴリズム
1. 収集ジョブを開始し`collection_runs`を作成。
2. 取得したレスポンスのバイト列を原本ストレージに永続化、SHA-256とメタデータを`raw_payloads`に記録。原本永続化に失敗したら正規化を進めない。
3. パーサー版を記録して解析し、`parse_runs`と`source_observations`を追記。解析不能でも原本とエラーを残す。
4. 数値・日付・単位・参照整合性を検証。致命的な不整合は隔離し、既存の採用値を変更しない。
5. 当たりイベント系列、グラフ系列・全点、途中経過を原本/観測に紐付けて保存。
6. 店舗＋営業日＋台番号に対してDBトランザクション/ロックで競合を制御し、採用ルールで候補を比較。
7. 採用値が変化した場合だけ`daily_stat_revisions`へ旧新値・理由・根拠を追記し、`daily_machine_stats`を更新。変化しなければ不要な改訂を作らない。
8. 閉店後の取得を自動的に確定扱いしない。`provisional -> final_candidate -> final`を根拠に基づいて遷移し、後日訂正を許容する。`corrected`は履歴イベント/フラグとして扱い、確定状態と混同しない。
9. 途中失敗時はジョブ単位で再実行できるようにする。原本保存とDB登録の間の不整合を検出・復旧する仕組みを用意する。
10. 過去日のバックフィルは公開範囲・権限内で実行し、取得不能日は欠損理由を残す。

## 7. 配置CSVの標準仕様
必須列：`hall_code, layout_valid_from, island_code, side, sequence_no, machine_number`。
任意列：`x, y, rotation_deg, position_kind, notes`。
- `sequence_no`は同じ島の両側で共通の物理的基準端から数える。島端の定義をメタデータとして記録。
- 例：`H001,2026-10-21,A,LEFT,1,301`。
- 取込は`validate -> preview -> confirm -> publish`。不正CSVは公開版に反映しない。
- 同一版で台番号重複、同一位置重複、参照先不明、矛盾する隣接、期間重複を検出。
- 配置版の公開は原子的に行う。過去の版は不変で監査可能。
- 位置関係の自動生成後に手動補正可能。人間の確認を経た版のみ分析に利用。

## 8. インデックス・性能
- 日次実績：`UNIQUE(hall_id,business_date,machine_number)`、`(hall_id,business_date)`、`(model_id,business_date)`。
- 観測・スナップショット：`(hall_id,business_date,machine_number,observed_at DESC)`。
- グラフ点：`UNIQUE(series_id,point_index)`、系列単位の連続取得を最適化。
- 当たり履歴：`UNIQUE(series_id,ordinal)`。
- 設置・配置版の有効期間：GiST排他制約等を検討し、重複期間をDBレベルで防ぐ。
- 大規模化時は時系列のパーティショニング、圧縮、Parquetエクスポートを検討。初期から不要な複雑性を導入しない。
- 原本の保持期限は原則無期限。ただし法令・利用条件に抵触する場合は保存対象と保持期間を見直す。

## 9. データ品質・監査
必須検証：負のゲーム数/回数、範囲外数値、日付・タイムゾーン不整合、台番号重複、機種設置期間の矛盾、当たり順序不整合、グラフ軸/単位不明、点順序重複、サイト間不一致、値の不自然な逆行、原本参照切れ。
不正値は推測補完せず、理由コード・重大度・対象・検出日時を記録。品質ステータスは`valid, warning, quarantined, unavailable`等を定義。手動修正は元観測を改変せず、監査可能な訂正レコードで管理。

## 10. 実行・運用
- CLI例：`init-db`, `import-layout`, `validate-layout`, `collect`, `backfill`, `reparse`, `reconcile`, `export`, `audit`。実際の引数はヘルプとREADMEに記載。
- 収集間隔・対象店舗・対象期間・同時実行数・レート上限・再試行方針を設定可能にする。サイトの許容範囲を超えない。
- リトライは指数バックオフ、タイムアウト、失敗理由の分類、途中再開を備える。
- ログは構造化し、ジョブID、原本ID、店舗、営業日、台番号、処理段階を追跡可能にする。秘密情報は記録しない。
- PostgreSQLと原本ストレージを一貫してバックアップし、リストア試験を用意する。
- CSV/Excelは店舗・日付範囲・機種・台番号を絞り込み可能。数値の欠損と出典を保持。大量履歴はParquetも利用可能にする。

## 11. 推奨ディレクトリ構成
```text
slot-data/
  pyproject.toml
  alembic.ini
  alembic/
  config/
    app.example.yaml
    sources/
  src/slot_data/
    domain/
    models/
    schemas/
    adapters/
      pscube/
      daidata_online/
    ingestion/
    normalization/
    reconciliation/
    storage/
    layout/
    analytics/
    exports/
    cli/
    observability/
  tests/
    unit/
    integration/
    fixtures/
  docs/
    architecture.md
    erd.md
    schema_dictionary.md
    source_contracts.md
    data_quality.md
    operations.md
    decisions/
  docker-compose.yml
  README.md
```

## 12. 実装フェーズと受入条件
**Phase 0: 設計レビュー** — ER図、テーブル定義（型、NULL、PK/FK、UNIQUE、CHECK、索引）、有効期間の定義、収集元契約、ADR、データフロー、実装計画を提出。矛盾を解消する。

**Phase 1: DB基盤** — PostgreSQL、Alembic、ORM、テストDB、マイグレーション往復、制約テストを完成。

**Phase 2: 原本・正規化・改訂** — サイト非依存のテストフィクスチャで原本保存、解析版管理、観測追記、採用値更新、訂正履歴、冪等性を検証。

**Phase 3: 全履歴** — 当たり履歴系列、スランプグラフ全点、営業中スナップショットを保存。点数制限なし、再取得時も旧系列を保持。

**Phase 4: 配置管理** — CSV/Excelインポート、プレビュー、公開版管理、隣接・角台算出、過去日付での配置再現を検証。

**Phase 5: サイトアダプター** — P's CUBE、台データオンラインについて、利用条件と取得可能なデータを実地検証してアダプターを実装。取得できない項目はNULL、手動インポート経路を用意。

**Phase 6: 運用・出力** — スケジューリング、再試行、バックフィル、CSV/Excel/Parquet、監査、バックアップ、運用文書を完成。

必須テストケース：同一原本の再投入、同日複数回取得、閉店後訂正、異なるサイト間の値不一致、機種入替、台番号変更、配置版切替、当たり履歴の順序訂正、グラフ座標の全点保持、途中失敗後再開、原本からの再解析、取得不能・NULL、並列収集競合、バックアップ復元。

## 13. 最初の応答と作業ルール
最初に以下を提示すること：
1. 要求仕様の整合性レビューと未決定事項（重大度付き）
2. 正規化ER図とテーブル定義案
3. 収集元からDBまでのシーケンス図
4. 更新・改訂・冪等性の仕様
5. Phase 0〜6のタスク分解・依存関係・完了基準

その後、ブロッカーのみ質問し、Phase 1から順番に実装を進める。各フェーズで実装コード、実行コマンド、テスト結果、未解決事項を報告する。モックを本番接続済みと表現しない。未確認のデータ項目・サイト仕様・設定推測精度を断定しない。
