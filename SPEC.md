# ポーカーGTO 分析システム 仕様書

**バージョン:** 3.2  
**最終更新:** 2026-04-06  
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
| **Phase 5** | **管理者ダッシュボード** | ⬜ 未着手 |
| Phase 6 | 仕上げ・UX改善 | ⬜ 未着手 |
| **Phase 7** | **リアルタイムハンドログ自動取得** | ✅ 完了 |

---

## 1. システム概要

T4ポーカーサイトのハンドログをリアルタイム自動取得し、GTO観点の分析を行うWebアプリケーション。Chrome拡張機能でプレイ中にWebSocket通信を傍受し自動でFirestoreへ蓄積、ボタン1つでWeb画面に結果を表示する。

### モード一覧

| モード | 説明 | Gemini API |
|---|---|---|
| **リアルタイム解析（メイン）** | 拡張機能自動取得 → hand_converter → classify → Web結果画面 | 不要 |
| **classifyモード（レガシー）** | テキストアップロード → parse → classify → Web結果画面 | 不要 |
| **NoAPI PDF** | 分類結果からAPIなしでPDFを生成（Web結果画面からオプション） | 不要 |
| **AI PDF** | 分類結果にGemini分析を追加してPDFを生成（Web結果画面からオプション） | 必要（BYOK） |
| **クイック解析** | 統計ダッシュボードのみ（PDFなし、即時） | 不要 |

### 画面構成

| URL | 内容 |
|---|---|
| `/` | ランディングページ（拡張機能ダウンロード・4ステップ利用案内） |
| `/sessions` | セッション一覧（ログイン必須）・リアルタイム解析ボタン |
| `/classify_result/{job_id}` | 解析結果WebUI（全プレイヤーのホールカード・アクションフロー表示）|
| `/legacy` | 旧テキストアップロード解析（後方互換） |

### フロー図（リアルタイム解析がメイン）

```
[T4プレイ中]
    │ Chrome拡張（interceptor.js: MAIN world）がWebSocket傍受
    │ ハンド終了時: content.js → background.js → POST /api/hands/realtime
    ▼
Firestore: users/{uid}/hands/{handId}

[セッション画面: ⚡ リアルタイム解析ボタン]
    │ POST /api/hands/analyze
    ▼
[server.py] → run_classify_pipeline_from_json（バックグラウンド）
    │
    ├── hand_converter.py  → data/realtime_*.json（parse.py互換形式に変換）
    ├── classify.py        → data/realtime_*_classified.json
    │
    └── /classify_result/{job_id} に遷移（Web結果画面）
               │
     ┌─────────┴──────────┐
     │                    │
POST /generate_pdf    POST /start_ai
（オプション）        （オプション）
     │                    │
NoAPI_Report_*.pdf   GTO_Report_*.pdf
```

### レガシーフロー（/legacy）

