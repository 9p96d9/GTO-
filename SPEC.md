# ポーカーGTO レポート自動生成システム 仕様書

**バージョン:** 1.0
**最終更新:** 2026-03-13
**ステータス:** 本番稼働中

---

## 1. システム概要

T4ポーカーサイトのハンド履歴テキストをアップロードするだけで、GTO観点の分析レポートPDFを自動生成するWebアプリケーション。

### 主な特徴
- ブラウザからtxtファイルをアップロードするだけで完結
- Gemini APIによるAI分析（BYOK方式）
- リアルタイム進捗表示（3ステップ）
- PDF をブラウザで表示 + ダウンロード
- クラウド（Railway）でインターネット公開済み

---

## 2. アーキテクチャ

```
ブラウザ
  │
  │ HTTP (multipart/form-data)
  ▼
FastAPI サーバー (server.py)
  │
  ├── スレッド1: レスポンス返却（即時）
  │     └── /progress/{job_id} へリダイレクト
  │
  └── スレッド2: パイプライン実行（バックグラウンド）
        ├── parse.py     ← ハンド履歴 → JSON
        ├── analyze.py   ← JSON → GTO評価（Gemini API）
        └── generate.js  ← JSON → PDF（puppeteer）
```

### 技術スタック

| レイヤー | 技術 |
|---|---|
| Webフレームワーク | FastAPI + uvicorn |
| 言語 | Python 3.13 / Node.js 20 |
| AI | Google Gemini 2.5 Flash |
| PDF生成 | puppeteer (Chromium) |
| ホスティング | Railway (Docker) |
| コンテナ | Docker (python:3.11-slim + Node.js 20) |

---

## 3. 処理パイプライン

### Step 1: parse.py
- 入力: `input/upload.txt`（T4ハンド履歴）
- 出力: `data/upload.json`
- 処理内容:
  - ハンドごとに分割・構造化
  - ヒーロー判定（`Guest`, `Guest\d*`, `Weq\*+`）
  - カード正規化（U+FE0E/FE0F 絵文字変体セレクタ除去）
  - ポジション・アクション・結果を抽出

### Step 2: analyze.py
- 入力: `data/upload.json`
- 出力: 同ファイルに `gto_analysis` フィールドを追記
- 処理内容:
  - バッチ処理（10ハンド/リクエスト）
  - Gemini 2.5 Flash でGTO評価
  - 評価済みハンドスキップ（`analyzed: true` フラグ）
  - 429レートリミット自動リトライ（最大3回、5秒待機）

### Step 3: generate.js
- 入力: `data/upload.json`
- 出力: `output/GTO_Report_{最古日付}_{最新日付}.pdf`
- 処理内容:
  - HTMLレポート生成 → puppeteerでPDF変換
  - A4縦向き、余白10mm
  - Gemini APIでセクション4・7を生成

---

## 4. レポート構成（PDFセクション）

| セクション | 内容 |
|---|---|
| 1 | サマリー（総損益・ハンド数・VPIP/PFR等） |
| 2 | ポジション別成績表 |
| 3 | 全ハンド一覧（12カラム、6pt） |
| 4 | 改善すべきプレイ（Gemini生成） |
| 5 | GTO評価カテゴリ別（❌ミス赤・⚠️改善橙） |
| 6 | ストレングスハンド分析（Gemini生成） |
| 7 | 対戦相手プロファイルカード（累積集計・Gemini生成） |

---

## 5. APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | アップロード画面 |
| POST | `/upload` | ファイル受信・ジョブ開始 |
| GET | `/progress/{job_id}` | 進捗表示画面 |
| GET | `/status/{job_id}` | ジョブ状態JSON |
| GET | `/error/{job_id}` | エラー詳細画面 |
| GET | `/report/{name}` | PDFビューア画面 |
| GET | `/pdf/{name}` | PDF inline配信 |
| GET | `/download/{name}` | PDF ダウンロード |

### /status/{job_id} レスポンス例
```json
{ "step": 2, "status": "running", "pdf": "", "log": "" }
{ "step": 0, "status": "done",    "pdf": "GTO_Report_2026-03-13.pdf", "log": "" }
{ "step": 0, "status": "error",   "pdf": "", "log": "エラー内容..." }
```

---

## 6. BYOK（Bring Your Own Key）

- ユーザーが自分のGemini APIキーをアップロード画面で入力
- キーはフォーム送信時のみ使用、サーバーに保存しない
- `.env` に `GEMINI_API_KEY` があればデフォルト値として表示
- RailwayのEnvironment Variablesに設定することも可能
- Gemini無料枠: 1日1500リクエスト（50ハンド分析は余裕）
- 取得先: https://aistudio.google.com/app/apikey

---

## 7. ファイル構成

```
GTO-/
├── server.py          # FastAPI Webサーバー（メインエントリ）
├── run.py             # ローカルCLI実行用（バッチ処理）
├── run.bat            # Windowsダブルクリック実行用
├── Dockerfile         # Railway/本番用コンテナ定義
├── docker-compose.yml # ローカルDocker用（省略可）
├── requirements.txt   # Python依存パッケージ
├── package.json       # Node.js依存パッケージ
├── .env               # APIキー（Gitに上げない）
├── scripts/
│   ├── parse.py       # ハンド履歴パーサー
│   ├── analyze.py     # Gemini GTO分析
│   └── generate.js    # PDF生成（puppeteer）
├── input/             # アップロードされたtxtの一時置き場
│   └── done/          # 処理済みtxt
├── output/            # 生成されたPDF
└── data/              # 解析済みJSON（累積・削除禁止）
    ├── upload.json
    └── opponents_summary.json  ← セクション7累積データ
```

---

## 8. 環境変数

| 変数名 | 説明 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini APIキー | BYOKで代替可 |
| `PORT` | サーバーポート（デフォルト: 5000） | 任意 |

---

## 9. デプロイ情報

### 本番環境（Railway）
- URL: `https://gto-production.up.railway.app`
- リポジトリ: `https://github.com/9p96d9/GTO-.git`
- ブランチ: `main`
- 自動デプロイ: `main` へのpushで自動ビルド・デプロイ
- プラン: トライアル（30日/$5）

### ローカル実行
```bash
cd c:\Users\user\Desktop\GTO-
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

## 10. 注意事項

- `data/` フォルダのJSONは削除しない（セクション7累積集計に使用）
- `.env` はGitにコミットしない（`.gitignore` 設定済み）
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- 処理時間: 約2〜3分（Gemini API分析がボトルネック）
