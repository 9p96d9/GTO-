# ポーカーGTO 分析システム 仕様書

**バージョン:** 5.0
**最終更新:** 2026-04-12
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
| **Phase 9** | **拡張機能UX改善（設定・バッジ・非干渉通知）** | 🔄 設計中 |
| Phase 10 | Web出力リデザイン（白背景・アコーディオン・可変表示） | ✅ 完了 |
| Phase 11 | 対戦相手統計DB ＋ SNS共有 | ⬜ 未着手 |

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
| AI | Google Gemini 2.5 Flash（オプション・BYOK） |
| 手役評価 | treys ライブラリ（classify.py で使用） |
| PDF生成 | puppeteer（Chromium内蔵） |
| リアルタイム通信 | SSE（Server-Sent Events） |
| 認証・DB | Firebase Auth（Google）/ Firestore |
| ブラウザ拡張 | Chrome拡張機能（MV3） |
| ホスティング | Railway（Docker、mainブランチ自動デプロイ） |

### ファイル構成

```
GTO-/
├── server.py                   # FastAPI サーバー（全エンドポイント・HTMLテンプレート含む）
├── scripts/
│   ├── parse.py                # ハンド履歴パーサー（txt → JSON）
│   ├── classify.py             # 青線/赤線分類（JSON → classified JSON）
│   ├── hand_converter.py       # fastFoldTableState JSON → parse.py互換JSON変換
│   ├── analyze.py              # Gemini GTO分析
│   ├── generate.js             # AI PDFレポート生成
│   ├── generate_noapilist.js   # NoAPI PDFレポート生成
│   └── firebase_utils.py       # Firebase Admin SDK ユーティリティ
├── extension/                  # Chrome拡張機能（MV3）
│   ├── manifest.json
│   ├── popup.html / popup.js   # ポップアップUI
│   ├── background.js           # Service Worker（Firebase Auth管理・自動解析トリガー）
│   ├── interceptor.js          # WebSocket傍受（MAIN world）
│   ├── content.js              # CustomEventをbackground.jsに転送
│   ├── options.html / options.js  # 設定画面（Phase 9で追加）
│   └── icons/
└── static/
    └── css_test.html           # CSSモック確認用
```

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
    勝ち+Hero最終アグレッサー → value_or_bluff_success（青）
    勝ち+相手最終アグレッサー → bluff_catch（青）
    負け+Hero最終アグレッサー → bluff_failed（青）
    負け+相手最終アグレッサー → call_lost（青）
  ショーダウンなし:
    Hero勝ち → hero_aggression_won（赤・needs_api）
    Hero負け: treysで判定可 → bad_fold / nice_fold（赤） / 判定不能 → fold_unknown（赤・needs_api）
```

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
| POST | `/start_ai/{job_id}` | AI分析+PDF生成（BYOKキー） |
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
  └── classified_snapshot: string  # classified.json（900KB上限、超過時は省略）

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
| `popup.html/js` | ポップアップUI |
| `options.html/js` | 設定画面（Phase 9で追加） |

**自動解析トリガー:** `handCounter` が閾値（デフォルト100）に達したら `/api/hands/analyze` をバックグラウンドで実行。完了後はバッジ通知（Phase 9実装）。

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

## 8. Phase 9: 拡張機能UX改善（設計中）

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

## 9. Phase 11: 対戦相手統計DB & SNS共有（未着手）

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

## 10. 注意事項

- `data/` フォルダのJSONは削除しない
- `.env` / サービスアカウントキーJSONはGitにコミットしない
- puppeteer（Chromium）が重いためDocker imageは約1GB
- Railway無料枠はストレージ永続化なし（PDFは即ダウンロード推奨）
- Firestoreの読み取りクォータ: 50,000回/日
- `order_by("saved_at")` を使う（`captured_at` は一部欠落ドキュメントがある）
- `classify_result/{job_id}` はサーバー再起動後もFirestoreから自動復元（Phase 8実装済み）
