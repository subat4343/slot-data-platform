# ADR-0005 店舗コード列の追加

仕様 §7 の配置 CSV は `hall_code` で店舗を指定するが、§4.1 `halls` にコード列が無い。`halls.code TEXT UNIQUE NOT NULL` を追加し、CLI・手動インポート・配置 CSV で共通に使う。取得元側の店舗 ID は `source_mappings(entity_type='hall')` で対応付ける。

あわせて、確定段階の判定に使う `halls.closing_time TIME NULL` を追加した（ADR-0007）。
