# ポーカーGTO 分析システム 仕様書

**バージョン:** 7.2
**最終更新:** 2026-04-27
**リポジトリ:** https://github.com/9p96d9/GTO-
**本番URL (AWS):** http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com
**旧URL (Railway・5月15日停止予定):** https://gto-production.up.railway.app

---

## 開発フェーズ ステータス

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1 | Firebase基盤構築 | ✅ 完了 |
| Phase 2 | Chrome拡張機能（スクレイプ→Firebase保存） | ✅ 完了 |
| Phase 3 | セッション一覧・解析フロー | ✅ 完了 |
| Phase 4 | ユーザー機能アップグレード（複数選択解析・テキスト保存） | ✅ 完了 |
| Phase 5 | 管理者ダッシュボード（ユーザー一覧・KPI・Firestoreクォータ監視） | ✅ 完了 |
| Phase 6 | 仕上げ・UX改善 | ⬜ 未着手 |
| Phase 7 | リアルタイムハンドログ自動取得 | ✅ 完了 |
| Phase 8 | 解析結果の永続化（Firestore保存）＋履歴表示 | ✅ 完了 |
| Phase 9 | 拡張機能UX改善（設定・バッジ・非干渉通知） | ✅ 完了 |
| Phase 10 | Web出力リデザイン（白背景・アコーディオン・可変表示） | ✅ 完了 |
| Phase 11 | 対戦相手統計DB ＋ SNS共有 | ⬜ 未着手 |
| Phase 12 | 解析カート & AI解析インライン表示（Groq/Gemini BYOK） | ✅ 完了 |
| Phase 13 | AI解析品質向上（analyze2.py・detailモード・explainモード） | ✅ 完了 |
| Phase 14 | server.py リファクタ（routes/ / pipelines.py / state.py 分割） | ✅ 完了 |
| **Phase 15** | UI/UX改善・Groq統合・トークン見積もり・ソート | ✅ 完了（15-5のみ動画待ち） |
| **Phase 16** | AI解析表示改善（スートカラーリング・ストリート別BET額） | ✅ 完了 |
| **Phase 17** | ランディング・セッションページ リデザイン（claude.ai/design活用） | ✅ 完了（ランディングのみ） |
| **Phase 20a** | セッション解析履歴 削除機能 ＋ Firestore転送量削減（フィールドマスク） | ✅ 完了 |
| **Phase 20b** | 3D可視化ページ `/3d_view/{job_id}`（Three.js 4タブ） | ✅ 完了 |
| **Phase 20c** | ドリルパネルリッチ化・バグ修正・UX polish | 🔄 進行中 |
| **Phase 18** | Railway → AWS 移行（ECS Fargate・IAM・VPC・ALB・Secrets Manager） | ✅ 完了（Railway は5月15日停止予定） |
| **Phase 19** | Firebase → PostgreSQL 移行 ＋ アドミンアナリティクスダッシュボード | ⬜ 計画中 |

---

## 1. システム概要

T4ポーカーサイトのハンドログをリアルタイム自動取得し、GTO観点の分析を行うWebアプリケーション。Chrome拡張機能でプレイ中にWebSocket通信を傍受し自動でFirestoreへ蓄積、ボタン1つでWeb画面に結果を表示する。

### モード一覧

| モード | 説明 | Gemini API |
|---|---|---|
| **リアルタイム解析（メイン）** | 拡張機能自動取得 → hand_converter → classify → Web結果画面 | 不要 |
| **classifyモード（レガシー）** | テキストアップロード → parse → classify → Web結果画面 | 不要 |
| **NoAPI PDF** | 分類結果からAPIなしでPDFを生成（Web結果画面からオプション） | 不要 |
| **解析カート → AI解析** | 結果画面で気になったハンドをカートに追加 → 選択ハンドのみAI解析（Groq優先・Geminiフォールバック）→ 同ページに結果を追記 | 必要（Firestoreに暗号化保存） |
| **AI込みPDF** | AI解析結果を含む結果画面をまとめてPDF化 | 不要（解析済みデータを使用） |

### 画面構成

| URL | 内容 |
|---|---|
| `/` | ランディングページ（拡張機能ダウンロード・4ステップ利用案内） |
| `/sessions` | セッション画面（ログイン必須）・解析ボタン・解析履歴 |
| `/classify_result/{job_id}` | 解析結果WebUI（白背景・アコーディオン・可変表示） |
| `/legacy` | 旧テキストアップロード解析（後方互換） |

### フロー図

```
[T4プレイ中]
    │ interceptor.js（MAIN world）がWebSocket傍受
    │ ハンド終了 / フォールド離脱時: content.js → background.js → POST /api/hands/realtime
    ▼
Firestore: users/{uid}/hands/{handId}

[セッション画面: ⚡ 解析ボタン]
    │ POST /api/hands/analyze
    ▼
run_classify_pipeline_from_json（バックグラウンド）
    ├── hand_converter.py → data/{job_id}.json
    ├── classify.py       → data/{job_id}_classified.json
    └── Firestore: users/{uid}/analyses/{job_id} に保存

→ /classify_result/{job_id}（サーバー再起動後はFirestoreから復元）
```

---

## 2. アーキテクチャ

### 技術スタック

| レイヤー | 技術 |
|---|---|
| Webフレームワーク | FastAPI + uvicorn |
| 言語 | Python 3.11 / Node.js 20 |
| AI | Groq llama-3.3-70b（優先・BYOK）/ Gemini 2.5 Flash（フォールバック・レガシー互換） |
| 手役評価 | treys ライブラリ（classify.py で使用） |
| PDF生成 | puppeteer（Chromium内蔵） |
| リアルタイム通信 | SSE（Server-Sent Events） |
| 認証・DB | Firebase Auth（Google）/ Firestore |
| ブラウザ拡張 | Chrome拡張機能（MV3） |
| ホスティング | AWS ECS Fargate（Docker、GitHub Actions自動デプロイ） |
| CI/CD | GitHub Actions（ECRプッシュ → タスク定義更新 → ECSデプロイ） |
| シークレット管理 | AWS Secrets Manager（`gto/production`） |
| ロードバランサー | AWS ALB（gto-alb、ap-northeast-1） |

### ファイル構成

