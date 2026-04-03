# ポーカーGTO 分析システム 仕様書

**バージョン:** 2.0  
**最終更新:** 2026-04-03  
**リポジトリ:** https://github.com/9p96d9/GTO-  
**本番URL:** https://gto-production.up.railway.app  

---

## 1. システム概要

T4ポーカーサイトのハンド履歴テキストをアップロードするだけで、GTO観点の分析を行うWebアプリケーション。

### モード一覧

| モード | 説明 | Gemini API |
|---|---|---|
| **classifyモード（デフォルト）** | アップロード → parse → classify → Web結果画面 → 選択肢提示 | 不要 |
| **NoAPI PDF** | 分類結果からAPIなしでPDFを生成 | 不要 |
| **AI PDF** | 分類結果にGemini分析を追加してPDFを生成 | 必要（BYOK） |
| **クイック解析** | 統計ダッシュボードのみ（PDFなし、即時） | 不要 |

### フロー図（classifyモードがメイン）

```
[ブラウザ: ファイルアップロード + hero_name（任意）]
         │ POST /upload
         ▼
[server.py] → run_classify_pipeline（バックグラウンド）
         │
         ├── Step1: parse.py        → data/upload.json
         ├── Step2: classify.py     → data/upload_classified.json
         │
         └── /classify_result/{job_id} に遷移（Web結果画面）
                    │
          ┌─────────┴──────────┐
          │                    │
  POST /generate_pdf       POST /start_ai
          │                    │
   run_pdf_pipeline       run_ai_pipeline
   (NoAPI PDF)            (Gemini + AI PDF)
          │                    │
  NoAPI_Report_*.pdf    GTO_Report_*.pdf
```

---

## 2. アーキテクチャ

### 技術スタック

| レイヤー | 技術 |
|---|---|
| Webフレームワーク | FastAPI + uvicorn |
| 言語 | Python 3.11 / Node.js 20 |
| AI | Google Gemini 2.5 Flash（オプション） |
| 手役評価 | treys ライブラリ（classify.py で使用） |
| PDF生成 | puppeteer（Chromium内蔵） |
| リアルタイム通信 | SSE（Server-Sent Events） |
| 認証・DB | Firebase Auth（Google）/ Firestore |
| ブラウザ拡張 | Chrome拡張機能（MV3） |
| ホスティング | Railway（Docker） |
| コンテナ | python:3.11-slim + Node.js 20 |

### ファイル構成

```
GTO-/
├── server.py                   # FastAPI サーバー（全エンドポイント・HTMLテンプレート含む）
├── Dockerfile                  # Railway/本番用コンテナ定義
├── docker-compose.yml          # ローカルDocker用
├── requirements.txt            # Python依存（fastapi, uvicorn, sse-starlette, treys, google-generativeai, firebase-admin）
├── package.json                # Node.js依存（puppeteer）
├── bookmarklet.js              # 旧: T4サイトからハンド履歴を直接送信するブックマークレット（後方互換）
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── analyze.py              # Gemini GTO分析（JSON → JSON + gto_analysis）
│   ├── generate.js             # AI PDFレポート生成（puppeteer）
│   ├── generate_noapilist.js   # NoAPI PDFレポート生成（puppeteer）
│   ├── quick_analyzer.py       # クイック統計計算（API不要・即時）
│   └── firebase_utils.py       # Firebase Admin SDK ユーティリティ（Firestore CRUD / idToken検証）
├── extension/                  # Chrome拡張機能（MV3）
│   ├── manifest.json           # 拡張機能設定（oauth2 client_id含む）
│   ├── popup.html / popup.js   # ポップアップUI（ログイン・スクレイプ送信）
│   ├── background.js           # Service Worker（Firebase Auth管理）
│   ├── content.js              # T4スクレイプ処理（bookmarklet移植）
│   ├── icons/                  # アイコン画像（16/48/128px）
│   └── README.md               # セットアップ手順
├── static/                     # 静的ファイル（/static/ で配信）
├── input/                      # アップロードtxt一時置き場
│   └── done/                   # 処理済みtxt（処理後自動移動）
├── output/                     # 生成PDF置き場
└── data/                       # 解析済みJSON（累積・削除禁止）
    ├── upload.json             # parse.py 出力
    ├── upload_classified.json  # classify.py 出力
    └── opponents_summary.json  # 対戦相手累積サマリー（削除禁止）
```

---

## 3. パイプライン詳細

### 3-1. parse.py

**入力:** `input/upload.txt`（T4ハンド履歴テキスト）  
**出力:** `data/upload.json`  
**CLI:** `python scripts/parse.py <input.txt> <output.json> [--hero-name <名前>]`

