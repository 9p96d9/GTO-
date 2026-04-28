# AWS インフラ構成 解説ガイド
## PokerGTO Phase 18/19 — Railway → AWS 移行

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
└──────────┬───────────────────┘
           │
    ┌──────┴──────────────────────┐
    │                             │
    ▼                             ▼
┌────────┐         ┌────────────────────────┐
│  ECR   │         │   Secrets Manager       │
│(イメージ│         │   gto/production        │
│ 倉庫)  │         │   ・Firebase系キー       │
└────────┘         │   ・GEMINI/GROQ         │
                   │   ・DATABASE_URL  ←新   │
                   │   ・USE_POSTGRES  ←新   │
                   └────────────────────────┘
                               │ DATABASE_URL
                               ▼
               ┌───────────────────────────────┐
               │  RDS PostgreSQL  ←Phase19追加 │
               │  gto-db / t4g.micro            │
               │  VPC内・外部から直接アクセス不可 │
               └───────────────────────────────┘

    ↑ アクセス制御
┌─────────────────────────────┐
│  IAM Role                   │
│  gto-ecs-task-execution-role│  ← ECSがECR/Secrets Managerに
└─────────────────────────────┘    アクセスする「許可証」

       ↑ 全体を囲む
┌──────────────────────────────────────┐
│  VPC（仮想ネットワーク）               │
│  ┌───────────┐ ┌─────────┐ ┌───────┐ │
│  │ gto-alb-sg│ │gto-ecs- │ │gto-   │ │  ← セキュリティグループ
│  │(ALB用SG)  │ │sg(ECS用)│ │rds-sg │ │
│  └───────────┘ └─────────┘ └───────┘ │
└──────────────────────────────────────┘

       ↓ ログの流れ
┌─────────────────────────────┐
│  CloudWatch Logs            │
│  /ecs/gto-app               │  ← アプリのログを記録・確認
└─────────────────────────────┘

       ↓ デプロイの流れ
GitHub push → GitHub Actions → ECR push → ECS deploy

       ↓ DB切り替えの仕組み
USE_POSTGRES=false → firebase_utils.py（既存・Firestore）
USE_POSTGRES=true  → postgres_utils.py（新・RDS PostgreSQL）
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

### RDS（Relational Database Service）— Phase 19追加
**= AWSが管理するPostgreSQLサーバー**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Relational Database Service |
| 役割 | PostgreSQLデータベースをAWSが管理（バックアップ・パッチ自動化） |
| 今回の設定 | インスタンス名 `gto-db` / タイプ `db.t4g.micro` / PostgreSQL 18 |
| エンドポイント | `gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432` |
| 配置 | VPC内のプライベート配置（インターネットから直接アクセス不可） |
| セキュリティグループ | `gto-rds-sg`（`gto-ecs-sg`からのポート5432のみ許可） |

**なぜFirestoreからPostgreSQLに移行するのか？**
FirestoreはNoSQLで柔軟だが、複数ユーザーをまたぐ集計・JOINが苦手。PostgreSQLにすることでアドミンダッシュボードの高度な分析が可能になる。

**テーブル構成：**
```
users         ← Firebase UIDと紐付けるユーザーテーブル
hands         ← ハンド生データ（JSON保存・user_idでFK）
analyses      ← 解析結果スナップショット（user_idでFK）
ai_results    ← AI解析テキスト（analysis_idでFK）
carts         ← 保存済みカート（user_idでFK）
user_settings ← APIキー等の設定（user_idでFK・1ユーザー1レコード）
```

**マイグレーション管理（Alembic）：**

スキーマの変更履歴をコードで管理するツール。Gitのコミット履歴のDB版。

```bash
# スキーマをDBに適用（初回・更新時）
alembic upgrade head

# 新しい変更ファイルを作成
alembic revision --autogenerate -m "add_xxx_column"

# 現在の状態確認
alembic current
```