```
GTO-/
├── server.py                   # FastAPI アプリ初期化・ミドルウェア・起動のみ（~150行）
├── state.py                    # グローバル変数（jobs, event_queues 等）
├── pipelines.py                # run_classify_pipeline_from_json 等のパイプライン関数
├── routes/
│   ├── pages.py                # 現役画面（/ /sessions /login /classify_result /classify_progress /progress /error /report /pdf /download 等）
│   ├── api.py                  # /api/hands/* /api/analyses/* /api/sessions/* /api/user/settings
│   └── cart.py                 # /api/cart/*（Phase 12）
├── html/
│   └── pages.py                # HTML生成ラッパー関数（全ページをJinja2テンプレートに外出し済み）
├── templates/
│   ├── classify_result.html    # 解析結果画面（フッターに3D可視化ボタン）
│   ├── 3d_view.html            # 3D可視化（Three.js 4タブ: 3Dバー/サンキー/バブル/時系列）ドリルパネルにハンド詳細（カード・アクション履歴）表示
│   ├── landing.html            # トップページ
│   ├── upload.html             # 手動アップロード（/legacy）
│   ├── classify_progress.html  # 解析進捗
│   ├── progress.html           # PDF生成進捗
│   ├── report.html             # PDFビューア
│   ├── error.html              # エラー画面
│   ├── dashboard.html          # クイック解析ダッシュボード
│   ├── login.html              # ログイン
│   ├── sessions.html           # セッション一覧（解析履歴削除ボタン付き）
│   └── restore.html            # Firestore復元中
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── hand_converter.py       # fastFoldTableState JSON → parse.py互換JSON変換
│   ├── analyze2.py             # Groq/Gemini両対応・detailモード/explainモード（現用）
│   ├── generate_noapilist.js   # NoAPI PDFレポート生成
│   └── firebase_utils.py       # Firebase Admin SDK ユーティリティ
├── extension/                  # Chrome拡張機能（MV3）
│   ├── manifest.json
│   ├── popup.html / popup.js   # ポップアップUI
│   ├── background.js           # Service Worker（Firebase Auth管理・自動解析トリガー）
│   ├── interceptor.js          # WebSocket傍受（MAIN world）
│   ├── content.js              # CustomEventをbackground.jsに転送
│   └── icons/
└── static/
    └── css_test.html           # CSSモック確認用
```

> **Phase 14 完了（2026-04-14）:** server.py（3900行）を上記構成に分割。server.py は45行に縮小。
> **2026-04-20:** html/pages.py のすべての HTML を templates/ 配下の Jinja2 テンプレートに外出し完了。routes/legacy.py と scripts/analyze.py を削除。

---

## 3. パイプライン詳細

### 3-1. parse.py（レガシー）

**CLI:** `python scripts/parse.py <input.txt> <output.json> [--hero-name <名前>]`

- `===...===` + `ハンドN/M` 行でハンドを分割
- Hero判定: `--hero-name` 指定 → `Guest`/`Weq*` パターン → 最多登場プレイヤー
- 3BETポット判定: PFで Raise 2回以上

### 3-2. classify.py

```
postflopなし:
  3BETポット: 勝ち→hero_aggression_won（赤・needs_api）/ 負け→fold_unknown（赤・needs_api）
  通常ポット → preflop_only

postflopあり:
  ショーダウンあり:
    勝ち+Hero最終アグレッサー → value_success（青）  ※旧: value_or_bluff_success
    勝ち+相手最終アグレッサー → bluff_catch（青）
    負け+Hero最終アグレッサー → bluff_failed（青）
    負け+相手最終アグレッサー → call_lost（青）
  ショーダウンなし:
    Hero勝ち → hero_aggression_won（赤・needs_api）
    Hero負け: treysで判定可 → bad_fold / nice_fold（赤） / 判定不能 → fold_unknown（赤・needs_api）
```

> **命名変更メモ:** ショーダウンしている時点でヒーローのカードは表になっており、ブラフではない。
> `value_or_bluff_success` → `value_success` に修正済み（Phase 12対応時にFirestoreテストデータも削除）

### 3-3. hand_converter.py

`fastFoldTableState`（Firestoreの`hand_json`）を`parse.py`出力形式に変換する。

**フォールド離脱（fastFoldTableRemoved）時の補完ロジック:**
- `interceptor.js` が `seats[mySeatIndex].cards` と `buttonPosition` からカード・ポジションを退避
- `dispatchWithHeroFallback()` でハンド送信前に `handResults` へ注入

---

## 4. APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | ランディングページ |
| GET | `/legacy` | 旧テキストアップロード画面 |
| POST | `/upload` | テキストファイル受信 → classifyパイプライン開始 |
| GET | `/classify_progress/{job_id}` | 分類進捗画面（SSE接続） |
| GET | `/classify_result/{job_id}` | 分類結果Web画面（Firestoreから復元対応） |
| GET | `/3d_view/{job_id}` | 3D可視化画面（Three.js・4タブ・認証不要） |
| POST | `/generate_pdf/{job_id}` | NoAPI PDF生成 |
| POST | `/generate_pdf/{job_id}?include_ai=true` | AI結果込みPDF生成（Phase 12） |
| ~~POST~~ | ~~`/start_ai/{job_id}`~~ | ~~全ハンドAI解析+PDF（廃止・Phase 12で削除）~~ |
| GET | `/sessions` | セッション画面（ログイン必須） |
| GET | `/api/firebase-config` | Firebase public設定 |
| GET | `/api/extension.zip` | 拡張機能ZIPダウンロード |
| GET | `/api/hands/stats` | 蓄積ハンド件数・期間（Bearer認証） |
| POST | `/api/hands/realtime` | ハンド1件即時保存（Bearer認証） |
| POST | `/api/hands/analyze` | hands取得→変換→classifyパイプライン（Bearer認証） |
| GET | `/api/analyses` | 解析履歴一覧（最新20件・フィールドマスク適用、Bearer認証） |
| DELETE | `/api/analyses/{job_id}` | 解析ドキュメント削除（Bearer認証） |
| POST | `/api/analyses/{job_id}/restore` | Firestoreから解析結果を復元（Bearer認証） |
| GET | `/api/sessions` | セッション一覧JSON（Bearer認証） |
| DELETE | `/api/sessions/{session_id}` | セッション削除 |
| POST | `/api/sessions/analyze-multi` | 複数セッション結合解析 |
| POST | `/api/sessions/download-text` | セッションtxtダウンロード |
| GET | `/api/cart/{job_id}` | アクティブカートの取得（Phase 12）✅実装済 |
| POST | `/api/cart/{job_id}/hands` | カートへのハンド追加/削除（Phase 12）✅実装済 |
| ~~POST~~ | ~~`/api/cart/{job_id}/save`~~ | ~~カートを名前付き保存（廃止）~~ |
| ~~GET~~ | ~~`/api/carts`~~ | ~~保存済みカート一覧（廃止）~~ |
| ~~GET~~ | ~~`/api/carts/{cart_id}`~~ | ~~保存済みカート取得（廃止）~~ |
| POST | `/api/cart/{job_id}/analyze` | カート内ハンドをAI解析・SSEで順次返却（Phase 12）✅実装済 |
| POST | `/api/cart/{job_id}/explain` | 1ハンドの詳細解説（explainモード）をオンデマンド生成（Phase 13）✅実装済 |
| GET | `/api/user/settings` | ユーザー設定取得（APIキー含む）（Phase 12）✅実装済 |
| PUT | `/api/user/settings` | ユーザー設定更新（Phase 12）✅実装済 |

