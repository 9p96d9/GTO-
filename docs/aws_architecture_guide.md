# AWS インフラ構成 解説ガイド
## PokerGTO Phase 18 — Railway → AWS 移行

> このドキュメントは「何をどこに設定したか」と「なぜそうなっているか」を理解するための学習資料です。

---

## 全体構成図

```
インターネット
    │
    │ HTTP:80
    ▼
┌─────────────────────────────┐
│  ALB（Application Load      │  ← 入口。外部からの通信を受け取る
│  Balancer）gto-alb          │
└──────────────┬──────────────┘
               │ HTTP:5000
               ▼
┌─────────────────────────────┐
│  ECS Service（gto-service） │  ← アプリの「常駐管理者」
│  ┌─────────────────────┐    │
│  │  Fargate Task        │    │  ← アプリが実際に動くコンテナ
│  │  (gto-app コンテナ)  │    │
│  └─────────────────────┘    │
└──────────────┬──────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
    ▼                     ▼
┌────────┐         ┌──────────────────┐
│  ECR   │         │  Secrets Manager  │
│(イメージ│         │  (環境変数を安全   │
│ 倉庫)  │         │   に保管)          │
└────────┘         └──────────────────┘
    ▲                     ▲
    │                     │
    └──────────┬──────────┘
               │
┌──────────────┴──────────────┐
│  IAM Role                   │
│  gto-ecs-task-execution-role│  ← ECSがECR/Secrets Managerに
│                             │    アクセスする「許可証」
└─────────────────────────────┘

       ↑ 全体を囲む
┌─────────────────────────────┐
│  VPC（仮想ネットワーク）      │
│  ┌───────────┐ ┌─────────┐  │
│  │ gto-alb-sg│ │gto-ecs- │  │  ← セキュリティグループ
│  │(ALB用SG)  │ │sg(ECS用)│  │    （誰の通信を許可するか）
│  └───────────┘ └─────────┘  │
└─────────────────────────────┘

       ↓ ログの流れ
┌─────────────────────────────┐
│  CloudWatch Logs            │
│  /ecs/gto-app               │  ← アプリのログを記録・確認
└─────────────────────────────┘

       ↓ デプロイの流れ
GitHub push → GitHub Actions → ECR push → ECS deploy
```

---

## 各サービスの説明

### ALB（Application Load Balancer）
**= アプリケーション負荷分散装置**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Application Load Balancer |
| 役割 | インターネットからのHTTPリクエストを受け取り、ECSタスクに転送する「玄関口」 |
| 今回の設定名 | `gto-alb` |
| リスナー | HTTP:80 → ターゲットグループ `gto-tg` に転送 |
| DNS名 | `gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com` |

**なぜ必要？**
ECSのFargateタスクはIPアドレスが毎回変わる。ALBが固定の入口になることで、タスクが再起動しても同じURLでアクセスできる。

---

### ECS（Elastic Container Service）
**= Dockerコンテナの管理サービス**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Elastic Container Service |
| 役割 | Dockerコンテナを「何台動かすか」「死んだら再起動」などを自動管理 |
| 今回の設定 | クラスター `gto-cluster` / サービス `gto-service` / タスク定義 `gto-task` |

**3つの概念の違い：**

```
クラスター（gto-cluster）
  └── サービス（gto-service）← 「タスクを常時1台動かし続けて」と指示
        └── タスク（実行中のコンテナ）← 実際に動いているアプリ本体
              └── コンテナ（gto-app）← Dockerイメージから作られた箱
```

---

### Fargate
**= サーバー管理不要のコンテナ実行環境**

| 項目 | 内容 |
|---|---|
| 役割 | EC2（仮想サーバー）を自分で管理せずにコンテナを動かせる仕組み |
| メリット | OS・パッチ・スケーリングをAWSが全部やってくれる |
| 今回の設定 | CPU: 512 (0.5vCPU) / メモリ: 1024MB |

---

### ECR（Elastic Container Registry）
**= Dockerイメージの保管庫**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Elastic Container Registry |
| 役割 | ビルドしたDockerイメージを保存しておく場所（GitHubのDocker版） |
| 今回の設定 | リポジトリ名 `gto-app` |
| URI | `273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app` |

**流れ：**
```
コード変更 → GitHub Actions がDockerビルド → ECRにpush → ECSがECRから取得して起動
```

---

### IAM（Identity and Access Management）
**= AWS内の「許可証」管理**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Identity and Access Management |
| 役割 | 「誰が・何を・できる/できない」を管理する仕組み |
| 今回作ったもの | ロール `gto-ecs-task-execution-role` |

**ロールとは？**
人間ではなくAWSサービス自身に付与する「許可証」。
ECSタスクがECRからイメージを取得したりSecrets Managerから秘密情報を読むには、この許可証が必要。

**今回アタッチしたポリシー：**
```
AmazonECSTaskExecutionRolePolicy  ← ECRプル・CloudWatchログ書き込みを許可
SecretsManagerReadWrite           ← Secrets Manager読み書きを許可
gto-secrets-access（インライン）   ← 特定シークレットへのGetSecretValue許可
```

---

### Secrets Manager
**= 環境変数・APIキーの金庫**