**処理内容:**
- `===...===` + `ハンドN/M` 行でハンドを分割
- Hero判定の優先順位:
  1. `--hero-name` 指定あり → 大文字小文字無視で完全一致
  2. 指定なし → `Guest`, `Guest\d*`, `Weq\*+` パターンマッチ
  3. 上記でも未検出 → 全ハンドで最多登場プレイヤーを自動採用（stderr に `[Hero自動検出]` 出力）
- カード正規化: 絵文字変体セレクタ（U+FE0E/FE0F）除去
- プレイヤーブロック: 旧フォーマット（名前→カード→結果）と新フォーマット（名前→結果→カード）両対応
- ストリート: Preflop / Flop{pot}bb / Turn{pot}bb / River{pot}bb をパース
- All-in EV: `allin_ev: {プレイヤー名: float}` に格納
- Result: 勝者・Rake・All-in EV を格納
- Showdown: `SD{pot}bb` 以降をパース
- 3BETポット判定: プリフロップで Raise が2回以上 → `is_3bet_pot: true`
- `opponents_summary.json` を更新（同一ファイルの二重登録防止）

**JSONスキーマ（1ハンド）:**
```json
{
  "hand_number": 1,
  "hand_id": "xxxx",
  "datetime": "2026-03-13T08:58:00",
  "game": "6-Max NLH",
  "blinds": {"sb": 0.5, "bb": 1.0},
  "players": [
    {
      "position": "BTN",
      "name": "Guest",
      "is_hero": true,
      "hole_cards": ["A♠", "K♥"],
      "result_bb": 5.5
    }
  ],
  "streets": {
    "preflop": [{"position": "BTN", "name": "Guest", "action": "Raise", "amount_bb": 3.0}],
    "flop": {"board": ["T♠", "7♥", "2♦"], "pot_bb": 6.0, "actions": [...]},
    "turn": {"board": ["J♣"], "pot_bb": 12.0, "actions": [...]},
    "river": {"board": ["5♠"], "pot_bb": 20.0, "actions": [...]}
  },
  "showdown": [{"name": "Guest", "hand_name": "トップペア"}],
  "result": {
    "winners": [{"name": "Guest", "amount_bb": 22.5}],
    "rake_bb": 0.5,
    "allin_ev": {"Guest": 18.0}
  },
  "hero_position": "BTN",
  "hero_cards": ["A♠", "K♥"],
  "hero_result_bb": 5.5,
  "is_3bet_pot": false,
  "went_to_showdown": true,
  "analyzed": false,
  "gto_evaluation": "",
  "has_gto_error": false,
  "is_good_play": false
}
```

---

### 3-2. classify.py

**入力:** `data/upload.json`  
**出力:** `data/upload_classified.json`（各ハンドに `bluered_classification` フィールドを追加）  
**CLI:** `python scripts/classify.py <input.json> <output.json>`

**分類ロジック:**

```
postflopに進まなかった → preflop_only

postflopあり:
  └── ショーダウンあり (went_to_showdown)
        ├── 勝ち + 最終アグレッサー=Hero  → value_or_bluff_success  （青線）
        ├── 勝ち + 最終アグレッサー=相手  → bluff_catch             （青線）
        ├── 負け + 最終アグレッサー=Hero  → bluff_failed            （青線）
        └── 負け + 最終アグレッサー=相手  → call_lost               （青線）

  └── ショーダウンなし (non-showdown)
        ├── Heroが勝ち（相手がfold）       → hero_aggression_won    （赤線, needs_api=true）
        └── Heroが負け（Heroがfold）
              ├── treysで判定可能
              │     ├── 勝てた → bad_fold                          （赤線）
              │     └── 負けてた → nice_fold                       （赤線）
              └── 判定不能 → fold_unknown                          （赤線, needs_api=true）
```

**treysライブラリ:**  
フォールド時に相手の手札とボードが判明している場合、`treys.Evaluator` で手役を比較してbad_fold/nice_foldを判定。未インストール時はfold_unknownにフォールバック。

**bluered_classification フィールド:**
```json
{
  "line": "blue",
  "category": "value_or_bluff_success",
  "category_label": "バリュー/ブラフ成功",
  "needs_api": false,
  "showdown": true,
  "last_street": "river"
}
```

**カテゴリ一覧:**

