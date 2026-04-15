# ポーカーGTO 分析システム 仕様書

**バージョン:** 6.3
**最終更新:** 2026-04-15
**リポジトリ:** https://github.com/9p96d9/GTO-
**本番URL:** https://gto-production.up.railway.app

---

## 開発フェーズ ステータス

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1 | Firebase基盤構築 | ✅ 完了 |
| Phase 2 | Chrome拡張機能（スクレイプ→Firebase保存） | ✅ 完了 |
| Phase 3 | セッション一覧・解析フロー | ✅ 完了 |
| Phase 4 | ユーザー機能アップグレード（複数選択解析・テキスト保存） | ✅ 完了 |
| Phase 5 | 管理者ダッシュボード | ⬜ 未着手 |
| Phase 6 | 仕上げ・UX改善 | ⬜ 未着手 |
| Phase 7 | リアルタイムハンドログ自動取得 | ✅ 完了 |
| Phase 8 | 解析結果の永続化（Firestore保存）＋履歴表示 | ✅ 完了 |
| Phase 9 | 拡張機能UX改善（設定・バッジ・非干渉通知） | ✅ 完了 |
| Phase 10 | Web出力リデザイン（白背景・アコーディオン・可変表示） | ✅ 完了 |
| Phase 11 | 対戦相手統計DB ＋ SNS共有 | ⬜ 未着手 |
| Phase 12 | 解析カート & AI解析インライン表示（Groq/Gemini BYOK） | ✅ 完了 |
| Phase 13 | AI解析品質向上（analyze2.py・detailモード・explainモード） | ✅ 完了 |
| Phase 14 | server.py リファクタ（routes/ / pipelines.py / state.py 分割） | ✅ 完了 |
| **Phase 15** | UI/UX改善・Groq統合・トークン見積もり・ソート | ⬜ 次回 |

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
| ホスティング | Railway（Docker、mainブランチ自動デプロイ） |

### ファイル構成

```
GTO-/
├── server.py                   # FastAPI アプリ初期化・ミドルウェア・起動のみ（~150行）
├── state.py                    # グローバル変数（jobs, event_queues 等）
├── pipelines.py                # run_classify_pipeline_from_json 等のパイプライン関数
├── routes/
│   ├── pages.py                # 現役画面（/sessions /login /classify_result /classify_progress）
│   ├── api.py                  # /api/hands/* /api/analyses/* /api/sessions/* /api/user/settings
│   ├── cart.py                 # /api/cart/*（Phase 12）
│   └── legacy.py               # 旧フロー（/upload /progress /report /dashboard 等）将来削除予定
├── html/
│   └── pages.py                # classify_result_page() 等の Python HTML 生成関数（Jinja2 本格移行まで）
├── templates/
│   └── classify_result.html    # 解析結果画面テンプレート（Jinja2）
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── hand_converter.py       # fastFoldTableState JSON → parse.py互換JSON変換
│   ├── analyze.py              # Gemini GTO分析（レガシー・フォールバック用に存置）
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
> `classify_result_page()` の Jinja2 本格移行（HTML 組み立てをテンプレート側に移す）は Phase 13 の表示改善と合わせて実施予定。

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
| POST | `/generate_pdf/{job_id}` | NoAPI PDF生成 |
| POST | `/generate_pdf/{job_id}?include_ai=true` | AI結果込みPDF生成（Phase 12） |
| ~~POST~~ | ~~`/start_ai/{job_id}`~~ | ~~全ハンドAI解析+PDF（廃止・Phase 12で削除）~~ |
| GET | `/sessions` | セッション画面（ログイン必須） |
| GET | `/api/firebase-config` | Firebase public設定 |
| GET | `/api/extension.zip` | 拡張機能ZIPダウンロード |
| GET | `/api/hands/stats` | 蓄積ハンド件数・期間（Bearer認証） |
| POST | `/api/hands/realtime` | ハンド1件即時保存（Bearer認証） |
| POST | `/api/hands/analyze` | hands取得→変換→classifyパイプライン（Bearer認証） |
| GET | `/api/analyses` | 解析履歴一覧（最新20件、Bearer認証） |
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
  ├── classified_snapshot: string  # classified.json（900KB上限、超過時は省略）
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

## 11. Phase 15: UI/UX改善・Groq統合・ソート（⬜ 次回）

### 実装順序

```
15-1: Gemini表記統一 + インデックスGroq案内更新   ← 小規模・先行
15-2: カートにトークン見積もり表示               ← 小規模・先行
15-3: ハンド情報増量 + AI解析ハイブリッド表示     ← 中規模
15-4: フィルター/ソート機能                      ← 中規模
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

## 12. 注意事項

- `data/` フォルダのJSONは削除しない
- `.env` / サービスアカウントキーJSONはGitにコミットしない
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- Firestoreの読み取りクォータ: 50,000回/日
- `order_by("saved_at")` を使う（`captured_at` は一部欠落ドキュメントがある）
- `classify_result/{job_id}` はサーバー再起動後もFirestoreから自動復元（Phase 8実装済み）
