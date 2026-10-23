# 収集元契約

収集元ごとに、利用条件・取得方式・データ形式・取得可能項目・過去期間を記録する。
**未確認の項目は「未確認」と書き、推測で埋めない。**

## 1. P's CUBE（code: `pscube`）

| 項目 | 内容 |
|---|---|
| URL | https://www.pscube.jp/ |
| 調査日 | 2026-10-21 |
| robots.txt | `User-agent: *` → `Allow: /`。Content-Signal: `search=yes, ai-train=no, use=reference` |
| 利用約款 | https://www.pscube.jp/docs/yakkan/ |
| 自動取得 | **禁止**。第5条(1)「当社から明示的な許可を得ることなく、自動化された手段で本サイト上の情報を取得すること」 |
| HTML 抽出 | **禁止**。第5条(2)「本サイトから、ウェブページのHTMLデータを取得して、取得したデータの中から、特定のトピックに関わるデータを抽出、整形しなおすこと」 |
| 二次利用・開示 | 第5条(3) 複製・改変・転載等の二次利用を禁止。第5条(4)「本サイトにおいて提供される各種データをもとに、特定の店舗の情報を作成すること。そしてこれを第三者に開示すること」を禁止 |
| 負荷 | 第5条(7) システム負荷・不正アクセスを禁止。第8条(5) 平均を著しく上回る大量通信の継続を禁止 |
| 判断 | 自動取得・HTML 解析は実装しない。`access_policy.automated_fetch: prohibited` |
| エンドポイント・認証・形式・過去期間 | 未確認（自動取得が許可されていないため調査していない） |

## 2. 台データオンライン（code: `daidata_online`）

| 項目 | 内容 |
|---|---|
| URL | https://daidata.goraggio.com/ |
| 調査日 | 2026-10-21 |
| robots.txt | `User-agent: *` に対し `Disallow: /9*`, `/*/1*`, `/*/2*`, `/*/9*`（数字で始まる下位パス = 店舗・台の詳細系と見られる）。ClaudeBot・GPTBot 等は全体を Disallow |
| 利用規約 | https://daidata.goraggio.com/agreement |
| 複製 | 第4条10号「本サイトのコンピュータプログラム及びデータベース等を複製…」を禁止 |
| 第三者提供 | 第4条11号「本サイトを利用することにより取得した情報を第三者に開示及び売買等を行う行為」、第13条2項「掲載されている情報に関して有償無償を問わず第三者に提供してはならない」 |
| 権利帰属 | 第8条1項 データベース及びデータの著作権等は運営元または運営元の指定する第三者に帰属 |
| 判断 | 台データの取得は robots の Disallow 範囲に当たり、蓄積はデータベースの複製に当たるおそれがある。自動取得は実装しない。`automated_fetch: prohibited` |
| エンドポイント・認証・形式・過去期間 | 未確認 |

## 3. 自動取得を有効にする条件

1. 運営者から書面（メール・契約）で、取得方式・頻度・用途の許可を得る。
2. `config/sources/<code>.yaml` を `automated_fetch: permitted` に変更し、`permission_reference` に許可の根拠を書く。
3. 許可された方式（提供 API、データ提供ファイル等）に合わせて、アダプターの `plan` / `fetch` / パーサーを実装する。パーサーは `schemas/parsed.py` の中間表現を返し、実データ由来のテストフィクスチャを `tests/fixtures/<code>/` に追加する。
4. レート制限・同時実行数は許可条件の範囲内に設定する。認証回避、CAPTCHA 回避、アクセス制限の回避は実装しない。403/429 を受けたら再試行せず停止する。

許可ゲートは、`plan` と `fetch` の最初、およびパイプラインの `collect` の最初で確認する。禁止中は HTTP リクエストを 1 件も送らない（テスト `test_collect_refuses_before_any_request`）。

## 4. 手動インポート形式（代替経路）

人が閲覧・記録した値を、以下の形式のファイルにして `slot-data import-observations` で投入する。ファイルは原本として保存し、自動取得と同じ経路（解析版管理・観測追記・採用値更新・改訂履歴）で処理する。
**サイトの利用条件（第三者提供の禁止等）は手動記録したデータにも及ぶ。** 蓄積データの外部公開・提供はしない。

### 4.1 JSON（`slot-data/manual-v1`、完全形式）

```json
{
  "format": "slot-data/manual-v1",
  "hall_code": "H001",
  "observed_at": "2026-10-20T23:30:00+09:00",
  "business_date": "2026-10-20",
  "source_reported_at": "2026-10-20T23:00:00+09:00",
  "source_state": "closed",
  "scope": "営業日累計",
  "machines": [
    {
      "machine_number": "301",
      "model_name": "機種名（表示どおり）",
      "model_id": null,
      "fields": {
        "total_games": {"value": 5123, "raw": "5,123", "label": "総回転", "unit": "G"},
        "net_medals": -820,
        "bb_count": {"value": 0, "label": "BIG", "category": "BB"},
        "rb_count": null,
        "at_count": {"status": "fetch_failed"},
        "max_net_medals": {"value": 1200, "origin": "graph_estimated", "definition": "グラフ目視"}
      },
      "bonus_events": {
        "status": "complete",
        "events": [
          {"ordinal": 1, "event_time": "2026-10-20T11:02:00+09:00", "hit_games": 120,
           "cumulative_games": 120, "raw_label": "BIG", "normalized_type": "BB", "payout_medals": 240}
        ]
      },
      "slump_series": [
        {"series_key": "main", "x_axis_type": "cumulative_games", "x_unit": "games",
         "y_axis_type": "net_medals", "y_unit": "medals",
         "points": [{"x_numeric": 0, "y_numeric": 0}, {"x_numeric": 120, "y_numeric": -240}]}
      ]
    }
  ]
}
```

- `fields` の値: 整数 = 観測値（0 は真のゼロ）。`null` = 未公開。オブジェクトでは `status` に `observed / not_published / fetch_failed / unparseable / unconfirmed / not_applicable` を指定し、`raw`・`label`・`category`・`unit`・`origin`（`displayed / calculated / graph_estimated / manual`）・`definition` を添えられる。
- 指標名: `total_games, normal_games, net_medals, bb_count, rb_count, at_count, cz_count, first_hit_count, last_games, max_net_medals, min_net_medals, max_payout_medals`。入力に無い指標は「未公開」として扱い、0 は補完しない。
- `business_date` を省略すると、`observed_at` と店舗の営業日境界から算出する。
- 時刻にタイムゾーンが無い場合は店舗のタイムゾーンとみなす。
- グラフを画像からなぞった座標は `origin_type: "estimated"`、`x/y_axis_type: "pixel"`、`derivation_method` / `derivation_version` を付けた別系列にする。画素値を差枚として扱わない。

### 4.2 CSV（`manual-csv-v1`、日次指標のみ）

```csv
machine_number,model_name,business_date,observed_at,total_games,net_medals,bb_count,rb_count,source_state
301,機種名,2026-10-20,2026-10-20T23:30:00+09:00,"5,123",-820,0,,closed
```

必須列は `machine_number, observed_at`。数値セルの空欄と `-` は「未公開」と扱う。`#fetch_failed` `#unparseable` `#unconfirmed` `#not_applicable` で状態を明示できる。数値として読めない値は「解析不能」として原文を残す。文字コードは UTF-8（BOM 可）。読めない場合は CP932 を試す。
