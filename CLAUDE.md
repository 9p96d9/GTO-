# CLAUDE.md — PokerGTO 開発ガイド

> Claude Codeが会話開始時に自動で読み込む設定ファイル。
> 詳細仕様は **SPEC.md** を参照。このファイルは作業ルール＋現状サマリー。

---

## 作業ルール

- **実装前に SPEC.md を読む**（推測で進めない）
- **実装後に SPEC.md を更新**してからコミット（「実装だけしてSPEC更新しない」は禁止）
- 大きな変更の前に方針を一言確認する
- デバッグ用エンドポイント・ログは問題解決後に必ず削除する

---

## フェーズ状況

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1〜4, 7〜10, 12〜14 | 基盤・拡張機能・解析・UX・カート&AI解析・品質向上・リファクタ | ✅ 完了 |
| Phase 15 | UI/UX改善・Groq統合・トークン見積もり・ソート | ✅ 完了（15-5のみ動画待ち） |
| Phase 16 | AI解析表示改善（スートカラーリング・ストリート別BET額） | ✅ 完了 |
| Phase 17 | ランディングページ リデザイン（claude.ai/design活用） | ✅ 完了（sessionsページは未着手） |
| Phase 5, 6, 11 | 管理者ダッシュボード・UX改善・対戦相手統計 | ⬜ 未着手 |
| **Phase 18** | Railway → AWS 移行（ECS Fargate・IAM・VPC） | ⬜ 計画中 |
| **Phase 19** | Firebase → PostgreSQL 移行 ＋ アドミンダッシュボード | ⬜ 計画中 |
| **Phase 20** | バグ修正・仕上げ・UX polish | ⬜ 計画中（先行対応済みあり） |

---

## システム概要

T4ポーカーサイトのハンドログをChrome拡張でWebSocket傍受 → Firestore蓄積 → GTO分類 → Web表示。

**本番URL:** https://gto-production.up.railway.app
**リポジトリ:** https://github.com/9p96d9/GTO-
**ホスティング:** Railway（Docker・mainブランチ自動デプロイ）

| レイヤー | 技術 |
|---|---|
| Backend | FastAPI + uvicorn / Python 3.11 |
| AI | Groq(llama-3.3-70b) / Gemini 2.5 Flash 自動切替（BYOK・`gsk_`→Groq） |
| DB / 認証 | Firestore / Firebase Auth（Google） |
| PDF | puppeteer（Chromium内蔵・Docker必須の原因） |
| 拡張機能 | Chrome MV3 |

---

## ファイル構成（主要）

```
server.py                    # FastAPI 初期化・ミドルウェアのみ
state.py                     # グローバル変数（jobs, event_queues 等）
pipelines.py                 # run_classify_pipeline_from_json 等
routes/
  pages.py                   # 画面ルート
  api.py                     # /api/* ルート
  cart.py                    # /api/cart/* ルート
  legacy.py                  # 旧フロー（削除予定）
html/
  pages.py                   # Python HTML 生成関数（Jinja2移行予定）
templates/
  classify_result.html       # 解析結果画面テンプレート（Jinja2）
  *.html                     # その他ページ（Jinja2移行後に増える）
static/
  classify_result.js         # 解析結果画面JS（?v=日付でキャッシュバスト）
scripts/
  classify.py                # 青線/赤線分類
  hand_converter.py          # fastFoldTableState → parse.py互換JSON
  analyze2.py                # Groq/Gemini両対応・detailモード既定（現用）
  firebase_utils.py          # Firebase Admin SDK
extension/
  background.js              # Service Worker・自動解析トリガー
  interceptor.js             # WebSocket傍受（MAIN world）
  content.js                 # CustomEvent転送（ISOLATED world）
  popup.html/js              # ポップアップUI
```

---

## Firestoreデータ構造（概要）

```
users/{uid}/hands/{handId}          # 拡張機能が蓄積するハンド生データ
users/{uid}/analyses/{job_id}       # 解析結果
  ├── classified_snapshot: string   # gzip+base64圧縮（圧縮後900KB上限、超過時は省略）
  ├── snapshot_encoding:  string    # "gzip_b64"（旧レコードはフィールドなし＝生JSON）
  ├── active_cart: [42, 17, 88]     # アクティブカート
  └── gemini_results: {"42": {...}} # AI解析結果（Groq使用時もフィールド名維持）
users/{uid}/settings/gemini         # encrypted_api_key・needs_api_auto_cart
users/{uid}/sessions/{sessionId}    # レガシー手動アップロード
```

**注意:** ソートは必ず `order_by("saved_at")`（`captured_at` は欠落ドキュメントあり）

---

## よくあるミスと対処法

### Firestoreのソート
```python
# NG: captured_at は一部ドキュメントに欠落
order_by("captured_at")
# OK
order_by("saved_at")
```

### `get()` と None の罠
```python
# NG: value が null だと [] にならない
hand_results = hand_json.get("handResults", [])
# OK
hand_results = hand_json.get("handResults") or []
```

### chrome.runtime の罠
- `/sessions` ページのコンソールから拡張機能APIは呼べない
- 拡張機能コンソール（chrome://extensions → background）と別物

### classify_result.js のキャッシュ
- 変更後は `?v=YYYYMMDD` を更新しないとブラウザが旧JSを使い続ける
- 変更箇所: `templates/classify_result.html` の `<script src="/static/classify_result.js?v=...">` 

---

## デプロイ手順

```bash
# 構文チェック（必須）
python -c "
import ast, pathlib
for f in ['server.py','state.py','pipelines.py','routes/pages.py','routes/api.py','routes/cart.py','routes/legacy.py','html/pages.py']:
    p = pathlib.Path(f)
    if p.exists(): ast.parse(p.read_text(encoding='utf-8')); print(f'OK: {f}')
"

# コミット & プッシュ（Railway が main ブランチを自動デプロイ）
git add <files>
git commit -m "feat/fix/docs: 変更内容"
git push origin master:main
```

---

## 会話再開テンプレート（/clear後）

```
CLAUDE.mdを読んでから作業を始めてください。
細部が必要な場合はSPEC.mdも読む。
今日やりたいこと：[ここに作業内容]
```
