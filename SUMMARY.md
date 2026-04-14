# SUMMARY.md — PokerGTO 仕様サマリー

> 毎回の作業開始時に読む圧縮版。細部は **SPEC.md** を参照。
> **更新タイミング:** フェーズ状況変化・エンドポイント追加・Firestore構造変更時のみ

---

## システム概要

T4ポーカーサイトのハンドログをChrome拡張でWebSocket傍受 → Firestore蓄積 → GTO分類 → Web表示。

**本番URL:** https://gto-production.up.railway.app  
**リポジトリ:** https://github.com/9p96d9/GTO-  
**ホスティング:** Railway（Docker・mainブランチ自動デプロイ）

---

## 技術スタック

| レイヤー | 技術 |
|---|---|
| Backend | FastAPI + uvicorn / Python 3.11 |
| Frontend | Jinja2 外部テンプレート（`templates/classify_result.html`） |
| AI | Groq(llama-3.3-70b) / Gemini 2.5 Flash 自動切替（BYOK・`gsk_`キー→Groq） |
| PDF | puppeteer（Chromium内蔵・Docker必須の原因） |
| DB / 認証 | Firestore / Firebase Auth（Google） |
| 拡張機能 | Chrome MV3 |

---

## フェーズ状況

| フェーズ | 内容 | 状態 |
|---|---|---|
| 1〜4, 7〜10, 12〜14 | 基盤・拡張機能・解析・UX・カート&AI解析・品質向上・リファクタ | ✅ 完了 |
| **Phase 15** | PDF AI込みバージョン（`?include_ai=true`） | ⬜ 次回 |
| Phase 5, 6, 11 | 管理者ダッシュボード・UX改善・対戦相手統計 | ⬜ 未着手 |

---

## Phase 13 実装状況（2026-04-14 完了）

**完了済み**
- `analyze.py` プロンプト改修
  - 新フィールド追加: `hand_reading`（相手ハンドリーディング）・`opp_gto_diff`（相手GTOずれ）
  - `kaizen` → 良いプレイにも代替ライン提示、悪いプレイは必須記載
  - `detail` 上限 80 字に拡張
- `html/pages.py` に `data-board` 属性追加（ボード情報をハンドカードに付与）
- `classify_result.html` AI解析結果セクション刷新
  - GTO評価バッジ（✅/⚠️/❌/🎲 色分け）
  - Hero情報ヘッダー（ポジション・手札・ボード・損益）
  - `ハンドリーディング` / `相手GTOずれ` 折りたたみセクション
  - 解析進捗バー（N/M手完了・経過秒数）

**未着手（Phase 15 に移行）**
- PDF AI込みバージョン（`?include_ai=true`）

---

## ファイル構成（主要）

```
server.py                    # FastAPI 初期化・ミドルウェアのみ（Phase 14 リファクタ後）
state.py                     # グローバル変数（jobs, event_queues 等）
pipelines.py                 # run_classify_pipeline_from_json 等
routes/
  pages.py                   # 現役画面ルート
  api.py                     # /api/* ルート
  cart.py                    # /api/cart/* ルート
  legacy.py                  # 旧フロー（将来削除予定）
html/
  pages.py                   # Python HTML 生成関数（Jinja2 本格移行まで）
templates/
  classify_result.html       # 解析結果画面テンプレート（Jinja2）
scripts/
  classify.py                # 青線/赤線分類
  hand_converter.py          # fastFoldTableState → parse.py互換JSON
  analyze.py                 # Gemini専用（旧・現在は未使用）
  analyze2.py                # Groq/Gemini両対応・detailモード既定（現用）
  generate_noapilist.js      # NoAPI PDF生成
  firebase_utils.py          # Firebase Admin SDK
extension/
  background.js              # Service Worker・自動解析トリガー
  interceptor.js             # WebSocket傍受（MAIN world）
  content.js                 # CustomEvent転送（ISOLATED world）
  popup.html/js              # ポップアップUI
```

---

## classify カテゴリ一覧

| カテゴリ | 色 | needs_api |
|---|---|---|
| value_success | 青 | |
| bluff_catch | 青 | |
| bluff_failed | 青 | |
| call_lost | 青 | |
| hero_aggression_won | 赤 | ✅ |
| bad_fold / nice_fold | 赤 | |
| fold_unknown | 赤 | ✅ |
| preflop_only | — | 不可（カート追加不可） |

---

## Firestoreデータ構造（概要）

```
users/{uid}/hands/{handId}          # 拡張機能が蓄積するハンド生データ
users/{uid}/analyses/{job_id}       # 解析結果（classified_snapshot・gemini_results）
  └── active_cart: [42, 17, 88]     # アクティブカート
  └── gemini_results: {"42": {...}} # AI解析結果
users/{uid}/settings/gemini         # encrypted_api_key・needs_api_auto_cart
users/{uid}/sessions/{sessionId}    # レガシー手動アップロード
users/{uid}/opponents/{playerName}  # Phase 11: 未実装
```

**注意:** ソートは必ず `order_by("saved_at")`（`captured_at` は欠落ドキュメントあり）

---

## 主要APIエンドポイント（概要）

| パス | 説明 |
|---|---|
| `POST /api/hands/realtime` | ハンド1件保存 |
| `POST /api/hands/analyze` | 解析パイプライン実行 |
| `GET /classify_result/{job_id}` | 結果画面 |
| `POST /generate_pdf/{job_id}` | NoAPI PDF |
| `GET/POST /api/cart/{job_id}` | カート操作 ✅ |
| `POST /api/cart/{job_id}/analyze` | Gemini解析SSE ✅ |
| `GET/PUT /api/user/settings` | APIキー管理 ✅ |

---

## 環境変数（必須）

```
FIREBASE_SERVICE_ACCOUNT_JSON
FIREBASE_API_KEY
FIREBASE_AUTH_DOMAIN
FIREBASE_PROJECT_ID
GEMINI_API_KEY  # 任意（ユーザーBYOKが優先）
GROQ_API_KEY    # 任意（gsk_ キー。設定するとGroq優先・Geminiフォールバック）
```

**BYOKキー判定ロジック（`analyze2.py`）:**
- `gsk_` で始まる → Groq (llama-3.3-70b-versatile)
- それ以外 → Gemini (gemini-2.5-flash)

**よくあるミス（Dockerfile）:**
新ファイルを追加したら必ず `Dockerfile` の `COPY` に追記すること。
Phase14リファクタ時に `state.py` / `pipelines.py` / `routes/` / `html/` の追記漏れで本番クラッシュした実績あり。
