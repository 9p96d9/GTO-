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
| Frontend | HTMLテンプレート（server.py内） |
| AI | Gemini 2.5 Flash（BYOK・Firestoreに暗号化保存） |
| PDF | puppeteer（Chromium内蔵・Docker必須の原因） |
| DB / 認証 | Firestore / Firebase Auth（Google） |
| 拡張機能 | Chrome MV3 |

---

## フェーズ状況

| フェーズ | 内容 | 状態 |
|---|---|---|
| 1〜4, 7〜10 | 基盤・拡張機能・解析・UX | ✅ 完了 |
| **Phase 12** | 解析カート & AI解析インライン（Gemini刷新） | 🔄 実装中 |
| Phase 5, 6, 11 | 管理者ダッシュボード・UX改善・対戦相手統計 | ⬜ 未着手 |

---

## Phase 12 実装状況

**完了済み**
- カートUI（追加/削除/ドロワー）
- `/api/cart/{job_id}` GET（gemini_results 込み）・POST
- `/api/user/settings` GET・PUT（APIキー保存・Firestore）
- `/api/cart/{job_id}/analyze` POST（SSE + バッチGemini）
- 🤖 AI解析結果セクション（JS で動的描画・ページロード時に復元）
- カートドロワーにAPIキー設定UI
- needs_api 自動カート追加（サーバー設定で ON/OFF）

**未着手**
- PDF AI込みバージョン（`?include_ai=true`）

**廃止済み（削除すること）**
- `POST /start_ai/{job_id}`
- カート名前保存・読み込み機能（API は残存・フロントから非公開）
- classify_progress の推定解析時間表示
- `value_or_bluff_success` → `value_success` にリネーム済み

---

## ファイル構成（主要）

```
server.py                    # FastAPI 全エンドポイント + HTMLテンプレート
scripts/
  classify.py                # 青線/赤線分類
  hand_converter.py          # fastFoldTableState → parse.py互換JSON
  analyze.py                 # Gemini GTO分析
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
  └── active_cart: [42, 17, 88]     # Phase 12: アクティブカート
  └── gemini_results: {"42": {...}} # Phase 12: AI解析結果
users/{uid}/settings/gemini         # Phase 12: encrypted_api_key・needs_api_auto_cart
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
| `POST /generate_pdf/{job_id}?include_ai=true` | AI込みPDF（Phase 12） |
| `GET/POST /api/cart/{job_id}` | カート操作（Phase 12）✅実装済 |
| `POST /api/cart/{job_id}/analyze` | Gemini解析SSE（Phase 12）⬜未実装 |
| `GET/PUT /api/user/settings` | APIキー管理（Phase 12）⬜未実装 |

---

## 環境変数（必須）

```
FIREBASE_SERVICE_ACCOUNT_JSON
FIREBASE_API_KEY
FIREBASE_AUTH_DOMAIN
FIREBASE_PROJECT_ID
GEMINI_API_KEY  # Phase 12以降は任意（ユーザーBYOK）
```