---

## 5. Firebase / Firestoreデータ構造

```
users/{uid}/hands/{handId}
  ├── hand_json:    object       # fastFoldTableState生データ
  ├── captured_at:  string       # ISO8601（拡張機能側時刻）
  └── saved_at:     timestamp    # Firestore保存日時（ソート・フィルタに使用）

users/{uid}/analyses/{job_id}   # Phase 8: 解析結果永続化
  ├── job_id:       string
  ├── created_at:   timestamp
  ├── hand_count / blue_count / red_count / pf_count: number
  ├── categories:   object       # カテゴリ別内訳
  ├── classified_snapshot: string  # classified.json の gzip+base64 圧縮（圧縮後900KB上限、超過時は省略）
  ├── snapshot_encoding:  string  # "gzip_b64"（旧レコードはフィールドなし＝生JSON）
  ├── has_snapshot:       bool    # 一覧取得API（get_analyses）が付与する派生フラグ（Firestoreには非保存）
  ├── active_cart:  [42, 17, 88]   # アクティブカートのhand_number配列（Phase 12）
  └── gemini_results: {           # AI解析結果（Phase 12）※フィールド名はGroq使用時も維持（後方互換）
        "42": {
          text:        string      # detailモード解析テキスト（GTO評価:\n詳細:\n... 形式）
          category:    string      # fold_unknown など
          explain:     string      # explainモード長文解説（📖詳細解説ボタンで生成・Phase 13）
          analyzed_at: timestamp
        }
      }

users/{uid}/carts/{cartId}      # 名前付き保存カート（Phase 12）
  ├── job_id:       string
  ├── name:         string        # ユーザーが付けた名前（デフォルト: 日付）
  ├── created_at:   timestamp
  ├── hand_numbers: [42, 17, 88]
  └── status:       "saved" | "analyzed"

users/{uid}/settings/gemini     # ユーザー設定（Phase 12）
  ├── encrypted_api_key: string  # Groq（gsk_...）またはGeminiキーを保存。ドキュメント名"gemini"は後方互換のため変更しない
  └── needs_api_auto_cart: bool  # デフォルト: true

users/{uid}/sessions/{sessionId}   # レガシー手動アップロード
  ├── raw_text, filename, hand_count
  ├── uploaded_at, status, result_pdf
  └── job_id

users/{uid}/opponents/{playerName}  # Phase 11: 未実装
  ├── hand_count, vpip, pfr, three_bet_pct
  ├── cbet_flop, fold_to_3bet, sd_winrate
  └── last_seen
```

---

## 6. Chrome拡張機能

**拡張機能ID:** `ilkbcfenghigefpfjohppfjodahhoiif`
**OAuthクライアントID:** `615725442966-l1k8rgi5m43stim6ellgj8e36s8hfn6l.apps.googleusercontent.com`

| ファイル | 役割 |
|---|---|
| `background.js` | Service Worker。Firebase Auth管理・HAND_COMPLETE受信・自動解析トリガー |
| `interceptor.js` | WebSocket傍受（MAIN world）。フォールド時のカード退避ロジック含む |
| `content.js` | CustomEventをbackground.jsに転送（ISOLATED world） |
| `popup.html/js` | ポップアップUI（設定もインライン表示・Phase 9で刷新） |

**自動解析トリガー:** `handCounter` が閾値（デフォルト100）に達したら `/api/hands/analyze` をバックグラウンドで実行。完了後はバッジ通知（Phase 9実装）。

---

## 7. 環境変数

| 変数名 | 説明 | 必須 |
|---|---|---|
| `GROQ_API_KEY` | サーバー側デフォルトGroq APIキー（`gsk_`で始まる）。ユーザーBYOKが優先。設定するとGroq優先 | 任意 |
| `GEMINI_API_KEY` | サーバー側デフォルトGemini APIキー。GROQ_API_KEY未設定時のフォールバック | 任意 |
| `PORT` | サーバーポート（デフォルト: 5000） | 任意 |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | FirebaseサービスアカウントキーJSON | Firebase機能を使う場合 |
| `FIREBASE_API_KEY` | Firebase Web API Key | Firebase機能を使う場合 |
| `FIREBASE_AUTH_DOMAIN` | `{project-id}.firebaseapp.com` | Firebase機能を使う場合 |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID | Firebase機能を使う場合 |

**ローカル開発:** `.env` ファイルに上記を記載し `uvicorn server:app --reload` で起動可能。`.env` は `.gitignore` 対象。

---

## 8. Phase 9: 拡張機能UX改善（完了）

### 合意済み仕様

| 項目 | 仕様 |
|---|---|
| 自動解析 | バックグラウンド実行・タブを開かない |
| 解析完了通知 | バッジ（✓緑）＋ブラウザ通知ポップアップ |
| プレイ時間通知 | ブラウザ通知（30分/60分/120分、設定可） |
| 履歴リンク | ポップアップに最新3件表示 |
| 今すぐ解析ボタン | ポップアップに配置（/sessions不要） |

### 設定（ポップアップ内インライン、options.html は不要）

| 設定名 | 選択肢 | デフォルト |
|---|---|---|
| 自動解析トリガー | オフ / 50 / 100 / 200 / 500手 | 100手 |
| 自動解析モード | オフ / バックグラウンド | バックグラウンド |
| プレイ時間通知 | なし / 30分 / 60分 / 120分 | なし |

### ポップアップUI（刷新後）

```
🃏 PokerGTO
─────────────────
⏱ 1h 23m  🃏 87手
[⚡ 今すぐ解析]
─────────────────
📊 最新の結果（最大3件）
  2026/04/12 50手 →
  2026/04/11 100手 →
─────────────────
[⚙ 設定]  [ログアウト]
```