```
[ブラウザ: テキストファイルアップロード]
    │ POST /upload
    ▼
run_classify_pipeline（parse.py → classify.py）→ /classify_result/{job_id}
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
├── requirements.txt            # Python依存
├── package.json                # Node.js依存（puppeteer）
├── bookmarklet.js              # 旧: T4サイトからハンド履歴を直接送信するブックマークレット（後方互換）
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── hand_converter.py       # fastFoldTableState JSON → parse.py互換JSON変換（Phase 7）
│   ├── analyze.py              # Gemini GTO分析（JSON → JSON + gto_analysis）
│   ├── generate.js             # AI PDFレポート生成（puppeteer）
│   ├── generate_noapilist.js   # NoAPI PDFレポート生成（puppeteer）
│   ├── quick_analyzer.py       # クイック統計計算（API不要・即時）
│   └── firebase_utils.py       # Firebase Admin SDK ユーティリティ（Firestore CRUD / idToken検証）
├── extension/                  # Chrome拡張機能（MV3）v2.1.0
│   ├── manifest.json           # 拡張機能設定（oauth2 client_id・key含む）
│   ├── popup.html / popup.js   # ポップアップUI（ログイン・スクレイプ送信）
│   ├── background.js           # Service Worker（Firebase Auth管理・HAND_COMPLETE受信）
│   ├── interceptor.js          # WebSocket傍受（MAIN world）Socket.IOイベント検知（Phase 7）
│   ├── content.js              # CustomEventをbackground.jsに転送（ISOLATED world）
│   └── icons/                  # アイコン画像（16/48/128px）
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

---

### 3-3. analyze.py / generate.js / generate_noapilist.js / quick_analyzer.py

（変更なし。v2.0仕様を継続）

---

## 4. APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | ランディングページ（拡張機能ダウンロード・利用案内） |
| GET | `/legacy` | 旧テキストアップロード画面（後方互換） |
| POST | `/upload` | テキストファイル受信 → classifyパイプライン開始（/legacyから） |
| GET | `/classify_progress/{job_id}` | 分類進捗画面（SSE接続） |
| GET | `/classify_result/{job_id}` | 分類結果Web画面（ホールカード・アクションフロー表示） |
| POST | `/generate_pdf/{job_id}` | NoAPI PDFを生成（オプション） |
| POST | `/start_ai/{job_id}` | AI分析+AI PDFを生成（api_key フォームパラメータ、オプション） |
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
| **Firebase連携（要 FIREBASE_SERVICE_ACCOUNT_JSON）** | | |
| GET | `/login` | Googleログイン画面（Firebase Auth） |
| GET | `/sessions` | セッション一覧画面（ログイン必須）・リアルタイム解析ボタン |
| GET | `/api/firebase-config` | フロントエンド向けFirebase public設定を返す |
| GET | `/api/extension.zip` | 拡張機能ZIPダウンロード |
| POST | `/api/upload-from-extension` | Chrome拡張機能からセッション保存（Bearer認証） |
| GET | `/api/sessions` | ログインユーザーのセッション一覧JSON（Bearer認証） |
| DELETE | `/api/sessions/{session_id}` | セッション削除（Bearer認証） |
| POST | `/api/sessions/{session_id}/analyze` | Firestoreのセッションをclassifyパイプラインに流す（Bearer認証） |
| POST | `/api/sessions/analyze-multi` | 複数セッションを結合してclassifyパイプラインに流す（Bearer認証） |
| POST | `/api/sessions/download-text` | 複数セッションのraw_textを結合してtxtダウンロード（Bearer認証） |
| **Phase 7: リアルタイム** | | |
| POST | `/api/hands/realtime` | 拡張機能からハンド1件を即時保存（Bearer認証） |
| POST | `/api/hands/analyze` | Firestoreの全handsを取得→変換→classifyパイプライン実行（Bearer認証） |
| **Phase 5で追加予定** | | |
| GET | `/admin` | 管理者ダッシュボード（adminロールのみ） |
| POST | `/api/admin/set-claim` | ユーザーにadminクレームを付与（adminロールのみ） |

---

## 5. Firebase / Firestoreデータ構造

### Firestoreコレクション設計

```
users/{uid}/sessions/{sessionId}
  ├── raw_text:    string        # T4ハンドログ全文（レガシー手動アップロード用）
  ├── filename:    string        # 元ファイル名（例: t4_hands_20260403.txt）
  ├── hand_count:  number        # ハンド数
  ├── uploaded_at: timestamp     # アップロード日時（UTC）
  ├── status:      string        # "pending" | "analyzing" | "done" | "error"
  ├── result_pdf:  string        # 生成PDFファイル名（旧フロー用、基本は空）
  └── job_id:      string        # classify_result WebページのjobId（/classify_result/{job_id}で参照）

users/{uid}/hands/{handId}       # Phase 7: リアルタイム自動取得
  ├── hand_json:    object        # fastFoldTableStateの生データ
  │     ├── tableId:         string
  │     ├── actionHistory:   string[]
  │     ├── handResults:     object[]  # 全プレイヤー情報・ホールカード含む
  │     ├── seats:           object[]
  │     ├── communityCards:  string[]
  │     ├── mySeatIndex:     number
  │     └── isHandInProgress: boolean
  ├── captured_at:  string        # ISO8601（拡張機能側の時刻）
  └── saved_at:     timestamp     # Firestore保存日時（UTC）