**DB切り替えの仕組み（`scripts/db.py`）：**

```
USE_POSTGRES=false（デフォルト）→ firebase_utils.py を使う
USE_POSTGRES=true              → postgres_utils.py を使う
Firebase Auth（ログイン認証）は常にFirebaseのまま
```

コスト超過時や障害時に `USE_POSTGRES=false` に戻すだけでFirebaseに即切り戻しできる。

---

### IAM（Identity and Access Management）
**= AWS内の「許可証」管理**

| 項目 | 内容 |
|---|---|
| 略称の意味 | Identity and Access Management |
| 役割 | 「誰が・何を・できる/できない」を管理する仕組み |
| 今回作ったもの | ロール `gto-ecs-task-execution-role` |

**ロールとは？**
人間ではなくAWSサービス自身に付与する「許可証」。ECSタスクがECRからイメージを取得したりSecrets Managerから秘密情報を読むには、この許可証が必要。

**アタッチしたポリシー：**
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

**格納しているキー（Phase 19追加分含む）：**

| キー名 | 内容 | 追加時期 |
|---|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase SA JSON | Phase 18 |
| `FIREBASE_API_KEY` | Firebase Web APIキー | Phase 18 |
| `FIREBASE_AUTH_DOMAIN` | Firebaseドメイン | Phase 18 |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID | Phase 18 |
| `ADMIN_UID` | 管理者Firebase UID | Phase 18 |
| `GEMINI_API_KEY` | Gemini APIキー | Phase 18 |
| `GROQ_API_KEY` | Groq APIキー | Phase 18 |
| `PORT` | `5000` | Phase 18 |
| `DATABASE_URL` | PostgreSQL接続文字列 | **Phase 19** |
| `USE_POSTGRES` | `true` / `false` | **Phase 19** |

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
| `gto-rds-sg` | RDS用。`gto-ecs-sg`からのポート5432のみ許可（Phase 19追加） |

```
インターネット
  → gto-alb-sg（80許可）
  → ALB
  → gto-ecs-sg（5000のみ許可）
  → ECSタスク
  → gto-rds-sg（5432のみ許可）
  → RDS PostgreSQL
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

## トラブルシューティング記録（ハマったポイント）

| エラー | 原因 | 解決策 |
|---|---|---|
| ターゲットグループ選択エラー | instance型TGはFargateで使えない | IP型で `gto-tg` を新規作成 |
| ResourceNotFoundException | タスク定義のARNサフィックス誤字（`cxxWn`→`cxsxWn`） | CLIで正確なARNを取得して修正 |
| シークレット名形式エラー | `secretname:key::` はSSM形式と判定される | Secrets Manager使用時はフルARN必須 |
| AccessDeniedException（logs） | CloudWatchロググループが存在しない | `/ecs/gto-app` を手動作成 |
| 503エラー | ALBリスナーが古いTGを向いていた | デフォルトルールを `gto-tg` に変更 |

---

## 現在の本番環境情報

| 項目 | 値 |
|---|---|
| AWS アカウントID | 273949555510 |
| リージョン | ap-northeast-1（東京） |
| ECSクラスター | gto-cluster |
| ECSサービス | gto-service |
| タスク定義 | gto-task（最新版） |
| ECRリポジトリ | gto-app |
| ALB DNS | gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com |
| Secrets Manager | gto/production（ARN末尾: -cxsxWn） |
| CloudWatch | /ecs/gto-app |
| IAMロール | gto-ecs-task-execution-role |
| RDS インスタンス | gto-db（db.t4g.micro・PostgreSQL 18） |
| RDS エンドポイント | gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432 |
| RDS セキュリティグループ | gto-rds-sg |
| DB切り替え | `USE_POSTGRES` 環境変数（Secrets Manager管理） |
| AWSクレジット残高 | $97.95（2026-10月下旬まで） |
| Railway停止予定 | 2026-05-15 |