| category | 日本語 | line | needs_api |
|---|---|---|---|
| `value_or_bluff_success` | バリュー/ブラフ成功 | blue | false |
| `bluff_catch` | ブラフキャッチ | blue | false |
| `bluff_failed` | ブラフ失敗 | blue | false |
| `call_lost` | コール負け | blue | false |
| `hero_aggression_won` | アグレッション勝利 | red | true |
| `bad_fold` | バッドフォールド | red | false |
| `nice_fold` | ナイスフォールド | red | false |
| `fold_unknown` | フォールド(要確認) | red | true |
| `preflop_only` | プリフロップのみ | preflop_only | false |

---

### 3-3. analyze.py（AIモード専用）

**入力:** `data/upload.json`  
**出力:** 同ファイルに `gto_analysis` / `gto_evaluation` フィールドを追記  
**処理:** バッチ10ハンド単位でGemini 2.5 Flashに送信、429レートリミット時は5秒待機・最大3回リトライ、`analyzed: true` のハンドはスキップ

---

### 3-4. generate.js（AI PDFモード）

**入力:** `data/upload.json`（analyze.py済み）  
**出力:** `output/GTO_Report_{最古日付}_{最新日付}.pdf`  
**処理:** HTML生成 → Puppeteer（A4縦・余白10mm）でPDF変換  

**PDF セクション構成:**

| # | 内容 |
|---|---|
| 1 | サマリー（総損益・ハンド数・VPIP/PFR等） |
| 2 | 3BETポット専用分析（カテゴリ順ソート・カテゴリ区切り行あり） |
| 3 | 単独レイズポット（カテゴリ順ソート・カテゴリ区切り行あり） |
| 4 | ポジション別成績表 |
| 5 | 改善すべきプレイ（Gemini生成） |
| 6 | GTO評価カテゴリ別（❌ミス赤・⚠️改善橙） |
| 7 | ストレングスハンド分析（Gemini生成） |
| 8 | 対戦相手プロファイルカード（累積集計・Gemini生成） |

**カテゴリ順ソート（generate.js・generate_noapilist.js 共通）:**  
青線: `value_or_bluff_success` → `bluff_catch` → `bluff_failed` → `call_lost`  
赤線: `hero_aggression_won` → `bad_fold` → `nice_fold` → `fold_unknown`  
同カテゴリ内はストリート（preflop→flop→turn→river）→ハンド番号順

---

### 3-5. generate_noapilist.js（NoAPI PDFモード）

**入力:** `data/upload_classified.json`  
**出力:** `output/NoAPI_Report_{最古日付}_{最新日付}.pdf`  
**処理:** classify.pyの結果のみでPDF生成（Gemini不要）

---

### 3-6. quick_analyzer.py（クイック解析）

**入力:** parse.py出力のJSONデータ（dict）  
**出力:** 統計データdict（HTTP レスポンスでダッシュボードHTMLに渡す）

**統計項目:**
- `summary`: total_hands / total_bb / bb_per_100
- `timeline`: 累積損益タイムライン（ハンドごと）
- `streets`: ストリート別決着数・ショーダウン数
- `bet_sizing`: フロップ以降のベットサイジング別（〜33% / 〜66% / 〜100% / オーバーベット）勝率・平均BB
- `win_types`: 勝利パターン（value / bluff / bluff_catch / other）
- `combos`: ホールカードのコンボキー別（AA / AKs / AKo など）成績

---

## 4. APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | アップロード画面 |
| POST | `/upload` | ファイル受信 → classifyパイプライン開始 |
| GET | `/classify_progress/{job_id}` | 分類進捗画面（SSE接続） |
| GET | `/classify_result/{job_id}` | 分類結果Web画面（PDF選択画面） |
| POST | `/generate_pdf/{job_id}` | NoAPI PDFを生成 |
| POST | `/start_ai/{job_id}` | AI分析+AI PDFを生成（api_key フォームパラメータ） |
| GET | `/progress/{job_id}` | PDF生成進捗画面（SSE接続） |
| GET | `/stream/{job_id}` | SSEイベントストリーム |
| GET | `/status/{job_id}` | ジョブ状態JSON |
| GET | `/error/{job_id}` | エラー詳細画面 |
| GET | `/report/{name}` | PDFビューア画面 |
| GET | `/pdf/{name}` | PDF inline配信 |
| GET | `/download/{name}` | PDF ダウンロード |
| POST | `/analyze/quick` | クイック解析 → ダッシュボードへリダイレクト |
| GET | `/dashboard/{job_id}` | クイック解析ダッシュボード |
| POST | `/scrape_upload` | ブックマークレットからJSON直接POST（サーバーAPIキー使用） |

### POST /upload フォームパラメータ

| パラメータ | 型 | 説明 |
|---|---|---|
| `file` | UploadFile | ハンド履歴txtファイル |
| `hero_name` | str（省略可） | Hero名を明示指定（省略時は自動検出） |

