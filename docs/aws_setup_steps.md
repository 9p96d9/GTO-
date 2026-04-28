# AWS ECS Fargate セットアップ手順書
## PokerGTO — Railway → AWS 移行（Phase 18 完了版）

> 実際に動作確認済みの手順をまとめたもの。次回同じ構成を作る際の再現手順として使える。

---

## 前提

| 項目 | 値 |
|---|---|
| リージョン | ap-northeast-1（東京） |
| アプリのポート | 5000 |
| ヘルスチェックパス | `/health` |
| Dockerイメージ管理 | GitHub Actions で自動ビルド・ECR push |

---

## STEP 1: IAMユーザー作成

**場所:** AWS Console（rootログイン） → IAM → ユーザー → ユーザーの作成

1. ユーザー名: `gto-admin-user`
2. 「AWSマネジメントコンソールへのアクセスを提供する」をチェック
3. 以下のポリシーをアタッチ:
   - `AmazonECS_FullAccess`
   - `AmazonEC2ContainerRegistryFullAccess`
   - `AmazonVPCFullAccess`
   - `ElasticLoadBalancingFullAccess`
   - `SecretsManagerReadWrite`
   - `CloudWatchFullAccess`
4. 作成後、アクセスキーを発行:
   - IAM → ユーザー → gto-admin-user → セキュリティ認証情報 → アクセスキー作成
   - 用途: 「CLIの使用」
   - `Access Key ID` と `Secret Access Key` を安全な場所に保存（画面を閉じると二度と見られない）

**以降はこのIAMユーザーでログインして作業する（rootは使わない）**

---

## STEP 2: AWS CLI セットアップ

```bash
# 設定
aws configure
# AWS Access Key ID     : [STEP1で取得]
# AWS Secret Access Key : [STEP1で取得]
# Default region name   : ap-northeast-1
# Default output format : json

# 確認
aws sts get-caller-identity
# → Account ID が表示されればOK
```

---

## STEP 3: ECR リポジトリ作成

**場所:** AWS Console → ECR → リポジトリの作成

| 項目 | 値 |
|---|---|
| 可視性 | プライベート |
| リポジトリ名 | `gto-app` |

作成後のURIを記録:
```
273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app
```

---

## STEP 4: Secrets Manager に環境変数を登録

**場所:** AWS Console → Secrets Manager → 新しいシークレットを保存

1. 種類: 「その他のシークレット」
2. キー/値ペアで以下をまとめて登録:

```json
{
  "FIREBASE_SERVICE_ACCOUNT_JSON": "{ ...JSONを1行に圧縮... }",
  "FIREBASE_API_KEY": "AIza...",
  "FIREBASE_AUTH_DOMAIN": "xxx.firebaseapp.com",
  "FIREBASE_PROJECT_ID": "xxx",
  "ADMIN_UID": "your-firebase-uid",
  "GEMINI_API_KEY": "xxx（任意）",
  "GROQ_API_KEY": "gsk_...",
  "PORT": "5000"
}
```

3. シークレット名: `gto/production`

作成後、**CLIでARNを取得して記録**（コンソール表示のARNはサフィックスが省略されている場合があり誤字の原因になる）:
```bash
aws secretsmanager describe-secret --secret-id gto/production --query ARN --output text
# → arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn
```

---

## STEP 5: セキュリティグループ作成

**場所:** AWS Console → VPC → セキュリティグループ → セキュリティグループの作成

### ① ALB用: `gto-alb-sg`

| ルール | タイプ | ポート | ソース |
|---|---|---|---|
| インバウンド | HTTP | 80 | 0.0.0.0/0 |
| インバウンド | HTTPS | 443 | 0.0.0.0/0 |
| アウトバウンド | すべて | - | 0.0.0.0/0 |

### ② ECSタスク用: `gto-ecs-sg`

| ルール | タイプ | ポート | ソース |
|---|---|---|---|
| インバウンド | カスタムTCP | 5000 | `gto-alb-sg` のSG-ID（IPアドレスではなくSG-IDを指定） |
| アウトバウンド | すべて | - | 0.0.0.0/0 |

---

## STEP 6: ECSタスク実行ロール作成

**場所:** AWS Console → IAM → ロール → ロールを作成

1. 信頼エンティティ: 「AWSのサービス」→「Elastic Container Service」→「Elastic Container Service Task」
2. 以下のポリシーをアタッチ:
   - `AmazonECSTaskExecutionRolePolicy`（ECRプル・CloudWatchログ書き込み）
   - `SecretsManagerReadWrite`
3. ロール名: `gto-ecs-task-execution-role`