### バッジ・通知仕様

- 解析完了: バッジ「✓」（緑）+ `chrome.notifications` でブラウザ通知
- プレイ時間超過: `chrome.notifications` のみ（バッジは解析通知と混在させない）
- 通知クリック: `chrome.windows.create({ type: "popup", focused: false })` で小窓表示（ゲーム画面フォーカス非侵害）

---

## 9. Phase 12: 解析カート & AI解析インライン表示（実装中）

### 概要（✅ 完了）

`classify_result/{job_id}` 画面上で気になったハンドだけを「解析カート」に入れ、
選択分のみ AI解析（Groq優先・Geminiフォールバック）。結果は同じページの最上部に追記表示される。
全ハンドまとめてAI解析する `/start_ai/{job_id}` は**廃止**。

### 結果ページのレイアウト（AI解析後）

```
┌──────────────────────────────────────────────────────┐
│ ヘッダー（収支サマリー）                               │
│ [🛒 カート N件]  [⬇ PDFにする（分類のみ）]            │
│                  [⬇ PDFにする（AI込み）] ← 解析済み時のみ│
├──────────────────────────────────────────────────────┤
│ 🤖 AI解析結果（N手）  ← 解析済みの場合のみ表示・最上部  │
│  H42 fold_unknown | BTN | AA                         │
│  「このハンドは〇〇の理由でフォールドが...」            │
│  H17 hero_aggression_won | CO                        │
│  「ターンでのベットサイズは...」                       │
├──────────────────────────────────────────────────────┤
│ ① 青線ハンド詳細（変更なし）                           │
├──────────────────────────────────────────────────────┤
│ ② 赤線ハンド詳細（変更なし）                           │
├──────────────────────────────────────────────────────┤
│ ③ 全ハンド一覧（変更なし。AI解析済みハンドに🤖マーク）  │
└──────────────────────────────────────────────────────┘
```

### カートUI

- 各ハンドカードに `[+🛒]` ボタン（ホバーで表示）
- `preflop_only` ハンドはカート追加不可（分析素材不足）
- 画面右下に浮くカートアイコン（バッジで件数表示）
- クリックでカートドロワー（右からスライド）

```
┌─────────────────────────────────┐
│ 解析カート                  🛒 3│
│─────────────────────────────────│
│ H42  [BTN] A♠K♦   +12.30bb [✕] │
│ H17  [CO]  7♥8♥   -5.00bb  [✕] │
│ H88  [SB]  Q♣Q♦   -20.33bb [✕] │
│─────────────────────────────────│
│ [⚡ 解析を実行（準備中）]        │
└─────────────────────────────────┘
```

カートアイテム表示: ハンド番号 / ポジション / ハンド / 収支（緑=プラス, 赤=マイナス）

### カートの仕様

| 操作 | 動作 |
|---|---|
| ハンド追加/削除 | Firestore に即時反映（自動保存）→ タブを閉じても消えない |
| カートは job 単位 | 1 job_id につき 1 アクティブカート |
| 名前保存・読み込み | **廃止**（別 classify のカートに意味がないため） |

### needs_api ハンドの自動カート追加

- ユーザー設定 `needs_api_auto_cart`（Firestoreに保存）
- デフォルト: **ON**（結果ページを開いた時点で自動追加）
- ON/OFF はカートドロワー内またはユーザー設定から切替

### Gemini解析の仕様

- エンドポイント: `POST /api/cart/{job_id}/analyze`（SSE）
- バッチサイズ: `BATCH_SIZE = 10`（カートが少数の場合は1バッチで完結）
- バッチ完了ごとにSSEで結果を返却 → ページの「🤖 AI解析結果」セクションに順次追記
- 再解析: 同じハンドを再送した場合は上書き
- 解析結果は `analyses/{job_id}/gemini_results` に保存（ページリロード後も復元）
- 推定解析時間の表示: **廃止**（実測後に再設計）

### Gemini APIキーの保管

- ユーザーが `/api/user/settings` に登録
- Firestore の `users/{uid}/settings/gemini/encrypted_api_key` に保存
- Firebaseのセキュリティルールで本人のみアクセス可（Firebase転送・保存時暗号化を利用）
- 表示はマスキング（末尾4文字のみ）

### PDFの仕様

| ボタン | 対象 | 生成内容 |
|---|---|---|
| ⬇ PDFにする（分類のみ） | 常に表示 | 青・赤・全ハンド（現状と同じ） |
| ⬇ PDFにする（AI込み） | AI解析済み時のみ | 🤖 AI解析セクション ＋ 青・赤・全ハンド |

### 廃止するもの

| 対象 | 対応 |
|---|---|
| `POST /start_ai/{job_id}` | 削除 |
| `scripts/generate.js`（AI PDF生成） | 削除またはAI込みPDF生成に統合 |
| classify_progress画面の推定解析時間表示 | 削除 |
| `value_or_bluff_success` カテゴリ名 | `value_success` に修正・Firestoreテストデータ削除 |

### 実装順序（推奨）

```
1. classify.py: value_success にリネーム + Firestoreテストデータ削除  ✅
2. /start_ai 廃止・推定解析時間削除                                   ✅
3. result画面: カートUI実装（追加/削除/ドロワー）                      ✅
4. /api/cart エンドポイント群（Firestore連携）                         ✅
5. needs_api 自動カート追加（サーバー設定連携）                        ✅
6. /api/user/settings（APIキー保存・カートドロワー内UI）               ✅
7. /api/cart/{job_id}/analyze（SSE + バッチGemini）                   ✅
8. result画面: 🤖 AI解析セクション表示（JS動的描画・リロード復元）     ✅
9. classify_result を Jinja2 外部テンプレートに移行                   ✅
10. PDF: AI込みバージョン対応                                         ⬜未着手
11. AI解析品質向上（Phase 13）                                        ⬜未着手
```

### 2026-04-14 作業ログ（苦労点）

- **Jinja2移行**: server.py内の1300行f-string HTMLを外部テンプレートに移行。`\n` がf-string内で改行に展開されJSの `split('\n')` が壊れていた根本原因を修正。Dockerfileに `COPY templates/` を追記し忘れてRailwayで500エラーが続いた。
- **Geminiモデル名問題**: 元々 `gemini-2.5-flash` だったのを誤って `gemini-2.0-flash` 等に変更し無駄なトラブルを招いた。`gemini-2.0-flash` は新規ユーザー向け廃止済み。現在は `gemini-2.5-flash` を使用。
- **APIキー入力欄**: flexレイアウトで入力欄が幅ゼロに潰れていた。縦並びレイアウトに修正。

