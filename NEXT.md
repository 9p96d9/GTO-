# 次回セッションでやること

作成日: 2026-04-03

---

## ステータスまとめ

| | 状態 |
|---|---|
| コード（Phase1〜3） | ✅ 完成・プッシュ済み |
| Firebaseコンソール設定 | ❌ 未着手（手動作業） |
| Railway環境変数 | ❌ 未設定 |
| Chrome拡張機能ロード | ❌ 未実施 |
| 動作確認 | ❌ 未実施 |

---

## Step 1【手動】Firebaseコンソール設定 〜10分

> ブラウザで https://console.firebase.google.com を開いてやる作業

1. プロジェクト作成（名前は何でもOK）
2. **Authentication** → ログイン方法 → **Google** を有効化
3. **Firestore Database** → データベースを作成 → 本番モード → リージョン選択（asia-northeast1 推奨）
4. Firestoreのセキュリティルールを以下に変更:
   ```
   rules_version = '2';
   service cloud.firestore.beta {
     match /databases/{database}/documents {
       match /users/{uid}/sessions/{sessionId} {
         allow read, write: if request.auth != null && request.auth.uid == uid;
       }
     }
   }
   ```
5. **プロジェクト設定（歯車）→ 全般 → マイアプリ → Webアプリを追加**
   → `apiKey`, `authDomain`, `projectId` をメモ
6. **プロジェクト設定 → サービスアカウント → 新しい秘密鍵を生成**
   → JSONファイルをダウンロード（中身を次のステップで使う）

---

## Step 2【手動】Railway環境変数を4つ追加 〜5分

> Railwayダッシュボード → プロジェクト → Variables

| 変数名 | 値 |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Step1-6のJSONファイルの中身をまるごと貼る |
| `FIREBASE_API_KEY` | Step1-5の `apiKey` |
| `FIREBASE_AUTH_DOMAIN` | Step1-5の `authDomain` |
| `FIREBASE_PROJECT_ID` | Step1-5の `projectId` |

設定後、Railwayが自動で再デプロイされる。

---

## Step 3【手動】Chrome拡張機能をChromeに読み込む 〜3分

1. `chrome://extensions` を開く
2. 右上「デベロッパーモード」をON
3. 「パッケージ化されていない拡張機能を読み込む」→ `extension/` フォルダを選択
4. **拡張機能のIDをメモする**（例: `abcdefghijklmnopabcdefghijklmnop`）

---

## Step 4【手動】OAuthクライアントにChrome拡張機能IDを登録 〜5分

1. https://console.cloud.google.com → 同じプロジェクト
2. APIとサービス → 認証情報 → OAuth 2.0 クライアントID → Chromeアプリを選択
3. アプリケーションID: Step3でメモした拡張機能IDを入力 → 保存
4. **Firebase Authentication → 承認済みドメイン → 追加**
   → `chrome-extension://{Step3のID}` を追加

---

## Step 5【動作確認】 〜5分

1. `https://gto-production.up.railway.app/login` にアクセス
   → Googleログインできるか確認
2. `https://gto-production.up.railway.app/sessions` にアクセス
   → セッション一覧が表示されるか確認（最初は空でOK）
3. Chrome拡張機能のポップアップを開いてGoogleログインできるか確認

---

## 動作確認OKなら次は

**「NEXT.mdを読んでPhase4を実装して」** と伝えればOK。

### Phase 4 概要: 管理者ダッシュボード
- Firebase Custom Claimsで `admin: true` を特定ユーザーに付与
- `GET /admin` — 全ユーザーのセッション一覧（adminのみアクセス可）
- ユーザー一覧・セッション閲覧・任意のセッションを解析にかける

---

## トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| `/login` が「Firebase未設定」と表示 | Railway環境変数が未設定 or デプロイ前 |
| 拡張機能でログイン失敗 | Step4のOAuth登録・承認済みドメイン追加が未完了 |
| セッション保存後に一覧に出ない | Firestoreセキュリティルールを確認 |
| 解析ボタンを押してもエラー | Railwayのログを確認（`FIREBASE_SERVICE_ACCOUNT_JSON` が正しいか） |
