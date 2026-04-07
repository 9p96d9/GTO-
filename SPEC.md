# ポーカーGTO 分析システム 仕様書

**バージョン:** 4.0  
**最終更新:** 2026-04-07  
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
| **Phase 8** | **解析結果の永続化（Firestore保存）＋累積解析** | ⬜ 未着手 |
| **Phase 9** | **拡張機能UX改善（設定・バッジ・非干渉通知）** | ⬜ 未着手 |
| **Phase 10** | **Web出力リデザイン（白背景・アコーディオン・可変表示）** | 🔄 CSS実装中 |
| **Phase 11** | **対戦相手統計DB ＋ SNS共有** | ⬜ 未着手 |

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
| `/sessions` | セッション画面（ログイン必須）・リアルタイム解析ボタン・解析履歴（Phase 8以降） |
| `/classify_result/{job_id}` | 解析結果WebUI（白背景・アコーディオン・可変表示） |
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
```

---

## 2. アーキテクチャ

### 技術スタック

| レイヤー | 技術 |
|---|---|
| Webフレームワーク | FastAPI + uvicorn |
| 言語 | Python 3.11 / Node.js 20 |
| AI | Google Gemini 2.5 Flash（オプション・BYOK） |
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
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── package.json
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── hand_converter.py       # fastFoldTableState JSON → parse.py互換JSON変換
│   ├── analyze.py              # Gemini GTO分析
│   ├── generate.js             # AI PDFレポート生成
│   ├── generate_noapilist.js   # NoAPI PDFレポート生成
│   ├── quick_analyzer.py       # クイック統計計算
│   └── firebase_utils.py       # Firebase Admin SDK ユーティリティ
├── extension/                  # Chrome拡張機能（MV3）
│   ├── manifest.json
│   ├── popup.html / popup.js
│   ├── background.js           # Service Worker（Firebase Auth管理・HAND_COMPLETE受信）
│   ├── interceptor.js          # WebSocket傍受（MAIN world）
│   ├── content.js              # CustomEventをbackground.jsに転送
│   └── icons/
├── static/
│   └── css_test.html           # Web出力 新CSSモック（開発用）
├── input/
├── output/
└── data/
```

---

## 3. パイプライン詳細

### 3-1. parse.py

**入力:** `input/upload.txt`  
**出力:** `data/upload.json`  
**CLI:** `python scripts/parse.py <input.txt> <output.json> [--hero-name <名前>]`

- `===...===` + `ハンドN/M` 行でハンドを分割
- Hero判定優先順位: `--hero-name` 指定 → `Guest`/`Weq*` パターン → 最多登場プレイヤー
- カード正規化（絵文字変体セレクタ除去）
- 3BETポット判定: PFで Raise 2回以上

### 3-2. classify.py

**入力:** `data/upload.json`  
**出力:** `data/upload_classified.json`

```
postflopなし → preflop_only

postflopあり:
  ショーダウンあり:
    勝ち + 最終アグレッサー=Hero  → value_or_bluff_success  （青）
    勝ち + 最終アグレッサー=相手  → bluff_catch             （青）
    負け + 最終アグレッサー=Hero  → bluff_failed            （青）
    負け + 最終アグレッサー=相手  → call_lost               （青）
  ショーダウンなし:
    Heroが勝ち（相手fold）        → hero_aggression_won    （赤・needs_api）
    Heroが負け（Hero fold）:
      treysで判定可:
        勝てた → bad_fold                                   （赤）
        負けてた → nice_fold                                （赤）
      判定不能 → fold_unknown                               （赤・needs_api）
```

### 3-3. hand_converter.py

`fastFoldTableState`（Firestoreの`hand_json`）を`parse.py`出力形式に変換する。

```
fastFoldTableState
  ├── handResults[].hand[]     → players[].hole_cards（"As" → "A♠"）
  ├── handResults[].position   → players[].position
  ├── handResults[].profit     → players[].result_bb
  ├── mySeatIndex              → players[].is_hero
  ├── actionHistory[]          → streets（preflop/flop/turn/river）
  ├── communityCards[]         → streets.{flop,turn,river}.board
  └── seats[].isFolded         → went_to_showdown 判定
```

---

