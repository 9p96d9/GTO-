# PokerGTO Chrome拡張機能

## セットアップ

### 1. アイコン画像を用意する
`icons/` フォルダに以下のファイルを配置してください（PNG形式）:
- `icon16.png`  (16×16px)
- `icon48.png`  (48×48px)
- `icon128.png` (128×128px)

### 2. Chromeに読み込む
1. `chrome://extensions` を開く
2. 右上の「デベロッパーモード」をON
3. 「パッケージ化されていない拡張機能を読み込む」→ この `extension/` フォルダを選択

### 3. 必要な環境変数（Railwayに設定）
| 変数名 | 内容 |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebaseサービスアカウントキー（JSON全体） |
| `FIREBASE_API_KEY` | Firebase Web API Key |
| `FIREBASE_AUTH_DOMAIN` | `{project-id}.firebaseapp.com` |
| `FIREBASE_PROJECT_ID` | Firebaseプロジェクト ID |

### 4. Firebase設定（コンソール作業）
1. [console.firebase.google.com](https://console.firebase.google.com) でプロジェクト作成
2. Authentication → ログイン方法 → **Google** を有効化
3. Firestore → データベース作成（本番モード）
4. Firestoreセキュリティルール:
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
5. プロジェクト設定 → 全般 → マイアプリ → Webアプリを追加 → `apiKey`, `authDomain`, `projectId` を取得
6. プロジェクト設定 → サービスアカウント → 新しい秘密鍵を生成（JSON） → Railwayに設定

### 5. chrome.identity の OAuth クライアント設定
background.js は `chrome.identity.getAuthToken()` を使ってGoogleトークンを取得します。
これには Chrome Web Store に公開するか、`key` フィールドを manifest.json に設定する必要があります。

**ローカルテスト時:**
1. `chrome://extensions` → 拡張機能のIDを確認
2. [Google Cloud Console](https://console.cloud.google.com) → OAuth 2.0クライアント → Chrome拡張機能 → 拡張機能IDを登録
3. Firebase Authentication → 承認済みドメイン → `chrome-extension://{拡張機能ID}` を追加

## ファイル構成
```
extension/
├── manifest.json   # 拡張機能設定（MV3）
├── popup.html      # ポップアップUI
├── popup.js        # ポップアップロジック（ログイン・送信）
├── background.js   # Service Worker（Firebase Auth管理）
├── content.js      # T4ページのスクレイプ処理
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```