---

## 10. Phase 13: AI解析品質向上（✅ 完了 2026-04-15）

### 完了内容

| 項目 | 実装内容 |
|---|---|
| `analyze2.py` 新規作成 | Groq/Gemini両対応・OpenAI互換・503リトライ・JSONフォールバック |
| detailモード | 数値なし・`rep`フィールド（Hero表現レンジ）・systemプロンプト使用 |
| explainモード | 長文教育解説（400〜1200文字）・`SYSTEM_PROMPT_EXPLAIN`・temperature=0.5・max_tokens=2000 |
| `POST /api/cart/{job_id}/explain` | 1ハンドオンデマンド生成・Firestore保存 |
| UI: 📖詳細解説ボタン | AI解析結果カードに追加・トグル表示・ページ再読み込み時復元 |
| UI: 進捗バー | SSEバッチ完了ごとに N/M手・経過秒数を表示 |
| AI解析結果カード刷新 | GTO評価バッジ・ヒーロー情報ヘッダー・折りたたみセクション |

### analyze2.py の解析モード

| MODE | 説明 | 出力形式 | 用途 |
|---|---|---|---|
| `standard` | 数値あり・旧来互換 | JSON | レガシー |
| `detail` | 数値なし・rep追加 | JSON | カートAI解析（メイン） |
| `explain` | 長文教育解説 | free-text | 📖詳細解説ボタン（オンデマンド） |

### BYOKキー判定ロジック

```
api_key が "gsk_" で始まる → Groq (llama-3.3-70b-versatile)
それ以外                   → Gemini (gemini-2.5-flash)
```

### PDF AI込みバージョン（Phase 15 に移行）

Phase 12 からの持ち越し。Phase 15 で実装予定。

---

## 11. Phase 15: UI/UX改善・Groq統合・ソート（✅ 完了）

### 実装順序

```
15-1: Gemini表記統一 + インデックスGroq案内更新   ✅ 完了
15-2: カートにトークン見積もり表示               ✅ 完了
15-3: ハンド情報増量 + AI解析ハイブリッド表示     ✅ 完了（data-hnum-oppはdata-oppで代替）
15-4: フィルター/ソート機能                      ✅ 完了（2026-04-15）
15-5: インデックス拡張機能インストール動画案内     ← 外部作業（動画制作）後
```

---

### 15-1. Gemini表記統一 + インデックスGroq案内更新

**変更対象と内容:**

| 対象 | 変更前 | 変更後 |
|---|---|---|
| classify_result.html カートドロワー | 「Gemini APIキー」 | 「AIキー (Groq推奨)」 |
| classify_result.html カートボタン | 「⚡ Gemini解析を実行」 | 「⚡ AI解析を実行」 |
| classify_result.html APIキー説明文 | Geminiキーの説明 | Groqキー取得方法に更新 |
| `/` ランディングページ | Geminiキーの案内 | GroqキーURLと取得ステップに更新 |

**⚠️ 変更しないもの（互換性維持）:**
- Firestore: `users/{uid}/settings/gemini` ドキュメントパス
- Firestore: `analyses/{job_id}/gemini_results` フィールド名
- `analyze2.py`: PROVIDERS dict（Groq/Gemini両対応を内部維持）

---

### 15-2. カートにトークン見積もり表示

**表示場所:** カートドロワーのフッター（解析実行ボタンの上）

**表示内容:**
```
📊 トークン見積もり（目安）
  簡易解析   N手 × 370 tok ≈ X tok
  詳細解説   1手ごとに ≈ 1,700 tok
⚡ Groq無料枠: 約14,400 tok/分
  → [余裕で収まります / 複数回に分けて実行推奨]
注: 実際の使用量はGroqダッシュボードで確認できます
```

**実装方針:**
- フロントエンド定数計算のみ（バックエンド変更なし）
- トークン定数: `DETAIL_TOKENS_PER_HAND = 370` / `EXPLAIN_TOKENS_PER_HAND = 1700`
- Groq無料枠定数: `GROQ_FREE_TPM = 14400`
- カート件数変更時にリアルタイム更新

---

### 15-3. ハンド情報増量 + AI解析ハイブリッド表示

#### 現状の問題
- AI解析セクションのカードに対戦相手情報・ストリート別アクションがない
- 解析を読みながら該当ハンドを探すのが手間

#### 設計方針
- **AI解析セクション（上部）**: 既存を維持しつつ情報を増量（ハンドの概要を俯瞰）
- **hand-card（下部）**: AI結果を折りたたみで内包（詳細をhand-card近くで読める）
- どちらも同じデータを使うため重複ではなく**役割分担**

#### 15-3-A. html/pages.py の hand-card に data 属性追加

```python
# 追加するdata属性
data-3bet="1"          # is_3bet_pot が True の場合（ソート機能でも使用）
data-hnum-opp="CO:AhKd BTN:Th9c"  # 相手pos:cards をスペース区切り（表示・sort用）
```

追加場所: `data_attrs` 変数の末尾

また各 hand-card の末尾（`hand-card-body` の後）に AI結果プレースホルダーを追加:
```html
<div id="ai-inline-{hnum}" class="ai-inline-section"></div>
```

#### 15-3-B. AI解析セクション（renderAiSection）の表示増量

追加表示項目:
- 対戦相手情報: `data-hnum-opp` から取得してポジション・カードを表示
- ストリート別アクション: hand-card の `.hand-card-body` からDOMコピー

#### 15-3-C. hand-card内AI結果（新規）

- `renderAiInHandCard(hnum, result)`: プレースホルダーに折りたたみAIカードを注入
- 初期状態: 折りたたんで非表示（ `[AI解析 ▼]` トグルボタン）
- SSE streaming 時: `renderAiSection()` と `renderAiInHandCard()` を両方呼ぶ
- ページロード時: `_geminiResults` から復元

#### 15-3-D. classify_result.html の肥大化対策

現状17,000トークン超。機能追加前に外部JS分離を実施:
- `static/classify_result.js` を新規作成し、JSロジックをすべて移動
- HTML側は `<script src="/static/classify_result.js">` のみに
- ルーティング: `routes/pages.py` で `/static` を mount するか、既存の static mount を確認

---

### 15-4. フィルター/ソート機能