## 4. APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | ランディングページ |
| GET | `/legacy` | 旧テキストアップロード画面 |
| POST | `/upload` | テキストファイル受信 → classifyパイプライン開始 |
| GET | `/classify_progress/{job_id}` | 分類進捗画面（SSE接続） |
| GET | `/classify_result/{job_id}` | 分類結果Web画面 |
| POST | `/generate_pdf/{job_id}` | NoAPI PDF生成 |
| POST | `/start_ai/{job_id}` | AI分析+PDF生成（BYOKキー） |
| GET | `/progress/{job_id}` | PDF生成進捗画面 |
| GET | `/stream/{job_id}` | SSEイベントストリーム |
| GET | `/status/{job_id}` | ジョブ状態JSON |
| GET | `/sessions` | セッション画面（ログイン必須） |
| GET | `/api/firebase-config` | Firebase public設定 |
| GET | `/api/extension.zip` | 拡張機能ZIPダウンロード |
| GET | `/api/hands/stats` | 蓄積ハンド件数・期間（Bearer認証） |
| POST | `/api/hands/realtime` | ハンド1件即時保存（Bearer認証） |
| POST | `/api/hands/analyze` | 全hands取得→変換→classifyパイプライン（Bearer認証） |
| GET | `/api/sessions` | セッション一覧JSON（Bearer認証） |
| DELETE | `/api/sessions/{session_id}` | セッション削除 |
| POST | `/api/sessions/{session_id}/analyze` | セッション解析 |
| POST | `/api/sessions/analyze-multi` | 複数セッション結合解析 |
| POST | `/api/sessions/download-text` | セッションtxtダウンロード |

---

## 5. Firebase / Firestoreデータ構造

```
users/{uid}/sessions/{sessionId}        # レガシー手動アップロード
  ├── raw_text, filename, hand_count
  ├── uploaded_at, status, result_pdf
  └── job_id                            # classify_result/{job_id}のID

users/{uid}/hands/{handId}              # Phase 7: リアルタイム自動取得
  ├── hand_json:    object              # fastFoldTableState生データ
  ├── captured_at:  string             # ISO8601（拡張機能側時刻）
  └── saved_at:     timestamp          # Firestore保存日時（ソート・フィルタに使用）

users/{uid}/analyses/{job_id}           # Phase 8: 解析結果永続化（未実装）
  ├── job_id, created_at
  ├── hand_count, blue_count, red_count, pf_count
  ├── categories:          object       # カテゴリ別内訳
  ├── classified_snapshot: string       # classified.json（JSON文字列、1MB上限）
  └── diff_from:           string       # 前回job_id（差分ハイライト用）

users/{uid}/opponents/{playerName}      # Phase 11: 対戦相手統計DB（未実装）
  ├── hand_count, vpip, pfr, three_bet_pct
  ├── cbet_flop, fold_to_3bet, sd_winrate, avg_bet_size
  ├── last_seen
  └── t4_stats: object                  # T4 UIから傍受したスタッツ（raw）
```

---

## 6. Chrome拡張機能

**拡張機能ID:** `ilkbcfenghigefpfjohppfjodahhoiif`  
**OAuthクライアントID:** `615725442966-l1k8rgi5m43stim6ellgj8e36s8hfn6l.apps.googleusercontent.com`

| ファイル | 役割 |
|---|---|
| `manifest.json` | MV3設定。2つのcontent_scripts（MAIN/ISOLATED） |
| `background.js` | Service Worker。Firebase Auth管理 + HAND_COMPLETE→POST |
| `popup.html/js` | ポップアップUI。ログイン・手数表示・「PokerGTOを開く」 |
| `interceptor.js` | WebSocket傍受（MAIN world）。Socket.IOイベント検知 |
| `content.js` | CustomEventをbackground.jsに転送（ISOLATED world） |

**自動解析トリガー:** `chrome.storage.local` の `handCounter` が閾値（デフォルト100）に達したら `/api/hands/analyze` を呼び出す。結果URLはポップアップに蓄積（Phase 9で実装）。

**対応ドメイン:**
- `https://*.tenfourpoker.com/*`
- `https://*.tenfour-poker.com/*`

---

## 7. 環境変数

| 変数名 | 説明 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini APIキー | AIモードのみ |
| `PORT` | サーバーポート（デフォルト: 5000） | 任意 |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | FirebaseサービスアカウントキーJSON | Firebase機能を使う場合 |
| `FIREBASE_API_KEY` | Firebase Web API Key | Firebase機能を使う場合 |
| `FIREBASE_AUTH_DOMAIN` | `{project-id}.firebaseapp.com` | Firebase機能を使う場合 |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID | Firebase機能を使う場合 |

---

## 8. デプロイ情報

