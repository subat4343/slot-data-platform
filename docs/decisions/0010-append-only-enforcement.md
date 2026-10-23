# ADR-0010 追記専用・不変性の DB での担保

## 決定
- `raw_payloads`、`source_observations`、`daily_stat_revisions`、`machine_snapshots`、`bonus_event_series`、`bonus_events`、`slump_series`、`slump_points` には UPDATE/DELETE を禁止するトリガーを付ける。
- `parse_runs` は running の行を終了状態にする更新だけを許す。
- 公開済み配置版とその配下（島・位置・関係）は変更・削除を禁止する（`valid_to` の確定だけ例外）。
- `source_mappings.internal_entity_id` は entity_type 別に参照先の存在をトリガーで検証し、参照されている店舗・機種の削除も禁止する。
- 法令・利用条件の都合で削除が必要な場合に限り、同一トランザクションで `SET LOCAL slot_data.allow_destructive = 'on'` を実行すれば解除できる。TRUNCATE は行トリガーの対象外なので、運用で使わない。

## 影響
アプリの不具合で履歴が壊れない。テストの初期化は TRUNCATE で行う。