**実装方針:** フロントエンドのみ（classify.py・バックエンド変更なし）

**UIレイアウト:**
```
[全て] [青線のみ] [赤線のみ] [AI解析済み]  ← フィルター
[損益▼] [損益▲] [ポジション] [3BETのみ]  ← ソート/フィルター
```

**ソート仕様:**

| ソートキー | data属性 | 備考 |
|---|---|---|
| 損益（大→小） | `data-pl-num` | デフォルト（現状） |
| 損益（小→大） | `data-pl-num` | |
| ポジション | `data-pos` | EP→MP→LP→ブラインド順 |

**フィルター仕様:**

| フィルター | 条件 | data属性 |
|---|---|---|
| 3BETのみ | `data-3bet="1"` | 15-3-Aで追加 |
| AI解析済みのみ | `_geminiResults[hnum]` 存在 | |
| ポジション指定 | `data-pos` が選択値に一致 | |

**スコープ:** 各セクション内（青線・赤線・全ハンドそれぞれ）でソート。青赤混在ソートは将来検討。

---

### 15-5. インデックス拡張機能インストール案内改善

**chrome://extensions リンク問題:**
- `<a href="chrome://extensions">` はブラウザセキュリティで無効
- → コピーボタンを実装: `navigator.clipboard.writeText('chrome://extensions')`

**動画案内:**
- 別タブで開く（`target="_blank" rel="noopener"`）
- 動画はYouTubeまたは外部ホスティング（スクショより動画優先: スペース効率・操作が直感的）
- 動画URLは実装時に確定

---

## 12. Phase 11: 対戦相手統計DB & SNS共有（未着手）

### 11-1. 対戦相手統計DB
- hand_jsonから自動算出（VPIP/PFR/3BET%/CBet%/フォールドto3BET/SD勝率）→ Firestore保存
- 算出タイミング: `/api/hands/analyze` 完了後にバックグラウンドで自動集計

### 11-2. T4スタッツ傍受の調査手順（必要時に実施）

```javascript
if (typeof raw === "string" && raw.startsWith("42")) {
  const payload = JSON.parse(raw.slice(2));
  if (/stat|player|profile|info/i.test(payload[0])) {
    console.log("[T4 Stats Event]", payload[0], payload[1]);
  }
}
```

### 11-3. SNS（X）共有

- `twitter.com/intent/tweet?text=...` 方式（X APIキー不要）
- 共有内容: hero_position / hero_cards / アクション / 損益（相手カード・名前・テーブルIDは除外）

---

## 11. ユーザー管理ページ（将来フェーズ・設計スタブ）

**URL:** `/settings`（ログイン必須）

将来的に一元管理する場所として設計を想定しておく。

| セクション | 内容 |
|---|---|
| 👤 アカウント | メール・ログイン方法・表示名 |
| 🔬 解析設定 | needs_api自動カート追加 ON/OFF |
| 🧩 拡張機能設定 | 自動解析トリガー・閾値・プレイ時間通知（ポップアップと同期） |
| 🔑 APIキー管理 | Gemini APIキー登録・変更・削除（マスキング表示） |
| 🛒 カート履歴 | 名前付き保存カードの一覧・読み込み・削除 |
| 🗄 データ管理 | 蓄積ハンド数・期間確認 / 全ハンド削除（確認ダイアログ付き） |

---

## 13. Phase 16: AI解析表示改善（⬜ 次回）

### 16-1. スートカラーリング

カード表示（♠♣♥♦）にCSSでスートの色を付ける。

**対象箇所:** `templates/classify_result.html` のカード表示部分、AI解析結果カード

**カラー仕様:**

| スート | 色 |
|---|---|
| ♠ スペード | #ccc（グレーホワイト） |
| ♣ クラブ | #4caf93（グリーン） |
| ♥ ハート | #e94560（レッド） |
| ♦ ダイヤ | #5b9bd5（ブルー） |

**実装方針:** JSでカード文字列をパースし `<span class="suit-s">♠</span>` 等に変換。CSSで色付け。

### 16-2. ストリート別BET額表示

AI解析カードのストリート別アクション表示に `amount_bb` を追加する。

**データ:** `hand_converter.py` が生成するJSONにすでに `amount_bb` フィールドが存在（Bet/Raise/Call時）。
表示側に渡すだけでよく、バックエンド変更不要。

**表示例:**
```
[FLOP] A♥ K♦ 2♣
  Hero (BTN): Bet 4.5bb
  Villain (BB): Raise 14.0bb
  Hero (BTN): Call 14.0bb
```

---

## 14. Phase 17: ランディング・セッションページ リデザイン（⬜ 次回）

### 概要

- **対象:** `/`（ランディング）と `/sessions`（セッション画面）のみ
- **解析結果画面 `/classify_result` は変更しない**
- **ツール:** claude.ai/design でデザイン案を生成 → HTML/CSSに移植
- **スタック変更なし:** Jinja2/Python生成HTML を維持（React移行はPhase 19以降）

### 現状デザインの課題

| 場所 | 課題 |
|---|---|
| ランディング | ヒーローセクションが地味。ポーカー感が薄い |
| ランディング | ステップ説明が縦並びで長い |
| セッション | 数字（ハンド数）が大きいだけで情報密度が低い |
| 共通 | フォントが `Meiryo` でモダン感が乏しい |

### デザイン方針

- **カラーパレット維持:** `#0a0e1a`（背景）/ `#e94560`（アクセント）/ `#4caf93`（緑）
- グラデーション・グラスモーフィズム・アニメーション追加でポーカー感を演出
- モバイルファースト（スマホでも使いやすく）

---

## 15. Phase 18: Railway → AWS 移行（✅ 完了）

### 目的

- 本番インフラをRailwayからAWSに移行
- AWSとセキュリティの実践的な学習

### 採用サービス構成（案）

| AWSサービス | 用途 |
|---|---|
| **ECS Fargate** | Dockerコンテナ実行（Railwayと同じ感覚で移行できる） |
| **ECR** | Dockerイメージ管理 |
| **ALB** | ロードバランサー + HTTPS終端 |
| **ACM** | SSL証明書（無料） |
| **Secrets Manager** | 環境変数の安全な管理（現在の.env相当） |
| **VPC + セキュリティグループ** | ネットワーク分離・ポート制御 |
| **IAM** | 最小権限ロール設計 |

### 学習できるセキュリティ項目

