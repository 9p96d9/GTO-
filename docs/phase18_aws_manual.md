# Phase 18: Railway → AWS 移行 手動作業手順書

> **役割分担:** このファイルは**手動コンソール作業担当**向け。コーディング作業は別の Claude Code インスタンスが担当。
> **作業完了後:** 取得した ARN・ID・DNS名を `docs/phase18_aws_values.md` に記録して共有する。

---

## 前提情報

| 項目 | 内容 |
|---|---|
| AWSクレジット | $100 / 185日（〜2026-10-24） |
| 月額概算 | ALB ~$17 + Fargate ~$4 + その他 ~$1 = **約$22/月** |
| 移行元 | Railway（Docker・mainブランチ自動デプロイ） |
| 現行URL | https://gto-production.up.railway.app |
| リージョン | ap-northeast-1（東京）に統一 |

---

## 必要な環境変数（Secrets Manager に登録する対象）

Railwayの「Variables」タブで確認できる。

| 変数名 | 内容 | 取得元 |
|---|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase SAのJSONまるごと（改行なし1行） | Firebaseコンソール → プロジェクト設定 → サービスアカウント |
| `FIREBASE_API_KEY` | Firebase Web API Key | Firebaseコンソール → プロジェクト設定 → 全般 |
| `FIREBASE_AUTH_DOMAIN` | `<project>.firebaseapp.com` | 同上 |
| `FIREBASE_PROJECT_ID` | プロジェクトID | 同上 |
| `ADMIN_UID` | あなたの Firebase UID | Firebase → Authentication → ユーザー一覧 |
| `GROQ_API_KEY` | Groq APIキー（任意） | console.groq.com |
| `PORT` | `5000` | 固定値 |

---

## STEP 1: IAMユーザー作成（rootを使わない）

**場所:** AWS Console → IAM → ユーザー → ユーザーの作成

1. ユーザー名: `gto-admin-user`
2. 「AWSマネジメントコンソールへのアクセスを提供する」をチェック
3. ポリシーをアタッチ（以下を全て選択）:
   - `AmazonECS_FullAccess`
   - `AmazonEC2ContainerRegistryFullAccess`
   - `AmazonVPCFullAccess`
   - `ElasticLoadBalancingFullAccess`
   - `AWSCertificateManagerFullAccess`
   - `SecretsManagerReadWrite`
   - `CloudWatchFullAccess`
4. 作成後: **アクセスキーも発行**
   - IAM → ユーザー → gto-admin-user → セキュリティ認証情報 → アクセスキー作成
   - 用途: 「CLIの使用」
   - `Access Key ID` と `Secret Access Key` を安全な場所に保存

5. **以降はこのIAMユーザーでログインして作業する**（rootは使わない）

---

## STEP 2: AWS CLI セットアップ（ローカルPC）

```bash
# インストール（未導入の場合）
# https://aws.amazon.com/cli/ からダウンロード・インストール

# 設定
aws configure
# AWS Access Key ID: [STEP1で取得したもの]
# AWS Secret Access Key: [STEP1で取得したもの]
# Default region name: ap-northeast-1
# Default output format: json

# 確認
aws sts get-caller-identity
# → Account IDが表示されればOK
```

**Account IDを記録する** → `docs/phase18_aws_values.md` に記載

---

## STEP 3: ECR リポジトリ作成

**場所:** AWS Console → ECR → リポジトリの作成

1. 可視性: プライベート
2. リポジトリ名: `gto-app`
3. 設定はデフォルトでOK → 作成

**作成後に表示される URI を記録する:**
```
[account-id].dkr.ecr.ap-northeast-1.amazonaws.com/gto-app
```

> Docker イメージの **ビルド・push はコーディング担当が実施**。STEP3はリポジトリ作成のみ。

---

## STEP 4: Secrets Manager に環境変数を登録

**場所:** AWS Console → Secrets Manager → 新しいシークレットを保存

1. 種類: 「その他のシークレット」
2. キー/値ペアで以下を1シークレットにまとめて登録:

```json
{
  "FIREBASE_SERVICE_ACCOUNT_JSON": "{ ... JSONの中身（1行に圧縮）... }",
  "FIREBASE_API_KEY": "AIza...",
  "FIREBASE_AUTH_DOMAIN": "xxx.firebaseapp.com",
  "FIREBASE_PROJECT_ID": "xxx",
  "ADMIN_UID": "your-firebase-uid",
  "GROQ_API_KEY": "gsk_...",
  "PORT": "5000"
}
```

3. シークレット名: `gto/production`
4. その他の設定はデフォルトでOK → 作成