```

### セキュリティルール（現在）

```javascript
rules_version = '2';
service cloud.firestore.beta {
  match /databases/{database}/documents {
    match /users/{uid}/sessions/{sessionId} {
      allow read, write: if request.auth != null && request.auth.uid == uid;
    }
  }
}
```

### セキュリティルール（Phase 5完了後）

```javascript
rules_version = '2';
service cloud.firestore.beta {
  match /databases/{database}/documents {
    match /users/{uid}/sessions/{sessionId} {
      allow read, write: if request.auth != null && request.auth.uid == uid;
    }
    match /admins/{uid} {
      allow read: if request.auth != null && request.auth.uid == uid;
      allow write: if false; // Admin SDKのみ書き込み可
    }
  }
}
```

---

## 6. Chrome拡張機能

| ファイル | 役割 |
|---|---|
| `manifest.json` | MV3設定 v2.1.0。`oauth2.client_id`・`key`（ID固定）。2つのcontent_scripts（MAIN/ISOLATED） |
| `background.js` | Service Worker。Firebase Auth管理 + `HAND_COMPLETE`メッセージ受信→サーバーPOST |
| `popup.html/js` | ポップアップUI。ログイン状態表示・手動スクレイプ送信ボタン |
| `interceptor.js` | **Phase 7新規**。`world: "MAIN"`でWebSocket傍受。Socket.IOイベントを検知しCustomEvent発火 |
| `content.js` | `t4_hand_complete` CustomEventを受け取りbackground.jsに転送（ISOLATED world） |

**拡張機能ID（固定）:** `ilkbcfenghigefpfjohppfjodahhoiif`  
**OAuthクライアントID:** `615725442966-l1k8rgi5m43stim6ellgj8e36s8hfn6l.apps.googleusercontent.com`

**対応ドメイン:**
- `https://*.tenfourpoker.com/*`
- `https://*.tenfour-poker.com/*`
- `https://*.t4poker.com/*`

**リアルタイム自動取得フロー（Phase 7 - メイン）:**
```
[T4プレイ中]
  interceptor.js（MAIN world）: window.WebSocket をオーバーライド
       ↓ fastFoldTableState（isHandInProgress=false）または
         fastFoldTableRemoved（フォールド離脱）を検知
  CustomEvent: t4_hand_complete
       ↓
  content.js（ISOLATED）: chrome.runtime.sendMessage({ type: 'HAND_COMPLETE' })
       ↓
  background.js: POST /api/hands/realtime（Bearer: idToken）
       ↓
  server.py → Firestore: users/{uid}/hands/{tableId}_{captured_at}
```

**手動スクレイプフロー（レガシー）:**
```
[T4ブックマーク一覧]
  content.js: ハンドカードをクリック → テキスト収集
       ↓ popup.js経由で POST /api/upload-from-extension
       ↓
  Firestore: users/{uid}/sessions/{sessionId}
       ↓ /sessions → 解析ボタン → run_classify_pipeline
```

---

## 7. 環境変数

| 変数名 | 説明 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini APIキー | AIモードのみ。BYOKで代替可 |
| `PORT` | サーバーポート（デフォルト: 5000） | 任意 |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | FirebaseサービスアカウントキーJSON（全体） | Firebase機能を使う場合 |
| `FIREBASE_API_KEY` | Firebase Web API Key | Firebase機能を使う場合 |
| `FIREBASE_AUTH_DOMAIN` | `{project-id}.firebaseapp.com` | Firebase機能を使う場合 |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID | Firebase機能を使う場合 |

---

## 8. デプロイ情報

### 本番環境（Railway）
- URL: `https://gto-production.up.railway.app`
- リポジトリ: `https://github.com/9p96d9/GTO-.git`
- ブランチ: `main`（masterブランチは削除済み）
- 自動デプロイ: `main` へのpushで自動ビルド・デプロイ

---

## 9. Phase 4: ユーザー機能アップグレード（✅ 完了）

### 概要

セッション一覧画面（`/sessions`）にチェックボックスを追加し、複数のセッションを選択して以下の操作を可能にする。

### 9-1. 複数セッション結合解析

**UI:**
- 各セッション行にチェックボックスを追加
- 「選択したセッションを解析」ボタン（1件以上選択時に有効化）

**動作:**
1. ユーザーが複数セッションのチェックボックスを選択
2. 「選択したセッションを解析」ボタンを押す
3. `POST /api/sessions/analyze-multi` に選択した `session_id[]` を送信
4. サーバーがFirestoreから各セッションの `raw_text` を取得して結合
5. 結合テキストを既存の classifyパイプライン（parse → classify）に流す
6. 結果は `/classify_result/{job_id}` で表示（既存フロー流用）

