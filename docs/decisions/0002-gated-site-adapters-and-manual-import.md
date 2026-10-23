# ADR-0002 サイトアダプターの許可ゲートと手動インポート

## 状況
2026-10-21 の調査（`docs/source_contracts.md`）で、P's CUBE は約款で自動取得と HTML 抽出を明示的に禁止していた。台データオンラインは robots.txt で詳細系パスを全 UA に Disallow し、規約でデータベースの複製と第三者提供を禁止していた。仕様 §1 は「利用不可の場合は収集を止め、手動インポートを代替とする」「認証回避、CAPTCHA 回避、アクセス制限回避を実装しない」としている。

## 決定
- 両サイトの自動取得と HTML パーサーは実装しない。サイトの HTML 構造の調査もしない（調査自体が禁止行為の準備に当たるため）。
- アダプターの枠組み（`SourceAdapter`、`PoliteHttpClient`、パイプラインの `collect`／resume）は実装し、テスト用のローカル応答で検証する。
- 許可ゲート: `access_policy.automated_fetch` が `permitted` で、`permission_reference` がある場合だけ fetch を許す。`unverified`（未確認）も拒否する。
- 本番経路は手動インポート（manual-v1 JSON / CSV）とする。ファイルを原本として保存し、同じパイプラインを通す。
- 403/429・robots の Disallow は再試行せず停止する。

## 影響
- 運営者の許可が得られたら、許可された方式に合わせて Phase 5 の残り（plan / fetch / parser と実データのフィクスチャ）を実装する。
- 手動記録したデータにもサイトの利用条件（第三者提供の禁止等）が及ぶ。出力物を外部へ公開しない。