### /status/{job_id} レスポンス例

```json
{"step": 1, "status": "running", "pdf": "", "log": "", "mode": "classify", "hero_name": "MyName"}
{"step": 0, "status": "done", "pdf": "", "log": "", "mode": "classify", "json_path": "...", "classified_path": "..."}
{"step": 3, "status": "done", "pdf": "NoAPI_Report_2026-03-13_2026-03-13.pdf", "log": "", "mode": "noapi"}
{"step": 3, "status": "done", "pdf": "GTO_Report_2026-03-13_2026-03-13.pdf", "log": "", "mode": "api"}
{"step": 0, "status": "error", "pdf": "", "log": "エラー内容..."}
```

### SSE イベント型一覧

| type | 説明 |
|---|---|
| `parse_done` | パース完了（`hands_total` を含む） |
| `generate_start` | PDF生成開始 |
| `classify_done` | 分類完了 |
| `done` | 全処理完了（`pdf` ファイル名を含む） |
| `error` | エラー発生（`message` を含む） |
| `batch_done` | Gemini分析1バッチ完了（`done` / `total` を含む） |

---

## 5. ジョブ管理

`server.py` のメモリ上で管理（再起動でリセット）:

```python
jobs: dict[str, dict]            # job_id → ジョブ状態
quick_results: dict[str, dict]   # job_id → quick_analyzer の結果
classify_results: dict[str, dict] # (現在未使用の予備)
event_queues: dict[str, asyncio.Queue]  # job_id → SSEキュー
```

複数ジョブが同時進行可能（threading.Lock で排他制御）。

---

## 6. 対戦相手サマリー（opponents_summary.json）

`parse.py` 実行のたびに `data/opponents_summary.json` を更新（同一ファイルの二重登録防止）。

**集計内容（プレイヤーごと）:**
- `total_hands`: 対戦ハンド数
- `vpip` / `pfr` / `threebet`: 各レートの小数（0〜1）
- `hero_winrate`: Heroが勝ったセッション割合
- `player_type`: LAG / TAG / LP / TP / ルース / タイト / アグレッシブ / パッシブ / バランス
- `sessions`: 対戦したセッション日付リスト

**このファイルは削除禁止。** セクション8（対戦相手プロファイル）の累積データとして使用。

---

## 7. Hero自動検出ロジック

```
1. --hero-name 指定あり → 大文字小文字無視の完全一致
2. なし → HERO_NAMES{"Guest"} / HERO_PATTERN(Weq\*+|Guest\d*) でマッチ
3. 上記でも0件 → 全ハンドで最多登場プレイヤー名を自動採用
```

`quick_analyzer.py` でも同様のフォールバックを実装（`is_hero` フラグ優先 → 最多登場）。

---

## 8. BYOK（Bring Your Own Key）

- `/start_ai/{job_id}` の `api_key` フォームパラメータで送信
- サーバーに保存しない（メモリ上でのみ使用）
- 環境変数 `GEMINI_API_KEY` があればデフォルト値として表示
- `/scrape_upload` はサーバー側の `GEMINI_API_KEY` を使用（ブックマークレット用）

---

## 9. 環境変数

| 変数名 | 説明 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini APIキー | AIモードのみ。BYOKで代替可 |
| `PORT` | サーバーポート（デフォルト: 5000） | 任意 |

---

## 10. デプロイ情報

### 本番環境（Railway）
- URL: `https://gto-production.up.railway.app`
- リポジトリ: `https://github.com/9p96d9/GTO-.git`
- ブランチ: `master`
- 自動デプロイ: `master` へのpushで自動ビルド・デプロイ

### ローカル実行
```bash
cd c:\iino\自作現用\GTO--main
python server.py
# → http://localhost:5000
```

### アップデート手順
```bash
git add .
git commit -m "変更内容"
git push
# Railwayが自動でリビルド・デプロイ
```

---

## 11. 注意事項

- `data/` フォルダのJSONは削除しない（opponents_summary.jsonは特に重要）
- `.env` はGitにコミットしない（`.gitignore` 設定済み）
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- classifyモード（NoAPI PDF）はGemini不要で高速（数秒〜十数秒）
- AIモードはGemini APIの呼び出し時間がボトルネック（約2〜3分）
- treysライブラリ未インストール時はbad_fold/nice_fold判定がfold_unknownにフォールバック

---

## 12. 今後の実装候補（メモ）

- classify結果のWebビューアでハンドを直接閲覧・編集
- NoAPI PDFへのクイック統計グラフ埋め込み
- 複数セッションの累積レポート生成