- **本番URL:** `https://gto-production.up.railway.app`
- **リポジトリ:** `https://github.com/9p96d9/GTO-.git`
- **ブランチ:** `main`（pushで自動デプロイ）

---

## 9. Phase 8: 解析結果の永続化と累積解析（未着手）

### 背景
現在 `classify_result/{job_id}` の結果はRailwayサーバーのメモリに保存されており、サーバー再起動で消える。累積解析・履歴閲覧・拡張機能リンク蓄積の前提となる最重要技術負債。

### 8-1. 保存方式（合意: 案②）
集計数値 + classified.jsonスナップショットをFirestoreへ保存。  
Firestoreの1ドキュメント1MB上限を超える場合はFirebase Storageへ移行。

### 8-2. 累積解析表示
- `/sessions` に「解析履歴」セクション追加（最新順）
- 差分ハイライト: 前回 `job_id` と比較して新規追加ハンドに 🆕 バッジ
- URLの永続化: Firestoreの `classified_snapshot` からページを復元

---

## 10. Phase 9: 拡張機能UX改善（未着手）

### 9-1. セッション管理
- **セッション定義:** ログイン（SIGN_IN完了）時点からリセット
- ポップアップに経過時間（⏱ 1h 23m）とハンド数（🃏 87手）を表示

### 9-2. 設定機能（options.html）

| 設定名 | 選択肢 | デフォルト |
|---|---|---|
| 自動解析トリガー（ハンド数） | なし / 50 / 100 / 200 / 500 | 100 |
| プレイ時間通知 | なし / 30分 / 60分 / 120分 | なし |

- `extension/options.html` + `extension/options.js` 新規追加
- `manifest.json` に `"options_ui"` を追加
- ポップアップに「⚙ 設定」リンク

### 9-3. バッジ通知（ゲーム非干渉）

| 現状 | 変更後 |
|---|---|
| 自動解析完了時に新タブを開く | タブを開かない |
| — | バッジ「✓」（緑）で完了通知 |
| — | ポップアップに「📊 最新の解析結果を見る」リンクを蓄積 |

- クリック時: `chrome.windows.create({ type: "popup", focused: false })` で小窓表示（ゲーム画面のフォーカスを奪わない）

---

## 11. Phase 10: Web出力リデザイン（実装中）

### 10-1. 配色・CSS仕様（合意済み）

- 背景: 白 `#ffffff` / 本文: 黒 `#1a1a1a`
- **スート色（ドイツ式4色）:**
  - ♠ 黒 `#1a1a1a` / ♥ 赤 `#d32f2f` / ♦ 青 `#1565c0` / ♣ 緑 `#2e7d32`
- 印刷対応（Word文書スタイル）

### 10-2. レイアウト（合意済み）
- 青線（ショーダウン）を全件表示してから赤線（ノーショーダウン）
- 2列コンパクト表示は維持
- PFのみはデフォルト折りたたみ

### 10-3. アコーディオン構成

```
▼ サマリー（常に展開）
  総ハンド数 | 🔵青線 | 🔴赤線 | PFのみ | 合計獲得BB

▼ タブ切替（10-4参照）

▼ 🔵 青線ハンド一覧（デフォルト展開）
   ▶ バリュー/ブラフ成功 ▶ 各ハンド詳細
   ▶ ブラフキャッチ

▼ 🔴 赤線ハンド一覧（デフォルト展開）
   ▶ アグレッション勝利（★要AI）
   ▶ バッドフォールド / ナイスフォールド / フォールド要確認（★要AI）

▼ PFのみ（デフォルト折りたたみ）
   ▶ ポジション別統計
   ▶ フィルタ: ☑ 3BETポット ☑ 4BETポット ☐ 通常
   ▶ 全件表示
```

### 10-4. 可変表示タブ
- タブ①: 青赤線（デフォルト）
- タブ②: ポジション別（VPIP/PFR/3BET%/平均獲得BB/赤線率）
- タブ③: 時間帯別推移
- タブ④: チップ推移グラフ（Chart.js）

### 10-5. インラインAPI解析（BYOK）
- 対象: `needs_api: true`（`hero_aggression_won` + `fold_unknown`）のみ
- 各ハンド詳細内に `[🤖 このハンドをAI解析]` ボタン
- ページ遷移なし・同アコーディオン内に結果表示

**定型プロンプト:**

