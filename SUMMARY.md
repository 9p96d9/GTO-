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
| AI | Gemini 2.5 Flash（BYOK・Firestoreに保存） |
| PDF | puppeteer（Chromium内蔵・Docker必須の原因） |
| DB / 認証 | Firestore / Firebase Auth（Google） |
| 拡張機能 | Chrome MV3 |

---

## フェーズ状況

| フェーズ | 内容 | 状態 |
|---|---|---|
| 1〜4, 7〜10, 12, 14 | 基盤・拡張機能・解析・UX・カート&AI解析・リファクタ | ✅ 完了 |
| **Phase 13** | AI解析品質向上・進捗表示復活・PDF AI込み | ⬜ 次回 |
| Phase 5, 6, 11 | 管理者ダッシュボード・UX改善・対戦相手統計 | ⬜ 未着手 |

---

## Phase 12 実装状況（2026-04-14 完了）

**完了済み**
- カートUI（追加/削除/ドロワー）
- `/api/cart/{job_id}` GET（gemini_results 込み）・POST
- `/api/user/settings` GET・PUT（APIキー保存・Firestore）
- `/api/cart/{job_id}/analyze` POST（SSE + バッチGemini）
- AI解析結果セクション（JS で動的描画・ページロード時に復元）
- カートドロワーにAPIキー設定UI
- needs_api 自動カート追加（サーバー設定で ON/OFF）
- classify_result を Jinja2 外部テンプレートに移行（`templates/classify_result.html`）
- 解析完了後に自動スクロール・ドロワーを閉じる

**未着手（Phase 13 に移行）**
- PDF AI込みバージョン（`?include_ai=true`）
- AI解析品質向上（プロンプト・表示）
- 解析進捗表示の復活

**2026-04-14 作業での苦労点**
- Jinja2移行後 Dockerfile に `COPY templates/` を追記し忘れ → Railway で 500 エラー
- `gemini-2.0-flash` は新規ユーザー向け廃止済み（`gemini-2.5-flash` が正解）
- APIキー入力欄が flex レイアウトで幅ゼロに潰れていた

---

## Phase 13 次回やること（優先順）

1. **AI解析品質向上**（`analyze.py` プロンプト改修）
   - 良いプレイにも代替ラインを提示
   - 悪いプレイには正しいアクションを必須記載
   - 各ストリートの相手ハンドリーディングを追加
   - 相手のGTOからのずれを分析・搾取ポイント提示
2. **解析結果表示改善**（`classify_result.html`）
   - Hero手札・ボード・各ストリートアクションを解析カードに表示
   - 評価ラベルをバッジ化
3. **解析進捗表示の復活**
   - 経過時間表示
   - カート追加時の推定解析時間表示
4. **PDF AI込みバージョン**（進捗表示完成後）

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
  analyze.py                 # Gemini GTO分析（MODEL=gemini-2.5-flash）
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
```