**サーバー側エンドポイント:**
```
POST /api/sessions/analyze-multi
Header: Authorization: Bearer {idToken}
Body: { session_ids: ["id1", "id2", ...] }

処理:
  - idTokenでuid取得
  - 各session_idのraw_textをFirestoreから取得（本人のデータのみ）
  - raw_textを改行で結合
  - INPUT_DIR に結合txtを書き出し
  - run_classify_pipeline を起動
  - { job_id, progress_url } を返す
```

### 9-2. テキストのローカル保存

**UI:**
- 各セッション行にチェックボックスを追加（9-1と共用）
- 「選択したテキストをダウンロード」ボタン（1件以上選択時に有効化）

**動作:**
1. ユーザーが複数セッションのチェックボックスを選択
2. 「テキストをダウンロード」ボタンを押す
3. `POST /api/sessions/download-text` に選択した `session_id[]` を送信
4. サーバーが各 `raw_text` を取得して結合したtxtファイルをレスポンス
5. ブラウザが自動でローカルに保存（`t4_hands_combined_{日付}.txt`）

**サーバー側エンドポイント:**
```
POST /api/sessions/download-text
Header: Authorization: Bearer {idToken}
Body: { session_ids: ["id1", "id2", ...] }

処理:
  - idTokenでuid取得
  - 各session_idのraw_textをFirestoreから取得（本人のデータのみ）
  - raw_textを結合
  - Content-Disposition: attachment; filename="t4_hands_combined_{YYYYMMDD}.txt"
  - text/plain で返す
```

### 9-3. セッション一覧UIの変更点

```
現状:
  [日付] [ハンド数] [ステータス] [解析] [削除]

変更後:
  [☐] [日付] [ハンド数] [ステータス] [解析] [削除]
  
  ── 一括操作バー（1件以上チェック時に表示）──
  [ 選択したセッションを解析 ]  [ テキストをダウンロード ]
```

---

## 10. Phase 5: 管理者ダッシュボード（未着手）

### 概要

特定ユーザーに `admin` ロールを付与し、全ユーザーのデータを閲覧・操作できる管理画面を追加する。

### 10-1. adminロール管理

- Firebase Custom Claims で `admin: true` を付与
- サーバー側の `verify_id_token()` で `decoded["admin"]` を確認
- 初期adminは手動でFirebase Admin SDKから設定

### 10-2. 管理者ダッシュボード（GET /admin）

**表示内容:**
- ユーザー一覧（UID・メール・セッション数・最終アップロード日）
- 全ユーザーのセッション一覧（ユーザーで絞り込み可）
- 任意のセッションを解析にかけるボタン
- ユーザーへのadmin権限付与/剥奪

### 10-3. Firestoreスキーマ追加

```
admins/{uid}
  └── granted_at: timestamp
```

---

## 11. Phase 6: 仕上げ・UX改善（未着手）

- セッション解析ステータスのリアルタイム更新（/sessions画面でSSE）
- opponents_summary.jsonをFirestoreに移行（Railway再起動でリセットされる問題の解消）
- PokerGTOサイトTOP画面をセッション一覧に変更
- 拡張機能からPokerGTOのセッション一覧を直接開くボタン

---

## 12. Phase 7: リアルタイムハンドログ自動取得（✅ 完了）

### 概要

プレイ中にSocket.IO通信を傍受し、ハンド終了を自動検知してFirestoreへ即時保存する。
ユーザーはプレイするだけでログが自動蓄積され、「⚡ リアルタイム解析」ボタン1つでWeb結果画面を表示できる。

### 12-1. 実装済み内容（2026-04-06 全パイプライン動作確認済み）