```
【hero_aggression_won向け】
ポーカーGTO解析。ヒーローが相手をフォールドさせて勝ちましたが、
バリューベットかブラフかを判定してください。
ポジション: {hero_position} / ホールカード: {hero_cards}
ボード: {board} / アクション: {action_flow} / 獲得: {result_bb}bb
回答（100字以内）:「バリュー」「ブラフ」「どちらとも言えない」と理由。

【fold_unknown向け】
ポーカーGTO解析。このヒーローのフォールドは正しかったか判定してください。
ポジション: {hero_position} / ホールカード: {hero_cards}
ボード: {board} / アクション: {action_flow}
回答（100字以内）:「正しいフォールド」「間違いフォールド」「判断難しい」と理由。
```

---

## 12. Phase 11: 対戦相手統計DB & SNS共有（未着手）

### 11-1. 対戦相手統計DB
- hand_jsonから自動算出（VPIP/PFR/3BET%/CBet%/フォールドto3BET/SD勝率）→ Firestore保存
- T4 UIから傍受したスタッツ（t4_stats）→ Firestore保存（自分専用ツールとして運用）
- 算出タイミング: `/api/hands/analyze` 完了後にバックグラウンドで自動集計

### 11-2. T4スタッツ傍受の調査手順（必要時に実施）

interceptor.js に以下を追加してイベント名を調査する:

```javascript
if (typeof raw === "string" && raw.startsWith("42")) {
  const payload = JSON.parse(raw.slice(2));
  if (/stat|player|profile|info/i.test(payload[0])) {
    console.log("[T4 Stats Event]", payload[0], payload[1]);
  }
}
```

T4でプレイヤーアイコンをクリックし、DevToolsで `[T4 Stats Event]` ログを確認する。

### 11-3. SNS（X）共有

**共有フォーマット（対戦相手カード・プレイヤー名・テーブルIDは除外）:**
```
🃏 ポーカーハンドまとめ
{hero_position} {hero_cards}
PF: {pf_actions}
{board_lines}
{result_sign}{result_bb}bb

#てんふぉー #ポーカー #テキサスホールデム
```

- `twitter.com/intent/tweet?text=...` 方式（X APIキー不要）
- 共有は完全任意。ナイスハンドの自動判定はしない（ユーザーが選択）

---

## 13. Phase 4〜6（既存）

### Phase 4: 複数セッション結合解析・テキスト保存（✅ 完了）
- `POST /api/sessions/analyze-multi`: 複数セッションのraw_textを結合して解析
- `POST /api/sessions/download-text`: 複数セッションを結合txtでダウンロード

### Phase 5: 管理者ダッシュボード（⬜ 未着手）
- Firebase Custom Claims で `admin: true` を付与
- `GET /admin`: 全ユーザーデータ閲覧・操作
- Firestoreスキーマ: `admins/{uid} { granted_at: timestamp }`

### Phase 6: 仕上げ・UX改善（⬜ 未着手）
- セッション解析ステータスのリアルタイム更新（SSE）
- opponents_summary.jsonをFirestoreに移行（Railway再起動でリセットされる問題の解消）

---

## 14. 技術的依存関係（フェーズ間）

```
Phase 8（永続化）
  ↓ 完了が前提
  Phase 9（拡張機能: 結果リンク蓄積）
  Phase 10（累積解析の積み上げ表示）
  Phase 11（対戦相手DB: 分析結果と紐付け）

Phase 10（Web出力リデザイン）
  ↓ 独立して実装可能
  10-1 CSS変更（✅ モック完成）
  10-3 アコーディオン
  10-4 可変表示タブ・Chart.js

Phase 11（対戦相手DB）
  ↓ T4スタッツ傍受は別途調査が必要
```

---

## 15. 注意事項

- `data/` フォルダのJSONは削除しない（`opponents_summary.json` は特に重要）
- `.env` はGitにコミットしない
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- classifyモードはGemini不要で高速（数秒〜十数秒）
- AIモードはGemini APIがボトルネック（約2〜3分）
- サービスアカウントキーJSONはGitにコミットしない（Railway環境変数で管理）
- `classify_result/{job_id}` はサーバー再起動でアクセス不能（→ Phase 8で解決予定）
- Firestoreの `order_by()` はインデックスに依存するため、`get_hands()` は `col.stream()` → Pythonソートではなく `order_by("saved_at")` を使用（全ドキュメントに `saved_at` が存在することを確認済み）
- Firestore無料プランの読み取りクォータ: 50,000回/日。`col.stream()` の多用で消費しやすいため注意
