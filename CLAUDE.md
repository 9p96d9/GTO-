# CLAUDE.md — PokerGTO 開発ガイド

> このファイルはClaude Codeが会話開始時に自動で読み込む設定ファイルです。
> プロジェクトルート（`c:\Users\user\Desktop\GTO-\`）に置いてください。

---

## 基本ルール

### 作業前に必ずSPEC.mdを読む
実装・修正・議論の前に `SPEC.md` を読んで現状を把握してから着手する。
推測で進めない。

### 実装したらSPEC.mdを更新する
機能追加・変更・バグ修正が完了したら、その内容をSPEC.mdに反映してからコミットする。
「実装だけしてSPECを更新しない」は禁止。

### コミット単位を小さく保つ
1コミット = 1つの変更目的。デバッグコードと機能実装を同じコミットに混ぜない。

---

## トークン節約ルール（重要）

### 読み込みを最小限に
- ファイル全体をむやみにReadしない
- まずGrepで該当箇所を特定してからReadする
- 1回のReadで必要な範囲だけ取得する（`lines 100-200` など）

### 診断・デバッグの後始末
- デバッグ用エンドポイント（`/api/hands/debug` 等）は問題解決後に必ず削除する
- `col.stream()` による全件取得は診断専用。本番コードには残さない
- Firestoreの無料枠：1日5万ドキュメント読み取り。診断で使い切らないよう注意

### 確認してから進む
- 大きな変更の前に「こういう方針で進めます」と一言確認する
- 間違った方向に10ステップ進むよりも、1ステップで確認するほうがトークンの節約になる

---

## ファイル構成（重要ファイル）

```
GTO-/
├── CLAUDE.md              ← このファイル（Claude Code自動読み込み）
├── SPEC.md                ← 唯一の仕様書（常に最新を保つ）
├── server.py              ← FastAPI サーバー（全エンドポイント・HTML含む）
├── scripts/
│   ├── classify.py        ← 青線/赤線分類
│   ├── hand_converter.py  ← Firestore JSON → classify.py互換形式
│   ├── firebase_utils.py  ← Firestore CRUD / idToken検証
│   ├── parse.py           ← テキスト → JSON（レガシー）
│   └── analyze.py         ← Gemini GTO分析
├── extension/             ← Chrome拡張機能 MV3
│   ├── manifest.json
│   ├── background.js      ← Service Worker・自動解析トリガー
│   ├── interceptor.js     ← WebSocket傍受（MAIN world）
│   ├── content.js         ← CustomEvent転送（ISOLATED world）
│   ├── popup.html/js      ← ポップアップUI
│   └── options.html/js    ← 設定画面（Phase 9で追加予定）
└── static/
    └── css_test.html      ← CSSモック確認用（サーバー不要）
```

---

## 技術スタック早見表

| 用途 | 技術 |
|---|---|
| サーバー | FastAPI + uvicorn（Python 3.11）|
| DB | Firestore（Firebase Admin SDK）|
| 認証 | Firebase Auth（Google OAuth）|
| 拡張機能 | Chrome MV3 |
| ホスティング | Railway（mainブランチ自動デプロイ）|
| AI（任意） | Gemini 2.5 Flash（BYOK）|
| PDF | puppeteer（Node.js 20）|

---

## Firestoreデータ構造

```
users/{uid}/hands/{handId}        ← リアルタイム自動取得ハンド
  ├── hand_json: object           ← fastFoldTableState 生データ
  ├── captured_at: string         ← ISO8601（一部欠落あり）
  └── saved_at: timestamp         ← Firestore保存日時（常に存在・ソートはこちらを使う）

users/{uid}/analyses/{job_id}     ← Phase 8で追加予定（解析結果永続化）
  ├── job_id: string
  ├── created_at: timestamp
  ├── hand_count / blue_count / red_count / pf_count: number
  ├── categories: object
  └── classified_snapshot: string ← JSON文字列（1MB上限）
```

---

## よくあるミスと対処法

### `get_hands()` のソート
- `order_by("captured_at")` は使わない（欠落ドキュメントが除外される）
- `order_by("saved_at")` を使う（全件に存在する）

### `.get()` と None の罠
```python
# NG: keyが存在してもvalueがNullなら [] にならない
hand_results = hand_json.get("handResults", [])

# OK: Nullも [] として扱う
hand_results = hand_json.get("handResults") or []
```

### `col.stream()` のコスト
- 全件取得はFirestoreの読み取り消費が大きい
- 件数確認には COUNT集計クエリを使う（読み取りコスト0）
- 診断後は必ず `order_by().limit()` に戻す

### chrome.runtime はページコンテキスト外では使えない
- `/sessions` ページのコンソールからは `window.debugXxx()` 経由で呼ぶ
- 拡張機能のコンソールとブラウザページのコンソールは別物

---

## effortレベルの使い分け

現在のレベル確認: `/effort`

| 場面 | レベル |
|---|---|
| バグ診断（原因不明系）・DB設計・セキュリティ実装 | `/effort high` |
| classify.py ロジック改善・Phase 8/11 設計 | `/effort high` |
| CSS・HTML・ドキュメント更新・エンドポイント追加 | デフォルト（medium）|

> `/effort max` は Opus 4.6 専用。Sonnet 4.6 では無効。

---

## デプロイ手順

```bash
# 構文チェック（必須）
python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"
node --check scripts/generate_noapilist.js && echo "OK"

# コミット & プッシュ（Railwayが自動デプロイ）
git add <files>
git commit -m "feat/fix/docs: 変更内容の説明"
git push origin main
```

---

## フェーズ状況（SPEC.mdと同期して更新）

| フェーズ | 内容 | 状態 |
|---|---|---|
| Phase 1〜4 | Firebase基盤・拡張機能・セッション管理 | ✅ 完了 |
| Phase 5 | 管理者ダッシュボード | ⬜ 未着手 |
| Phase 6 | 仕上げ・UX改善 | ⬜ 未着手 |
| Phase 7 | リアルタイムハンドログ自動取得 | ✅ 完了 |
| Phase 8 | 解析結果の永続化（Firestore保存）+ 累積解析 | ⬜ 未着手 |
| Phase 9 | 拡張機能UX改善（設定・バッジ・非干渉通知） | ⬜ 未着手 |
| Phase 10 | Web出力リデザイン（白背景・アコーディオン） | ✅ 完了 |
| Phase 11 | 対戦相手統計DB + SNS共有 | ⬜ 未着手 |

---

## 会話の始め方（/clear後の再開手順）

新しい会話を始めるときは以下をコピペする：

```
CLAUDE.mdとSPEC.mdを読んでから作業を始めてください。
今日やりたいこと：[ここに作業内容]
```

これだけで文脈が復元されます。長い会話ログを貼る必要はありません。
