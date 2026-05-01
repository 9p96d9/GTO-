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
| Phase 20a | sessions解析ログ削除機能・Firestoreフィールドマスク最適化 | ✅ 完了 |
| Phase 20b | 3D可視化 4タブ全実装（Sankey/Bubble/TimeSeries） | ✅ 完了 |
| **Phase 18** | Railway → AWS 移行（ECS Fargate・IAM・VPC・ALB・Secrets Manager） | ✅ 完了（Railway停止済み: 2026-05-15） |
| Phase 5 | 管理者ダッシュボード（/admin・KPI・ユーザー一覧） | ✅ 完了 |
| **Phase 19** | Firebase → PostgreSQL 移行 ＋ アドミンダッシュボード（USE_POSTGRESフラグで共存） | ✅ 完了（USE_POSTGRES=true・本番稼働中） |
| **Phase 20c** | ドリルパネルリッチ化・バグ修正・UX polish | 🔄 進行中 |
| Phase 6, 11 | UX改善・対戦相手統計 | ⬜ 未着手 |

---

## システム概要

T4ポーカーサイトのハンドログをChrome拡張でWebSocket傍受 → Firestore蓄積 → GTO分類 → Web表示。

**本番URL:** http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com/
**リポジトリ:** https://github.com/9p96d9/GTO-
**ホスティング:** AWS ECS Fargate（Docker・mainブランチ自動デプロイ）

| レイヤー | 技術 |
|---|---|
| Backend | FastAPI + uvicorn / Python 3.11 |
| AI | Groq(llama-3.3-70b) / Gemini 2.5 Flash 自動切替（BYOK・`gsk_`→Groq） |
| DB / 認証 | PostgreSQL（RDS） / Firebase Auth（Google）/ USE_POSTGRESフラグで切替 |
| PDF | puppeteer（Chromium内蔵・Docker必須の原因） |
| 拡張機能 | Chrome MV3 |

---

## ファイル構成（主要）

```
server.py                    # FastAPI 初期化・ミドルウェアのみ
state.py                     # グローバル変数（jobs, event_queues 等）
pipelines.py                 # run_classify_pipeline_from_json 等
routes/
  pages.py                   # 画面ルート（/3d_view/{job_id} 含む）
  api.py                     # /api/* ルート（DELETE /api/analyses/{job_id} 含む）
  cart.py                    # /api/cart/* ルート
html_pages/
  pages.py                   # Python HTML 生成関数（three_d_view_page 含む）
templates/
  classify_result.html       # 解析結果画面テンプレート（Jinja2）
  3d_view.html               # 3D可視化（4タブ: 3Dバー/Sankey/Bubble/TimeSeries）
  sessions.html              # セッション一覧・解析履歴（削除ボタン付き）
  landing.html               # ランディングページ
  login.html / error.html    # 認証・エラー
  progress.html / classify_progress.html / restore.html / upload.html / report.html / dashboard.html
static/
  classify_result.js         # 解析結果画面JS（?v=日付でキャッシュバスト）
scripts/
  classify.py                # 青線/赤線分類
  hand_converter.py          # fastFoldTableState → parse.py互換JSON
  analyze2.py                # Groq/Gemini両対応・detailモード既定（現用）
  firebase_utils.py          # Firebase Admin SDK（フィールドマスク最適化済み）
  postgres_utils.py          # PostgreSQL実装（firebase_utilsと同一シグネチャ）
  db.py                      # USE_POSTGRESフラグでfirebase/postgres切り替え
  export_firebase_csv.py     # FirebaseデータをCSVエクスポート（Power BI用）
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

**本番環境:** AWS ECS Fargate（Railway は 2026-05-15 停止済み）  
**本番URL:** http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com/  
**CI/CD:** `main` ブランチへの push → GitHub Actions が自動実行

```
git push origin main
  ↓ GitHub Actions (.github/workflows/deploy.yml)
  ↓ Docker build → ECR push → ECS タスク定義更新 → サービス再起動
  ↓ 所要時間: 約3〜5分（ALBヘルスチェック最適化済み: 間隔10秒・正常しきい値2回）
```

進捗確認: https://github.com/9p96d9/GTO-/actions

```bash
# 構文チェック（必須）
python -c "
import ast, pathlib
for f in ['server.py','state.py','pipelines.py','routes/pages.py','routes/api.py','routes/cart.py','html/pages.py']:
    p = pathlib.Path(f)
    if p.exists(): ast.parse(p.read_text(encoding='utf-8')); print(f'OK: {f}')
"

# コミット & プッシュ
git add <files>
git commit -m "feat/fix/docs: 変更内容"
git push origin main
```

---

## 会話再開テンプレート（/clear後）

```
CLAUDE.mdを読んでから作業を始めてください。
細部が必要な場合はSPEC.mdも読む。
今日やりたいこと：[ここに作業内容]
```
