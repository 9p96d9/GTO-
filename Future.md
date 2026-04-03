# PokerGTO 開発計画書 v1.0
作成日: 2026-04-03

---

## 目標（完成形のユーザー体験）

1. Chromeの拡張機能ボタンを押す（T4ページ上）
2. スクレイプ完了 → 自動でFirebaseに保存
3. PokerGTOサイトを開く → 自分のハンドログが一覧で見える
4. 解析したいセッションを選んで「解析」を押す
5. 終わり

**ローカル保存なし / ファイルアップロードなし / タブの往復なし**

---

## 現状 vs 完成形

| ステップ | 現状 | 完成形 |
|---|---|---|
| ① スクレイプ | 拡張機能起動 → txtをDLフォルダに保存 | 拡張機能起動 → Firebaseに自動保存 |
| ② データ転送 | txtをPokerGTOにアップロード | 不要（Firebaseから直接読む） |
| ③ 解析 | parse → classify → PDF | 同じ（変更なし） |
| ④ 履歴管理 | なし | Firebase上でセッション一覧・選択解析 |
| ⑤ ユーザー管理 | なし | Firebase Auth（一般 / 管理者） |

---

## アーキテクチャ概要

```
[Chrome拡張機能（自作JS）]
    │ scrape完了
    │ Firebase Auth トークン付きでPOST
    ▼
[Firebase Firestore]
    ├── users/{uid}/sessions/{sessionId}
    │       ├── raw_text: "ハンドログ全文"
    │       ├── uploaded_at: timestamp
    │       └── status: "pending" / "analyzed"
    └── admins/{uid}  ← 管理者フラグ

[PokerGTO サーバー（FastAPI）]
    │ Firestoreからraw_textを取得
    │ 既存パイプライン（parse→classify→PDF）に流す
    ▼
[PokerGTO フロントエンド]
    ├── セッション一覧画面（自分のデータのみ）
    ├── 解析画面（既存フローを流用）
    └── 管理者画面（全ユーザーのデータ）
```

---

## 開発フェーズ

---

### Phase 1: Firebase基盤構築
**目的:** データの受け皿を作る

#### タスク一覧

**1-1. Firebaseプロジェクト設定**
```
- Firestoreのデータ構造を設計・作成
  users/{uid}/sessions/{sessionId}
    raw_text: string
    filename: string
    uploaded_at: timestamp
    hand_count: number
    status: "pending" | "analyzing" | "done" | "error"
    result_pdf: string（PDFファイル名）

- Firebase Authを有効化（メール/パスワード認証）
- Firestoreセキュリティルール設定
  - 自分のデータのみ読み書き可
  - adminsコレクションは読み取り専用（サーバーから設定）
```

**1-2. PokerGTOサーバー側: Firebase Admin SDK導入**
```
- requirements.txtに firebase-admin を追加
- サービスアカウントキーを環境変数で管理（Railway secrets）
- Firestoreへの読み書きユーティリティ関数を作成
```

**1-3. 動作確認**
```
- FirestoreにテストデータをPOSTできるか
- サーバーからFirestoreのデータを読めるか
```

**完了条件:** サーバーとFirestoreが疎通している

---

### Phase 2: 拡張機能の改修
**目的:** ローカル保存 → Firebase直接保存に切り替える

#### タスク一覧
ぷ
**2-1. Firebase Auth対応（拡張機能側）**
```
- 拡張機能にログイン画面を追加（popup.html）
- メール/パスワードでサインイン
- 取得したidTokenをlocalStorageに保存
```

**2-2. スクレイプ後の送信先変更**
```
現状:
  スクレイプ完了 → chrome.downloads.download() でtxtを保存

変更後:
  スクレイプ完了 → POST /api/upload-from-extension
                    Header: Authorization: Bearer {idToken}
                    Body: { raw_text, filename, hand_count }
                → PokerGTOサーバーがFirestoreに保存
```

**2-3. PokerGTOサーバー側: 受信エンドポイント追加**
```
POST /api/upload-from-extension
  - idTokenをFirebase Adminで検証 → uidを取得
  - Firestore users/{uid}/sessions/{newId} に保存
  - レスポンス: { session_id, status: "saved" }
```

**2-4. 拡張機能にフィードバックUI追加**
```
- 送信中スピナー表示
- 成功: "PokerGTOに送信しました ✓"
- 失敗: "送信失敗。再試行しますか？"
```

**完了条件:** 拡張機能を使うとFirestoreにデータが入り、ローカルDLが不要になる

---

### Phase 3: PokerGTO フロントエンド改修
**目的:** ファイルアップロード画面 → セッション一覧画面に置き換える

#### タスク一覧

**3-1. ログイン画面（PokerGTOサイト側）**
```
GET /login
  - メール/パスワードフォーム
  - Firebase Auth JS SDKでサインイン
  - idTokenをsessionCookieまたはlocalStorageに保存
```

**3-2. セッション一覧画面**
```
GET /sessions
  - Firebase AuthのidTokenをサーバーに送信
  - サーバーがFirestoreから users/{uid}/sessions を取得
  - 一覧表示: 日付 / ハンド数 / ステータス / 操作ボタン

  操作ボタン:
    [解析する] → 既存パイプライン（parse→classify→PDF）に流す
    [削除]     → Firestoreから削除
```

