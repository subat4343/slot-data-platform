# ADR-0007 確定段階の遷移規則

## 決定（仮定を含む）
- 閉店時刻（`halls.closing_time`。未設定なら営業日終端）より前の観測は `provisional`、以降は `final_candidate`。閉店後の取得でも自動では final にしない。
- 同一取得元で、同じ値の final_candidate 観測が `selection.settle_hours`（既定 24 時間。**仮定**）以上続いたら `final`。同一内容の再取得（duplicate_content）の時刻も継続の根拠に含める。
- final_candidate / final に達した後に値が変われば、改訂理由 `correction` とし、`is_corrected = true` にする。`corrected` は状態ではなくフラグとする（仕様 §6-8）。訂正後は final_candidate に戻る。
- 状態の変化も採用値の変化として改訂を残す（理由 `status_change`）。

## 未決定
settle_hours の値（`spec_review.md` Q2）。取得元ごとの訂正傾向が分かったら見直す。
