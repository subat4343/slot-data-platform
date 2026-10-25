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
| 判断（2026-10-21） | 許可が無いため自動取得・HTML 解析は実装しない。`automated_fetch: prohibited` |
| 許可（2026-10-23） | ユーザー申告: 株式会社アンジェロより許可取得済み。個人利用については制限なし。ただし、「高頻度アクセスの禁止」。ブラウザ操作で人が確認する程度の頻度とする。`automated_fetch: permitted`、根拠は `config/sources/pscube.yaml` の `permission_reference` |

### 1.1 DMMぱちタウン経由の画面（2026-10-23 実地確認）

| 項目 | 内容 |
|---|---|
| 入口 | ぱちタウン店舗ページ https://p-town.dmm.com/shops/kanagawa/13152/jackpot （スロットハトヤ）。ページ読み込み後に iframe `#dedama-iframe` へ `https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c743201/` が設定される |
| ぱちタウンの robots.txt | `/shops/<都道府県>/<店舗>/jackpot` は Disallow の対象外 |
| 画面遷移 | トップ →「スロットデータ」（`nc-v03-001.php`、機種一覧）→ 機種（`nc-v05-003.php`）→ 台番号（`nc-v06-001.php?cd_dai=…`、台詳細）。いずれも iframe 内のクリックで遷移 |
| 機種一覧 | 機種名、レート（例「21.3スロ」、`bai=21.277`）、台数。2026-10-23 時点で 20 機種 |
| 機種画面 | 台番号 × 日別（本日〜6日前）の「BONUS」回数、更新時刻（例「2026/10/23 17:35 更新」）。BIG/REG の内訳・ゲーム数・差枚は無い |
| 詳細グラフ | スランプグラフ（画像のみ。数値なし） |
| 台詳細 | BIG、REG、BIG確率、REG確率、合成確率、累計ゲーム、最終ゲーム、出枚数（▲ は負）、BIG過去最高が日別（本日〜）に並ぶ。推移グラフ・スランプグラフ・本日の特賞履歴（回数・時刻・ゲーム・出枚pt・ステータス）の枠がある。**正常表示時の HTML は未取得**のため、項目の対応・表の下の続きは未確認 |
| 台詳細の拒否 | P's CUBE を直接開くと「INFORMATION error(451)」で表示されない。ぱちタウン経由・画面ありのブラウザで、人が 1 ステップずつ操作した場合（17:30）は表示された。ぱちタウン経由でも、画面なし（17:37）および画面ありの自動操作（17:42）では error(451)。短時間に台詳細を繰り返し開いたことによる制限か、自動操作の判定かは未確定 |
| データ取得の仕組み | 画面表示後に `cgi-bin/nc-m01-001.php` などへの通信でデータを読み込む。**これらを直接呼ぶことは「API 等の利用禁止」に当たるため行わない** |
| 台詳細の拒否の仕組み（2026-10-25 通信記録） | 機種一覧（`nc-m03-001.php`）・機種画面（`nc-m05-003.php`）のデータ通信は 200。台詳細のデータ通信 `nc-m06-001.php` だけに、ページ内の難読化スクリプトが生成するトークン（`_i`、`_t`）が付き、自動操作のブラウザ（画面あり・ぱちタウン経由・30 秒間隔・60 秒待機）では **HTTP 451** が返った。待ち時間の問題ではなく、サイト側のボット対策による拒否と判断。トークンの解析や判定を通すための変更は行わない。台詳細の自動取得は運営側の正規の手段（接続元の許可・データ提供など）が得られるまで保留 |
| 運営側の線引き（2026-10-26 ユーザー申告） | 「通常のブラウザで取得できる範囲で、人が毎晩確認してノートに記録する程度の定点収集は可。毎秒アクセスなど人には不可能な頻度・データ差分の取得は不可」。自動操作そのものは否定されていない |
| 台詳細の表示（2026-10-26） | 自動操作の目印（`--disable-blink-features=AutomationControlled`、`navigator.webdriver`）を無効化したブラウザで、ぱちタウン経由・30 秒間隔で開くと表示された。ページは reCAPTCHA のスクリプトを読み込むが、チャレンジ画面は出ていない。日別表（`#tblDAbv2`：BIG、REG、BIG確率、REG確率、合成確率、累計ゲーム、最終ゲーム、出枚数、BIG過去最高 × 本日〜6日前）と本日の特賞履歴（`#tblHISTb`：回数・時刻・ゲーム・出枚pt・ステータス。初期表示は最新 20 件、残りは「もっと見る」）を確認。ADR-0012 |

### 1.2 収集方式

- ぱちタウンの店舗ページを**画面ありの Chromium**で開き、iframe 内を人の閲覧順にクリックする（`adapters/pscube/`）。
- 画面遷移の間隔は 30 秒以上、同時に開くのは 1 ページ。robots.txt を遷移ごとに確認する。
- UA の偽装、webdriver の隠蔽など、検知を回避する処理は入れない。
- 画面の HTML を原本として保存する。機種画面の BONUS 回数は取得元 `pscube_list`、台詳細は `pscube` として記録する（台詳細を優先）。
- 台詳細で error(451) が出たら、その回の台詳細巡回を打ち切り、`FETCH_FAILED`（kind=access_denied）を記録する。拒否画面も証跡として原本保存し、`--resume` では取り直す。
- 台詳細のパーサー（`MachineDetailParser` 1.0.0）は 2026-10-26 の実画面で実装した。BIG→bb_count、REG→rb_count、累計ゲーム→total_games、最終ゲーム→last_games、出枚数→net_medals（▲ は負）、BIG+REG→bonus_count（計算値）。確率・BIG過去最高は原文を参考値として保持。特賞履歴は表示分のみ（20 件より前があれば partial）。
- それ以前に保存した台詳細の原本は `slot-data reparse` で取り込める。
- 実行: 毎日 23:30（閉店 22:30 の後）。手順は [deploy_windows.md](deploy_windows.md)。

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