| 項目 | 内容 |
|---|---|
| 役割 | APIキー・パスワードなどをコードに書かずに安全に管理 |
| 今回の設定 | シークレット名 `gto/production` |
| ARN | `arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn` |

**格納しているキー：**
`FIREBASE_SERVICE_ACCOUNT_JSON` / `FIREBASE_API_KEY` / `FIREBASE_AUTH_DOMAIN` /
`FIREBASE_PROJECT_ID` / `ADMIN_UID` / `GEMINI_API_KEY` / `GROQ_API_KEY` / `PORT`

**タスク定義での参照方法：**
```json
"valueFrom": "arn:aws:...:secret:gto/production-cxsxWn:FIREBASE_API_KEY::"
             ↑シークレットのARN                        ↑取得するキー名
```

---

### VPC（Virtual Private Cloud）
**= AWSの中の「プライベートなネットワーク空間」**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Virtual Private Cloud |
| 役割 | AWS上のリソースを隔離されたネットワークにまとめる |
| 今回の設定 | デフォルトVPCを使用 |

---

### セキュリティグループ（SG）
**= クラウド上のファイアウォール**

| 名前 | 役割 |
|---|---|
| `gto-alb-sg` | ALB用。インターネットからのHTTP(80)/HTTPS(443)を許可 |
| `gto-ecs-sg` | ECSタスク用。`gto-alb-sg`からのポート5000のみ許可（直接アクセス不可） |

```
インターネット → gto-alb-sg(80許可) → ALB → gto-ecs-sg(5000のみ許可) → ECSタスク
```

---

### CloudWatch Logs
**= AWSのログ収集・閲覧サービス**

| 項目 | 内容 |
|---|---|
| 役割 | ECSコンテナのstdout/stderrを収集して閲覧できる |
| 今回の設定 | ロググループ `/ecs/gto-app` |
| 確認場所 | AWS Console → CloudWatch → ロググループ → `/ecs/gto-app` |

---

### GitHub Actions（CI/CD）
**= コードのプッシュをトリガーに自動でビルド＆デプロイ**

| 項目 | 内容 |
|---|---|
| 略称の意味 | CI/CD = Continuous Integration / Continuous Delivery（継続的統合・配信） |
| トリガー | `main`ブランチへのpush |
| 設定ファイル | `.github/workflows/deploy.yml` |

**デプロイの流れ：**
```
git push → Actions起動
  ① AWS認証（GitHub Secretsのキーを使用）
  ② Dockerビルド
  ③ ECRにpush（:latest タグ + :コミットSHAタグ）
  ④ タスク定義のイメージを新しいものに更新
  ⑤ ECSサービスをローリングアップデート
  ⑥ ヘルスチェック通過まで待機
```

---

## トラブルシューティング記録（今回ハマったポイント）

| エラー | 原因 | 解決策 |
|---|---|---|
| ターゲットグループ選択エラー | instance型TGはFargateで使えない | IP型で `gto-tg` を新規作成 |
| ResourceNotFoundException | タスク定義のARNサフィックス誤字（`cxxWn`→`cxsxWn`） | CLIで正確なARNを取得して修正 |
| シークレット名形式エラー | `secretname:key::` はSSM形式と判定される | Secrets Manager使用時はフルARN必須 |
| AccessDeniedException（logs） | CloudWatchロググループが存在しない | `/ecs/gto-app` を手動作成 |

---

## 現在の本番環境情報

| 項目 | 値 |
|---|---|
| AWS アカウントID | 273949555510 |
| リージョン | ap-northeast-1（東京） |
| ECSクラスター | gto-cluster |
| ECSサービス | gto-service |
| タスク定義 | gto-task:8（最新） |
| ECRリポジトリ | gto-app |
| ALB DNS | gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com |
| Secrets Manager | gto/production（ARN末尾: -cxsxWn） |
| CloudWatch | /ecs/gto-app |
| IAMロール | gto-ecs-task-execution-role |
| RDS | gto-db（db.t4g.micro・PostgreSQL 18・VPC内） |
| RDS エンドポイント | gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432 |
| RDS SG | gto-rds-sg（ECSからの5432のみ許可） |
| Railway停止予定 | 2026-05-15 |

---

## Phase 19 追加構成（PostgreSQL）

```
インターネット
    │ HTTP:80
    ▼
  ALB（gto-alb）
    │ HTTP:5000
    ▼
  ECS Fargate（gto-app）
    ├── ECR
    ├── Secrets Manager
    │    ├── DATABASE_URL  ← NEW
    │    └── USE_POSTGRES  ← NEW
    └── RDS PostgreSQL（gto-db）← NEW・VPC内
         └── gto-rds-sg（ECSからのみ5432許可）
```

**切り替え方法:**
- `USE_POSTGRES=true` → PostgreSQL使用
- `USE_POSTGRES=false` → Firebase使用（コスト超過時の退避先）

**DB管理方法（ECS Exec）:**
```bash
# タスクID取得
aws ecs list-tasks --cluster gto-cluster --output text

# psqlでテーブル確認
aws ecs execute-command \
  --cluster gto-cluster \
  --task <タスクID> \
  --container gto-app \
  --interactive \
  --command "psql \$DATABASE_URL -c '\dt'"
```
※ ECS Execはサービスに`--enable-execute-command`が必要（設定済み）