- IAMロールと最小権限の原則
- VPCサブネット設計（パブリック/プライベート）
- セキュリティグループによるポート制御
- HTTPS強制・HTTPリダイレクト
- Secrets Managerによる秘密情報管理
- CloudWatch Logsによる監視・アラート

---

## 16. Phase 19: Firebase → PostgreSQL 移行 ＋ アドミンダッシュボード（🔄 進行中）

### 目的

- FirebaseからPostgreSQLへ移行してDBの学習
- 複数ユーザーのデータを分析・可視化するアドミンアナリティクスダッシュボード構築
- React化（フロントエンドをAPIサーバー + React SPAに分離）

### 基本方針（Firebase温存）

- **`USE_POSTGRES=true/false`** 環境変数で切り替え（AWSはtrue・費用超過時はfalseに戻すだけ）
- **Firebase Auth（verify_id_token）は常にFirebaseのまま**（認証層は変えない）
- **`firebase_utils.py`は一切変更しない**
- `db.py`（ラッパー）を追加し、routesのimportをfirebase_utils→dbに変えるだけ

### ファイル構成変化

```
scripts/
  firebase_utils.py   ← 触らない
  postgres_utils.py   ← NEW（firebase_utilsと同一シグネチャで実装）
  db.py               ← NEW（USE_POSTGRESで振り分け・verify_id_tokenはfirebase固定）

alembic/              ← NEW（マイグレーション管理）
  versions/
  env.py
alembic.ini
```

### テーブル設計

```sql
users         (id SERIAL PK, firebase_uid VARCHAR UNIQUE NOT NULL,
               email VARCHAR, created_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ)

hands         (id SERIAL PK, user_id INT FK→users.id NOT NULL,
               hand_id VARCHAR UNIQUE NOT NULL,
               hand_json JSONB NOT NULL,
               captured_at TIMESTAMPTZ, saved_at TIMESTAMPTZ NOT NULL)

analyses      (id SERIAL PK, user_id INT FK→users.id NOT NULL,
               job_id VARCHAR UNIQUE NOT NULL,
               created_at TIMESTAMPTZ NOT NULL,
               hand_count INT, blue_count INT, red_count INT, pf_count INT,
               categories JSONB, classified_snapshot TEXT,
               snapshot_encoding VARCHAR, active_cart JSONB,
               deleted_at TIMESTAMPTZ)

ai_results    (id SERIAL PK, analysis_id INT FK→analyses.id NOT NULL,
               hand_number INT NOT NULL,
               ai_text TEXT, analyzed_at TIMESTAMPTZ)

carts         (id SERIAL PK, user_id INT FK→users.id NOT NULL,
               cart_id VARCHAR UNIQUE NOT NULL,
               job_id VARCHAR NOT NULL,
               name VARCHAR, hand_numbers JSONB,
               created_at TIMESTAMPTZ NOT NULL)

user_settings (id SERIAL PK, user_id INT FK→users.id UNIQUE NOT NULL,
               encrypted_api_key TEXT,
               needs_api_auto_cart BOOL DEFAULT FALSE,
               updated_at TIMESTAMPTZ)
```

**INDEX:**
```sql
CREATE INDEX ON hands(user_id, saved_at DESC);
CREATE INDEX ON analyses(user_id, created_at DESC);
CREATE INDEX ON ai_results(analysis_id);
CREATE INDEX ON carts(user_id, created_at DESC);
```

### スキーマ設計方針（AIの典型的失敗と対策）

| 失敗パターン | 対策 |
|---|---|
| インデックス漏れ | `user_id`, `saved_at`, `job_id` に必ずINDEX |
| JSONまるごと保存 | 検索・集計が必要なフィールドは専用カラムに出す |
| マイグレーション未設計 | Alembicを導入しスキーマ変更を管理 |
| `TIMESTAMP` と `TIMESTAMPTZ` 混在 | 全て `TIMESTAMPTZ`（UTC）で統一 |
| NOT NULL 制約漏れ | 論理的に必須のカラムには必ず付ける |
| 外部キー制約なし | 孤立レコード防止のため必ず設定 |
| ソフトデリート未設計 | `deleted_at TIMESTAMPTZ NULL` で論理削除 |

### 実装ステップ

| # | 作業 | 状態 |
|---|---|---|
| 19-1 | RDS PostgreSQL作成（t3.micro・VPC内・gto-rds-sg） | ⬜ |
| 19-2 | requirements.txtに`sqlalchemy psycopg2-binary alembic`追加 | ⬜ |
| 19-3 | Alembicセットアップ・Initialマイグレーション作成 | ⬜ |
| 19-4 | `scripts/postgres_utils.py`実装 | ⬜ |
| 19-5 | `scripts/db.py`作成（USE_POSTGRESフラグで振り分け） | ⬜ |
| 19-6 | routes 4ファイルのimportをfirebase_utils→dbに変更 | ⬜ |
| 19-7 | Secrets Managerに`DATABASE_URL`・`USE_POSTGRES`追加 | ⬜ |
| 19-8 | ECSタスク定義更新（新環境変数を参照） | ⬜ |
| 19-9 | 動作確認（false→Firebaseの既存動作確認・true→PostgreSQL確認） | ⬜ |

### アドミンアナリティクスダッシュボード

管理者が複数ユーザーのポーカーデータを集計・可視化する画面（Phase 5の本格実装・PostgreSQL移行後）。

**可視化項目（案）:**

| 指標 | グラフ種別 |
|---|---|
| ユーザー別ハンド蓄積数 | 棒グラフ |
| 日別新規ハンド数 | 折れ線グラフ |
| カテゴリ分布（blue/red/preflop比率） | ドーナツチャート |
| AI解析利用率 | 数値カード |
| ポジション別損益分布 | ヒートマップ |

---

## 18. Phase 5: 管理者ダッシュボード（✅ 完了）

### 概要

Firebase Admin SDK を使い、全ユーザーのデータをサーバーサイドで集計・表示する管理者専用画面。  
現行の Firestore / Railway スタックで実装し、Phase 19（PostgreSQL 移行）時にデータ取得層のみ差し替える。

**URL:** `/admin`（ログイン必須・管理者UIDのみアクセス可）

---

### 5-1. 認証・アクセス制御

**方式:** 環境変数 `ADMIN_UID` に管理者の Firebase UID を設定し、JWT デコード後に照合。

```python
# routes/pages.py または routes/api.py 内
ADMIN_UID = os.environ.get("ADMIN_UID", "")

async def require_admin(request: Request) -> str:
    uid = await get_uid_from_request(request)   # 既存のJWT検証関数を流用
    if uid != ADMIN_UID:
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    return uid
```