| 項目 | 内容 |
|---|---|
| 通信方式 | Socket.IO（`wss://game.tenfour-poker.com/socket.io/?EIO=4&transport=websocket`） |
| データ形式 | 平文JSON（難読化・暗号化なし） |
| 傍受方法 | `world: "MAIN"` Content Script（`interceptor.js`）で `window.WebSocket` をオーバーライド |
| CSP対応 | `document.createElement('script')` は T4サイトのCSPでブロックされるため `world: "MAIN"` で解決 |
| ハンド終了検知 | `fastFoldTableState` イベント + `isHandInProgress: false` |
| Fast Fold対応 | `fastFoldTableRemoved` 発火時に `_lastTableState[tableId]` を保存 |
| 重複防止 | `actionHistory` のJSON文字列をtableIdごとにメモリキャッシュして比較 |
| ホールカード | `handResults[].hand` に全プレイヤーのカードが含まれる（showdown不問） |
| 保存先 | Firestore `users/{uid}/hands/{tableId}_{captured_at}` |
| 即時POST | ハンド1件終了ごとに即時POST（ブラウザクラッシュ時のロス防止） |
| 解析パイプライン | `/api/hands/analyze` → hand_converter → classify → Web結果画面 |

### 12-2. 実装ファイル

| ファイル | 変更内容 |
|---|---|
| `extension/interceptor.js` | WebSocket傍受・イベント検知（新規、`world: "MAIN"`） |
| `extension/content.js` | CustomEventを受け取りbackground.jsに転送（ISOLATED world） |
| `extension/background.js` | `HAND_COMPLETE`メッセージを受け取り `POST /api/hands/realtime` |
| `extension/manifest.json` | v2.1.0。interceptor.js（MAIN world）・content.js（ISOLATED）の2段構成 |
| `scripts/firebase_utils.py` | `save_hand()`, `get_hands()` 追加。`update_session_status()` に `job_id` 追加 |
| `scripts/hand_converter.py` | fastFoldTableState → parse.py互換JSON変換（新規） |
| `server.py` | `POST /api/hands/realtime`, `POST /api/hands/analyze`, `run_classify_pipeline_from_json()` 追加 |

### 12-3. hand_converter.py の処理

`fastFoldTableState`（Firestoreの`hand_json`）を`parse.py`出力形式に変換する。

```
fastFoldTableState
  ├── handResults[].hand[]     → players[].hole_cards（カード変換: "As" → "A♠"）
  ├── handResults[].position   → players[].position
  ├── handResults[].profit     → players[].result_bb
  ├── mySeatIndex              → players[].is_hero
  ├── actionHistory[]          → streets（preflop/flop/turn/river）
  ├── communityCards[]         → streets.{flop,turn,river}.board
  └── seats[].isFolded         → went_to_showdown 判定
```

**3BETポット判定:** プリフロップのRaiseが2回以上  
**ショーダウン判定:** `isHandInProgress=false` かつ `seats[i].isFolded=false` が2席以上

### 12-4. リアルタイム解析パイプライン

```
POST /api/hands/analyze（Bearer認証）
  ↓
get_hands(uid, limit=500)  ← Firestore users/{uid}/hands
  ↓
convert_hands_batch()      ← hand_converter.py
  ↓ data/realtime_{job_id}.json
run_classify_pipeline_from_json()  ← parse.pyスキップ
  ↓ classify.py
  ↓ data/realtime_{job_id}_classified.json
/classify_result/{job_id}  ← 全プレイヤーのホールカード・アクションフロー表示
```

### 12-5. 結果表示（classify_result Web画面）

- 全プレイヤーのホールカードを表示（リアルタイムハンドは全員分取得可能）
- アクションフロー: `PF UTG F › HJ Raise 2.5bb › ...` 形式
- 青線（ショーダウン）/ 赤線（ノーショーダウン）分類
- カテゴリ別集計（バリュー成功・ブラフ失敗・ナイスフォールド等）
- オールインEV差表示
- PDFレポート生成はオプション（ページ下部ボタンから）

---

## 13. 注意事項

- `data/` フォルダのJSONは削除しない（opponents_summary.jsonは特に重要）
- `.env` はGitにコミットしない（`.gitignore` 設定済み）
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- classifyモード（Web結果画面）はGemini不要で高速（数秒〜十数秒）
- AIモードはGemini APIの呼び出し時間がボトルネック（約2〜3分）
- Railwayはストレージ永続化なし → JSONデータは将来的にFirestoreに移行推奨
- サービスアカウントキーJSONはGitにコミットしない（Railway環境変数で管理）
- リアルタイムハンド（`hands`コレクション）は解析のたびに全件取得（将来的に解析済みフラグ管理が必要）
- `classify_result/{job_id}` はサーバー再起動でメモリが消えるためアクセス不能になる（将来的にFirestoreへの結果保存が必要）