**作成後に表示されるARNを記録する:**
```
arn:aws:secretsmanager:ap-northeast-1:[account-id]:secret:gto/production-xxxxxx
```

---

## STEP 5: VPC + セキュリティグループ設定

**場所:** AWS Console → VPC

### 5-1. デフォルトVPCの確認

- VPC一覧でデフォルトVPC（「デフォルト: はい」）のIDを記録
- サブネット一覧でそのVPCに属するサブネットID（2つ以上）を記録

### 5-2. セキュリティグループ作成

**場所:** VPC → セキュリティグループ → セキュリティグループの作成

**① ALB用: `gto-alb-sg`**

| ルール | タイプ | ポート | ソース |
|---|---|---|---|
| インバウンド | HTTP | 80 | 0.0.0.0/0 |
| インバウンド | HTTPS | 443 | 0.0.0.0/0 |
| アウトバウンド | 全てのトラフィック | - | 0.0.0.0/0 |

**② ECS用: `gto-ecs-sg`**

| ルール | タイプ | ポート | ソース |
|---|---|---|---|
| インバウンド | カスタムTCP | 5000 | `gto-alb-sg` のSG-IDを指定 |
| アウトバウンド | 全てのトラフィック | - | 0.0.0.0/0 |

> `gto-ecs-sg` のインバウンドのソースにはIPではなく **`gto-alb-sg`のSGID** を指定する（ALBからのみ受け付ける）

**作成した両SGのIDを記録する。**

---

## STEP 6: ECS タスク実行ロール作成（IAM）

**場所:** AWS Console → IAM → ロール → ロールを作成

1. 信頼エンティティ: 「AWSのサービス」→「Elastic Container Service」→「Elastic Container Service Task」
2. ポリシーをアタッチ:
   - `AmazonECSTaskExecutionRolePolicy`（ECRからイメージpull・CloudWatchログ書き込み用）
   - `SecretsManagerReadWrite`（Secrets Manager参照用）
3. ロール名: `gto-ecs-task-execution-role`

---

## STEP 7: ECS Fargate クラスター作成

**場所:** AWS Console → ECS → クラスターの作成

| 項目 | 値 |
|---|---|
| クラスター名 | `gto-cluster` |
| インフラ | AWS Fargate（サーバーレス）のみチェック |

作成後、クラスター名を確認して記録。

---

## STEP 8: ALB + ターゲットグループ作成

**場所:** EC2 → ロードバランサー → ロードバランサーの作成 → Application Load Balancer

### 8-1. ターゲットグループを先に作成

**場所:** EC2 → ターゲットグループ → ターゲットグループの作成

| 項目 | 値 |
|---|---|
| ターゲットタイプ | **IP アドレス** |
| ターゲットグループ名 | `gto-target-group` |
| プロトコル | HTTP |
| ポート | **5000** |
| VPC | デフォルトVPC |
| ヘルスチェックパス | `/health` |

ターゲット登録ステップはスキップしてOK（ECSサービスが自動登録する）。

### 8-2. ALB 作成

| 項目 | 値 |
|---|---|
| 名前 | `gto-alb` |
| スキーム | インターネット向け |
| IPアドレスタイプ | IPv4 |
| VPC | デフォルトVPC |
| サブネット | AZが異なるサブネットを2つ以上選択 |
| セキュリティグループ | `gto-alb-sg` のみ |
| リスナー | HTTP:80 → ターゲットグループ `gto-target-group` |

作成後、**ALBのDNS名を記録する:**
```
gto-alb-xxxxxxxxx.ap-northeast-1.elb.amazonaws.com
```

---

## STEP 9: ACM SSL証明書発行（独自ドメインがある場合）

**場所:** AWS Console → Certificate Manager（リージョン: ap-northeast-1）

1. 「パブリック証明書をリクエスト」
2. ドメイン名: `yourdomain.com` と `*.yourdomain.com`
3. 検証方法: **DNS検証**
4. 発行された CNAME レコードをドメインのDNSに追加
5. ステータスが「発行済み」になるまで待つ（5〜30分）

**独自ドメインがない場合:** このステップをスキップ（ALBのDNS名でHTTP接続）

---

## STEP 10: ALB HTTPSリスナー追加（独自ドメイン・ACM証明書がある場合）

**場所:** EC2 → ロードバランサー → gto-alb → リスナー

| リスナー | アクション |
|---|---|
| HTTP:80（既存） | 「HTTPS:443にリダイレクト」に変更 |
| HTTPS:443（新規追加） | `gto-target-group` に転送 / 証明書はACMで選択 |