- Railway の環境変数に自分の UID を設定するだけで完結
- UID は Firebase コンソール → Authentication → ユーザー一覧で確認可能
- 将来的に複数管理者が必要になったら `ADMIN_UIDS` をカンマ区切りに拡張

**セキュリティ注意:**
- `/admin` ルートは `require_admin` で必ずガードする
- API エンドポイント `/api/admin/*` も同様に全て認証必須
- フロントエンドで管理者判定するのは NG（サーバー側で必ずガード）

---

### 5-2. 画面構成

```
/admin
├── サマリーカード（数値KPI 4枚）
├── ユーザーテーブル（全ユーザー一覧）
└── Firestoreクォータ使用状況（読み取り数カード）
```

#### サマリーカード（4枚）

| カード | 内容 | 取得方法 |
|---|---|---|
| 総ユーザー数 | Firebase Auth の全ユーザー数 | `auth.list_users()` |
| 総ハンド数 | 全ユーザーの hands ドキュメント数合計 | `collection_group("hands").count()` |
| 総解析数 | 全ユーザーの analyses ドキュメント数合計 | `collection_group("analyses").count()` |
| アクティブユーザー（7日） | 直近7日以内に saved_at があるユーザー数 | collection_group + where |

#### ユーザーテーブル

| カラム | 内容 |
|---|---|
| メール | Firebase Auth より |
| 最終ログイン | Firebase Auth の `last_sign_in_time` |
| ハンド数 | `users/{uid}/hands` の count |
| 解析数 | `users/{uid}/analyses` の count |
| APIキー設定 | `users/{uid}/settings/gemini` の有無 |
| PFスコア平均 | 直近20件の analyses から `pf_count/hand_count` 平均 |

ユーザー数が多い場合は Firebase Auth の `page_token` でページネーション。

---

### 5-3. API エンドポイント

```
GET /api/admin/summary     # サマリーカード用集計
GET /api/admin/users       # ユーザーテーブル用一覧
```

**レスポンス例（summary）:**
```json
{
  "total_users": 12,
  "total_hands": 8430,
  "total_analyses": 87,
  "active_users_7d": 5,
  "fetched_at": "2026-04-23T10:00:00+00:00"
}
```

**レスポンス例（users）:**
```json
{
  "users": [
    {
      "uid": "abc123",
      "email": "user@example.com",
      "last_login": "2026-04-23T09:00:00+00:00",
      "hand_count": 1200,
      "analysis_count": 14,
      "has_api_key": true,
      "avg_pf_score": 84.2
    }
  ]
}
```

---

### 5-4. ファイル構成

```
routes/
  admin.py           # /admin ページルート・/api/admin/* エンドポイント
templates/
  admin.html         # 管理者ダッシュボード画面（ランディングデザイン統一）
scripts/
  firebase_utils.py  # get_admin_summary(), get_admin_users() を追加
```

`server.py` に `from routes.admin import router as admin_router` を追加してマウント。

---

### 5-5. Firestore クォータ設計

**問題:** 全ユーザーを横断するクエリはコストが高い。

| クエリ | 読み取りコスト |
|---|---|
| `collection_group("hands").count()` | **0**（Aggregate API は読み取り消費なし） |
| `collection_group("analyses").count()` | **0**（同上） |
| ユーザーごとの `analyses` 直近20件 | ユーザー数 × 最大20 |
| PFスコア平均の計算 | ユーザー数 × 最大20（上と同クエリで賄う） |

**対策:**
- `count()` Aggregate API を使う（既に `get_hands_stats` で実装済み）
- ユーザー詳細（ハンド数・解析数）は `count()` で取るため読み取りゼロ
- PFスコアは `/api/admin/users` 取得時に1リクエストにまとめて計算（N+1防止）
- 管理者ページは自分しか使わないためリアルタイム集計で十分（キャッシュ不要）

**ユーザー50人時の1回の `/admin` 表示コスト概算:**

| 操作 | 読み取り |
|---|---|
| `auth.list_users()` | 0（Auth API、Firestore 無関係） |
| `hands.count()` × 50人 | 0（Aggregate API） |
| `analyses.count()` × 50人 | 0（Aggregate API） |
| `analyses` 直近20件 × 50人（PFスコア用） | 最大 1,000 |

→ **50人規模で1回1,000読み取り**。無料枠50,000/日 = 管理者ページを1日50回開いても安全圏。

---

### 5-6. UI 設計

- ランディング・sessions と同じデザインシステム（Inter / Space Grotesk / CSS変数）
- ナビは sessions と同一（PokerGTO ロゴ + ログアウト）
- テーブルはソート可能（ハンド数降順がデフォルト）
- メールアドレスは直接表示（管理者のみ閲覧）
- レスポンシブ対応（テーブルはスクロール）

---

### 5-7. 実装順序

1. `ADMIN_UID` 環境変数設定（Railway）
2. `firebase_utils.py` に `get_admin_summary()` / `get_admin_users()` 追加
3. `routes/admin.py` 作成（ページルート + API エンドポイント）
4. `server.py` にルーター登録
5. `templates/admin.html` 作成
6. SPEC.md 更新 + コミット

---

## 17. Phase 20: バグ修正・仕上げ（⬜ 計画中）

既知バグの洗い出しと修正。現行機能のエッジケース対応。

### 実施済み改善（Phase 20 先行対応）

| 日付 | 内容 |
|---|---|
| 2026-04-20 | 分類カテゴリ内のソートをハンド番号昇順（H1, H2...）に変更 |
| 2026-04-20 | classified_snapshot を gzip+base64 圧縮保存（圧縮前比5〜10倍のハンド数を格納可能に） |
| 2026-04-20 | sessions 解析履歴で has_snapshot=false の場合「⚠ 再表示不可」を表示しリンクを非表示化 |

### バグ調査方針

1. 全ユーザーフローを通しで実行して動作確認
2. ブラウザコンソールエラーの確認
3. Railway ログの確認（本番エラー）
4. 拡張機能のService Workerログ確認

---

## 12. 注意事項

- `data/` フォルダのJSONは削除しない
- `.env` / サービスアカウントキーJSONはGitにコミットしない
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- Firestoreの読み取りクォータ: 50,000回/日
- `order_by("saved_at")` を使う（`captured_at` は一部欠落ドキュメントがある）
- `classify_result/{job_id}` はサーバー再起動後もFirestoreから自動復元（Phase 8実装済み）
