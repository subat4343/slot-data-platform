# ADR-0008 採用値の選択ルール v1

## 決定
1. 候補: 各原本の最新成功解析に由来し、quarantined でない観測。各取得元から最新 observed_at の 1 件を取る。
2. 並び順: 確定段階（final_candidate > provisional）→ 取得元優先度（設定。小さいほど優先。**仮定**: pscube=10, daidata_online=20, manual=50）→ 品質（valid > warning）→ 完全性（観測値の数）→ 鮮度（observed_at）→ ID。
3. 指標は採用した 1 観測から一括で取る。取得元をまたいで指標を混ぜない（仕様 §5-2）。
4. 同じ確定段階で異なる取得元の値が食い違えば `SOURCE_MISMATCH` を記録し、日次の品質を warning にする。
5. 有効な手動訂正を最後に重ねる。
6. 改訂には `selection_rule_version = "v1"` を記録する。ルールを変えたら版を上げ、`reconcile` で再計算する。

## 理由
確定段階を最優先にするのは、閉店前の途中経過が閉店後の値を上書きしないようにするため。指標を混ぜないのは、サイトごとに BB/RB 等の定義が違うため。
