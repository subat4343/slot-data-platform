# データ品質・監査

## 1. 原則

- 不正値は推測で補完しない。原文のまま観測に残し、理由コード・重大度・対象・検出日時を `data_quality_issues` に記録する。
- 値が無いとき 0 を入れない。値の状態は `observed / not_published / fetch_failed / unparseable / unconfirmed / not_applicable` で区別する（日次は `field_status`、観測は `field_metadata`）。
- 手動修正は元の観測を改変しない。`manual_corrections` に記録し、採用値に重ねる。改訂履歴には `manual_correction` として残す。取り消すと（`revoked_at`）再照合で元の値に戻る。
- 致命的（error / critical）な検証結果を持つ観測は `quarantined` とし、採用候補から外す。既存の採用値は変更しない。

## 2. 品質ステータス

| 値 | 意味 |
|---|---|
| `valid` | 検証に問題なし |
| `warning` | 採用はするが要確認（範囲外・逆行・取得元間の不一致など） |
| `quarantined` | 隔離。採用値に使わない |
| `unavailable` | 取得元にデータが無い（系列・イベント単位で使用） |

## 3. 理由コード

定義は `src/slot_data/quality/issues.py` の `ISSUE_CODES`。

| コード | 既定の重大度 | 検出箇所 | 内容 |
|---|---|---|---|
| NEGATIVE_VALUE | error | 観測検証 | ゲーム数・回数・獲得枚数が負（差枚の負値は正常）。当たり履歴のゲーム数が負の場合は warning とし、その値だけ空にして原文を `raw_attributes.rejected_negative` に残す。当たり履歴の出枚（P's CUBE「出枚pt」）は負があり得るため検査しない |
| OUT_OF_RANGE | warning | 観測検証 | 日次として不自然な絶対値（上限は設定可能） |
| NORMAL_GT_TOTAL | error | 観測検証 | 通常時ゲーム数 > 総ゲーム数 |
| MAX_LT_MIN | warning | 観測検証 | 最大差枚 < 最小差枚（両方に定義が記録されている場合だけ判定） |
| BUSINESS_DATE_MISMATCH | error | 観測検証 | 観測時刻が営業日の開始より前（日付・タイムゾーンの不整合） |
| DUPLICATE_MACHINE_NUMBER | error | 観測検証 | 同一原本内で正規化後の台番号が重複 |
| VALUE_REGRESSION | warning | 観測検証 | 同一取得元の後続観測で累積値（総ゲーム数・回数）が減少 |
| BONUS_ORDER_INCONSISTENT | warning | 当たり履歴 | 累積ゲーム数が順番と逆行 |
| BONUS_ORDER_CHANGED | info | 当たり履歴 | 前回系列が今回系列の先頭と一致しない（訂正・並べ替え） |
| SLUMP_AXIS_UNKNOWN | warning | グラフ | 軸種別または単位が不明 |
| SLUMP_X_NOT_MONOTONIC | warning | グラフ | 横軸が逆行 |
| SLUMP_DUPLICATE_X | info | グラフ | 同じ横軸値の点が複数ある（削らずに保持） |
| SOURCE_MISMATCH | warning | 照合 | 同じ確定段階の異なる取得元で値が食い違う（差分を details に記録） |
| INSTALLATION_CONFLICT | warning | 照合 | 観測された機種が既存の設置期間と矛盾（期間は書き換えない） |
| MODEL_MAPPING_PENDING | info | 照合 | 機種の対応付けがレビュー待ち |
| PARSE_FAILED | error | 解析 | 原本を解析できない。解析結果の保存で例外になった場合（details.stage = persist）は原本を未解析に戻すので、原因を直して `reparse --pending` で取り込む |
| PARSE_RECORD_REJECTED | error | 解析 | 原本内の一部レコードを解析できない |
| FETCH_FAILED | warning | 収集 | 取得失敗（取得不能日の欠損理由）。巡回が途中で止まった場合は details.kind = crawl_aborted と再開コマンドを記録 |
| RAW_MISSING | critical | 監査 | 原本ファイルが無い（参照切れ） |
| RAW_HASH_MISMATCH | critical | 監査 | 原本ファイルのハッシュが一致しない |
| RAW_ORPHAN_FILE | info | 監査 | DB から参照されない原本ファイル |
| RAW_UNPARSED | warning | 監査 | 一度も解析されていない原本 |

DB 制約で防ぐもの（課題ではなくエラーになる）: 日次実績の回数・ゲーム数の負値、店舗＋営業日＋台番号の重複、設置期間の重複（GiST 排他制約）、公開済み配置版の期間重複、配置版内の台番号・位置の重複、当たり履歴の順番の重複、グラフ点番号の重複、原本・観測・改訂の変更／削除。

## 4. 監査（`slot-data audit`）

| 検査 | 結果 |
|---|---|
| raw_payloads が参照する全ファイルの存在と SHA-256 | RAW_MISSING / RAW_HASH_MISMATCH（終了コード 1） |
| ストア内で参照されないファイル | RAW_ORPHAN_FILE |
| 解析が一度も試みられていない原本 | RAW_UNPARSED → `reparse --pending` で復旧 |
| `daily_machine_stats.revision_number` と改訂履歴の最大番号の一致 | revision_mismatch（終了コード 1） |

定期実行（日次）を推奨する。`--no-hash` を付けると存在確認だけになり、高速に終わる。