4. 作成後、**インラインポリシーを追加**（Secrets Managerの特定シークレットへのアクセス許可）:
   - IAM → ロール → gto-ecs-task-execution-role → インラインポリシーを作成
   - ポリシー名: `gto-secrets-access`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-*"
    }
  ]
}
```

> **ポイント:** ResourceのARN末尾は `-cxsxWn` の完全一致ではなく `-*`（ワイルドカード）にすること。キー個別取得時にARNのバリエーションが生じるため。

---

## STEP 7: ECS クラスター作成

**場所:** AWS Console → ECS → クラスターの作成

| 項目 | 値 |
|---|---|
| クラスター名 | `gto-cluster` |
| インフラ | AWS Fargate（サーバーレス）のみ |

---

## STEP 8: ALB + ターゲットグループ作成

### 8-1. ターゲットグループを先に作成

**場所:** EC2 → ターゲットグループ → ターゲットグループの作成

| 項目 | 値 |
|---|---|
| **ターゲットタイプ** | **IP アドレス**（Fargateはこれ必須。インスタンスは不可） |
| ターゲットグループ名 | `gto-tg` |
| プロトコル | HTTP |
| ポート | 5000 |
| VPC | デフォルトVPC |
| ヘルスチェックパス | `/health` |

ターゲット登録はスキップ（ECSサービスが自動登録する）。

### 8-2. ALB 作成

**場所:** EC2 → ロードバランサー → Application Load Balancer

| 項目 | 値 |
|---|---|
| 名前 | `gto-alb` |
| スキーム | インターネット向け |
| VPC | デフォルトVPC |
| サブネット | AZが異なるサブネットを2つ以上選択 |
| セキュリティグループ | `gto-alb-sg` のみ |
| リスナー | HTTP:80 → `gto-tg` に転送 |

作成後のDNS名を記録:
```
gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com
```

---

## STEP 9: CloudWatch ロググループ作成

**場所:** AWS Console → CloudWatch → ロググループ → ロググループの作成

| 項目 | 値 |
|---|---|
| ロググループ名 | `/ecs/gto-app` |

> タスク定義に `awslogs-create-group: true` を設定しても自動作成されない場合があるため手動で作る。

---

## STEP 10: タスク定義を登録（aws/task-definition.json）

リポジトリの `aws/task-definition.json` を使ってタスク定義を登録:

```bash
aws ecs register-task-definition --cli-input-json file://aws/task-definition.json
```

または GitHub Actions の deploy.yml で自動登録される（STEP11参照）。

**task-definition.json のポイント:**
- `secrets` の `valueFrom` には**フルARN**を使う（シークレット名だけではSSM形式と誤認される）
- ARN形式: `arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn:KEY_NAME::`

---

## STEP 11: GitHub Actions シークレット登録 → 自動デプロイ

**場所:** GitHub → リポジトリ → Settings → Secrets and variables → Actions

| シークレット名 | 値 |
|---|---|
| `AWS_ACCESS_KEY_ID` | STEP1で取得したアクセスキーID |
| `AWS_SECRET_ACCESS_KEY` | STEP1で取得したシークレットキー |

登録後、`main`ブランチにpushすれば GitHub Actions が:
1. Dockerビルド
2. ECRにpush
3. タスク定義を更新
4. ECSサービスをローリングアップデート

---

## STEP 12: ECSサービス作成

**場所:** ECS → gto-cluster → サービス → 作成

| 項目 | 値 |
|---|---|
| 起動タイプ | Fargate |
| タスク定義 | `gto-task`（最新リビジョン） |
| サービス名 | `gto-service` |
| 必要なタスク数 | 1 |
| VPC | デフォルトVPC |
| サブネット | 2つ以上選択 |
| セキュリティグループ | `gto-ecs-sg` |
| パブリックIP | **有効**（ECRからのpullに必要） |
| ロードバランサー | Application Load Balancer → `gto-alb` |
| ターゲットグループ | `gto-tg` |

---

## STEP 13: 動作確認

ECSタスクが `RUNNING`、ターゲットグループが `healthy` になったら:

```
□ http://[ALB-DNS]/health → {"status":"ok"} が返る
□ http://[ALB-DNS]/ でランディングページが表示される
□ Googleログインが機能する（Firebase 承認済みドメインにALB DNSを追加する必要あり）
□ /sessions でセッション一覧が表示される
□ 解析パイプラインが動く
□ PDF出力が動く
□ CloudWatch Logs (/ecs/gto-app) にエラーがないか確認
```

**Firebase 承認済みドメインへの追加:**
Firebase Console → Authentication → Settings → 承認済みドメイン → ALBのDNS名を追加

---

## 現在の本番環境値（2026-04-27 完了時点）

| 項目 | 値 |
|---|---|
| AWS アカウントID | `273949555510` |
| リージョン | `ap-northeast-1`（東京） |
| ECRリポジトリ | `gto-app` |
| ECSクラスター | `gto-cluster` |
| ECSサービス | `gto-service` |
| タスク定義 | `gto-task` |
| ALB DNS | `gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com` |
| ターゲットグループ | `gto-tg`（IPタイプ・ポート5000） |
| Secrets Manager | `gto/production`（ARN末尾: `-cxsxWn`） |
| CloudWatch | `/ecs/gto-app` |
| IAMロール | `gto-ecs-task-execution-role` |
| AWSクレジット残高 | $97.95（2026-10-下旬まで有効） |

---

## よくあるミスと対処

| ミス | 症状 | 対処 |
|---|---|---|
| ターゲットグループをインスタンスタイプで作成 | ECSサービス作成時にエラー | IPアドレスタイプで再作成 |
| Secrets ManagerのARNをシークレット名だけで指定 | GitHub Actions でSSM形式エラー | フルARNで指定する |
| ARNのサフィックス誤字（`-cxxWn`など） | ResourceNotFoundException | CLIで正確なARNを取得して修正 |
| CloudWatchロググループが存在しない | AccessDeniedException（logs） | `/ecs/gto-app` を手動作成 |
| IAMインラインポリシーのResourceが完全一致 | GetSecretValue でアクセス拒否 | ARN末尾を `-*` のワイルドカードにする |
| ALBリスナーが古いターゲットグループを向いている | 503エラー | リスナーのデフォルトルールを正しいTGに変更 |