**3-3. 既存の /upload エンドポイントの内部改修**
```
現状: txtファイルをformDataで受け取る
変更: session_idを受け取り、Firestoreからraw_textを取得して
      既存パイプライン（parse.py → classify.py）に流す

※ parse.py / classify.py 自体は変更不要
```

**3-4. 現状のアップロード画面の扱い**
```
選択肢A: 削除（Firestoreからのみに統一）
選択肢B: 残す（後方互換として。未ログインユーザー用）
→ 初期は選択肢Bを推奨（段階的移行）
```

**完了条件:** PokerGTOサイト上でセッション一覧が見えて解析まで完結する

---

### Phase 4: ユーザー管理 & 管理者機能
**目的:** 不特定多数への対応 + 管理者ダッシュボード

#### タスク一覧

**4-1. ユーザー登録フロー**
```
GET /register
  - メール/パスワード登録
  - Firebase AuthでcreateUser
  - Firestore users/{uid}/profile に初期データ作成
    { created_at, email, role: "user" }
```

**4-2. 管理者ロール設計**
```
Firestoreに admins コレクションを作成
admins/{uid}: { granted_at }

または Firebase Auth Custom Claims を使用（推奨）
  → サーバー側で admin: true クレームを付与
  → idToken検証時に自動で判定できる
```

**4-3. 管理者ダッシュボード**
```
GET /admin（adminロールのみアクセス可）

表示内容:
  - ユーザー一覧（登録日・最終アップロード日・総セッション数）
  - 全ユーザーのセッション一覧（ユーザーで絞り込み可）
  - 任意のセッションを解析にかける
  - セッションデータのCSVエクスポート
  - ユーザーへのadmin権限付与/剥奪
```

**4-4. セキュリティルール最終化**
```
Firestoreルール:
  - users/{uid}/** → 本人のみ読み書き
  - admins/** → サーバー（Admin SDK）のみ書き込み

サーバー側:
  - 全エンドポイントでidToken検証を必須化
  - adminエンドポイントはCustom Claims確認
```

**完了条件:** 一般ユーザーは自分のデータのみ、管理者は全データにアクセスできる

---

### Phase 5: 仕上げ・UX改善（任意）
```
- セッション複数選択 → まとめて解析（累積レポート）
- 解析ステータスのリアルタイム表示（SSEはすでにある）
- PokerGTOサイトのTOP画面をセッション一覧に変更
- 拡張機能からPokerGTOのセッション一覧を直接開くボタン
- opponents_summary.jsonをFirestoreに移行（Railway再起動で消える問題の解消）
```

---

## 開発順序サマリー

```
Phase 1: Firebase基盤        ✅ 完了（2026-04-03）
  └─ firebase-admin追加 / scripts/firebase_utils.py作成
  └─ /api/firebase-config エンドポイント追加

Phase 2: 拡張機能改修        ✅ 完了（2026-04-03）
  └─ extension/ ディレクトリ作成（manifest / popup / background / content）
  └─ /api/upload-from-extension / /api/sessions / DELETE・analyze追加

Phase 3: フロントエンド改修  ✅ 完了（2026-04-03）
  └─ /login （Google認証画面）
  └─ /sessions （セッション一覧・解析・削除ボタン）
  └─ セッション一覧から既存classifyパイプラインへ接続済み

Phase 4: ユーザー管理        ⬜ 未着手
  └─ 登録フロー / 管理者ダッシュボード

Phase 5: 仕上げ              ⬜ 未着手
```

## 残タスク（次回セッションで着手可能）

### ① Firebase コンソール設定（手動作業）
→ extension/README.md に手順を記載済み

### ② Railway 環境変数を4つ追加
```
FIREBASE_SERVICE_ACCOUNT_JSON = （サービスアカウントJSON全体）
FIREBASE_API_KEY               = （Webアプリ設定から）
FIREBASE_AUTH_DOMAIN           = xxx.firebaseapp.com
FIREBASE_PROJECT_ID            = プロジェクトID
```

### ③ Chrome拡張機能のアイコン画像作成
→ extension/icons/ に icon16.png / icon48.png / icon128.png を配置

### ④ OAuthクライアント登録（Chromeにロード後）
→ 拡張機能ID取得 → Google Cloud Console でOAuth追加
→ Firebase Authentication に `chrome-extension://{ID}` を承認済みドメインとして追加

### ⑤ Phase 4: 管理者ダッシュボード（/admin）
→ Firebase Custom Claims でadminロール管理
→ 全ユーザーのセッション一覧・CSV出力

---

## Claude Code向けメモ

各フェーズ開始時に以下を伝えると効率的:

```
「SPEC.mdを読んで、Phase Nのタスク[X-X]を実装して」
```

実装時の注意点:
- Firebase Admin SDKのキーは環境変数 FIREBASE_SERVICE_ACCOUNT_JSON で管理
- 既存の parse.py / classify.py は変更しない（入力をFirestoreから取るだけ）
- Railwayはストレージ永続化なし → JSONデータは将来的にFirestoreに移行推奨
- opponents_summary.json は Phase5でFirestore移行を検討

---

## 未解決の選択事項（着手前に決める）

| 項目 | 選択肢A | 選択肢B |
|---|---|---|
| 既存アップロード画面 | 残す（後方互換） | 削除（Firestoreに統一） |
| Auth方式 | メール/パスワード | Googleログイン |
| adminロール管理 | Firestoreのadminsコレクション | Firebase Custom Claims（推奨） |
| PDFの保存先 | Railwayのまま（即DL前提） | Firebase Storage に移行 |