---

## STEP 11: ECSサービス作成

**場所:** ECS → gto-cluster → サービス → 作成

> **タスク定義の作成はコーディング担当が実施**（JSONで管理）。  
> コーディング担当からタスク定義が登録されたことを確認してからサービス作成する。

| 項目 | 値 |
|---|---|
| 起動タイプ | Fargate |
| タスク定義 | `gto-task`（コーディング担当が登録） |
| サービス名 | `gto-service` |
| 必要なタスク数 | 1 |
| VPC | デフォルトVPC |
| サブネット | 2つ以上選択 |
| セキュリティグループ | `gto-ecs-sg` |
| パブリックIP | **有効**（ECRからイメージpullのため必須） |
| ロードバランサー | Application Load Balancer → `gto-alb` |
| ターゲットグループ | `gto-target-group` |

---

## STEP 12: 動作確認チェックリスト

ECSサービスのタスクが「RUNNING」になり、ターゲットグループのヘルスチェックが「healthy」になったら確認する。

```
□ http://[ALB-DNS]/ でランディングページが表示される
□ Googleログインが機能する
□ /sessions でセッション一覧が表示される
□ 解析パイプラインが動く（ジョブ投入 → 結果表示）
□ /admin にアクセスできる（管理者UIDでのみ）
□ PDF出力が動く
□ CloudWatch Logs (/ecs/gto-app) にエラーがないか確認
```

---

## STEP 13: Railway停止（全確認後）

全動作確認が完了してから:

1. Railway → プロジェクト → Settings → General → **Delete Project** または Suspend

---

## 記録シート（作業中に埋める）

```
AWS Account ID        : 273949555510
ECR URI               : 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app
Secrets Manager ARN   : arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxxWn
VPC ID                : （デフォルトVPC使用）
Subnet IDs            : （デフォルトVPC配下のサブネット使用）
ALB SG ID (gto-alb-sg): 作成済み
ECS SG ID (gto-ecs-sg): 作成済み
Task Execution Role ARN: arn:aws:iam::273949555510:role/gto-ecs-task-execution-role
ALB DNS Name          : 要記録
Target Group ARN (実際): gto-tg（IPタイプ・ポート5000）
ECS Cluster Name      : gto-cluster
ECS Service Name      : gto-service
```

---

## 作業ログ（2026-04-24）

### 完了した作業

- STEP 1〜8: IAM・ECR・Secrets Manager・VPC・SG・ECSクラスター・ALB 全て作成完了
- STEP 6 補足: タスク実行ロール `gto-ecs-task-execution-role` 作成後、**SecretsManagerReadWriteポリシーだけでは不十分だった**。後述のインラインポリシーが必要。
- GitHub Secrets に `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` を登録済み
- GitHub Actions が ECR への push に成功し、タスク定義 `gto-task:3` が登録済み
- STEP 11: ECSサービス `gto-service` 作成済み

### 発生したトラブルと対処

**① ターゲットグループの型が合わない**
- 症状: ECSサービス作成時にターゲットグループ選択でエラー
- 原因: 最初に作った `gto-target-group` がインスタンスタイプ。Fargate(awsvpc)はIPタイプが必要
- 対処: 新規で `gto-tg`（IPアドレスタイプ・ポート5000・ヘルスチェック `/health`）を作成し、ALBリスナーに優先度1で追加

**② ResourceInitializationError（Secrets Manager）**
- 症状: タスク起動直後に停止。ログに `ResourceNotFoundException: Secrets Manager can't find the specified secret`
- 原因: IAMインラインポリシーのResourceARNが完全一致指定だった。key-extraction形式（`:KEY_NAME::`）を使うとARNのバリエーションがあるためワイルドカードが必要
- 対処: IAM → ロール → `gto-ecs-task-execution-role` → インラインポリシー `gto-secrets-access` を以下の内容で作成・更新

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxxWn*"
    }
  ]
}
```

### 現在の状態（2026-04-24 15:25時点）

- IAMポリシー更新後にECSサービスを「新しいデプロイの強制」で再起動済み
- タスクが `RUNNING` になるか確認が必要（再開時に確認）

### 再開時にやること

1. ECS → gto-cluster → gto-service → タスク一覧でステータス確認
2. タスクが `RUNNING` になっていれば → `http://[ALB-DNS]/health` にアクセスして `{"status":"ok"}` が返るか確認
3. ヘルスチェックOKなら STEP 12 の動作確認チェックリストへ
4. タスクがまだ失敗している場合 → CloudWatch Logs `/ecs/gto-app` でエラー内容を確認
