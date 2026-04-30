# AWS インフラ構成・運用ガイド
## PokerGTO Phase 18/19 — Railway → AWS 移行

---

## 全体構成図

```
インターネット
    │ HTTP:80
    ▼
ALB（gto-alb）                ← 固定入口。FargateはIPが毎回変わるため必要
    │ HTTP:5000
    ▼
ECS Fargate（gto-app）        ← コンテナを常時1台管理・再起動自動化
    ├── ECR                   ← Dockerイメージ保管庫
    ├── Secrets Manager       ← 環境変数の金庫（gto/production）
    │    ├── Firebase系キー / GEMINI / GROQ / PORT
    │    ├── DATABASE_URL     ← Phase 19追加
    │    └── USE_POSTGRES     ← Phase 19追加
    └── RDS PostgreSQL（gto-db）← Phase 19追加・VPC内・外部直接アクセス不可

IAM Role（gto-ecs-task-execution-role）← ECSがECR/Secrets Managerにアクセスする許可証

VPC内セキュリティグループ（通信の壁）
  gto-alb-sg … ALB用。80/443をインターネットから許可
  gto-ecs-sg … ECS用。gto-alb-sgからの5000のみ許可
  gto-rds-sg … RDS用。gto-ecs-sgからの5432のみ許可

通信フロー:
  インターネット → gto-alb-sg:80 → ALB → gto-ecs-sg:5000 → ECS → gto-rds-sg:5432 → RDS

デプロイフロー:
  git push → GitHub Actions → Dockerビルド → ECR push → ECSローリングアップデート

DB切り替え:
  USE_POSTGRES=false → firebase_utils.py（Firestore）
  USE_POSTGRES=true  → postgres_utils.py（RDS PostgreSQL）
  ※ Firebase Auth（ログイン認証）は常にFirebaseのまま
```

---

## AWSサービス一覧

| サービス | 略称の意味 | 役割・今回の設定 |
|---|---|---|
| ALB | Application Load Balancer | HTTP:80受信→ECSへ転送 / 設定名: gto-alb |
| ECS | Elastic Container Service | コンテナ管理 / cluster: gto-cluster, service: gto-service |
| Fargate | （サーバーレスコンテナ基盤） | EC2不要でコンテナ実行 / 0.5vCPU・1024MB |
| ECR | Elastic Container Registry | Dockerイメージ倉庫 / リポジトリ名: gto-app |
| RDS | Relational Database Service | PostgreSQL管理サービス / gto-db・t4g.micro |
| IAM | Identity and Access Management | AWSの許可証管理 / role: gto-ecs-task-execution-role |
| SM | Secrets Manager | APIキー・環境変数の金庫 / シークレット名: gto/production |
| VPC | Virtual Private Cloud | AWS内の仮想ネットワーク / デフォルトVPC使用 |
| SG | Security Group | クラウドのファイアウォール / 3つ作成 |
| CW | CloudWatch Logs | コンテナログ収集 / ロググループ: /ecs/gto-app |
| CI/CD | Continuous Integration/Delivery | GitHub Actions / mainブランチpushで自動デプロイ |

**IAMアタッチポリシー:**
- `AmazonECSTaskExecutionRolePolicy` … ECRプル・CloudWatchログ書き込み
- `SecretsManagerReadWrite` … Secrets Manager読み書き
- `gto-secrets-access`（インライン）… 特定シークレットの GetSecretValue

**Secrets Manager キー一覧:**

| キー名 | 内容 | 追加時期 |
|---|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase SA JSON（1行に圧縮） | Phase 18 |
| `FIREBASE_API_KEY` | Firebase Web APIキー | Phase 18 |
| `FIREBASE_AUTH_DOMAIN` | Firebaseドメイン | Phase 18 |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID | Phase 18 |
| `ADMIN_UID` | 管理者Firebase UID | Phase 18 |
| `GEMINI_API_KEY` | Gemini APIキー | Phase 18 |
| `GROQ_API_KEY` | Groq APIキー | Phase 18 |
| `PORT` | `5000` | Phase 18 |
| `DATABASE_URL` | PostgreSQL接続文字列 | Phase 19 |
| `USE_POSTGRES` | `true` / `false` | Phase 19 |

**タスク定義でのSecrets Manager参照形式（フルARN必須）:**
```json
"valueFrom": "arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn:キー名::"
```

---

## セットアップ手順（Phase 18）

### STEP 1: IAMユーザー作成（rootを使わない）

1. ユーザー名: `gto-admin-user`
2. 「AWSマネジメントコンソールへのアクセスを提供する」をチェック
3. ポリシーをアタッチ: `AmazonECS_FullAccess` / `AmazonEC2ContainerRegistryFullAccess` / `AmazonVPCFullAccess` / `ElasticLoadBalancingFullAccess` / `SecretsManagerReadWrite` / `CloudWatchFullAccess`
4. アクセスキーも発行（IAM → ユーザー → セキュリティ認証情報 → アクセスキー作成 → 用途: CLI）

### STEP 2: AWS CLI セットアップ

```bash
aws configure
# Access Key ID / Secret Access Key / ap-northeast-1 / json

aws sts get-caller-identity  # Account ID が表示されればOK
```

### STEP 3: ECR リポジトリ作成

- 可視性: プライベート / リポジトリ名: `gto-app`
- 作成後URI: `273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app`

### STEP 4: Secrets Manager に環境変数を登録

1. 種類: 「その他のシークレット」（キー/値ペア）
2. シークレット名: `gto/production`
3. ARNはCLIで取得（コンソール表示はサフィックスが省略される場合がある）:

```bash
aws secretsmanager describe-secret --secret-id gto/production --query ARN --output text
# → arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn
```

### STEP 5: セキュリティグループ作成

**① ALB用: `gto-alb-sg`**
- インバウンド: HTTP:80 / HTTPS:443 → 0.0.0.0/0
- アウトバウンド: すべて

**② ECSタスク用: `gto-ecs-sg`**
- インバウンド: カスタムTCP:5000 → `gto-alb-sg` のSG-ID（IPではなくSG-IDを指定）
- アウトバウンド: すべて

### STEP 6: ECSタスク実行ロール作成

1. 信頼エンティティ: 「Elastic Container Service Task」
2. ポリシー: `AmazonECSTaskExecutionRolePolicy` + `SecretsManagerReadWrite`
3. ロール名: `gto-ecs-task-execution-role`
4. インラインポリシー `gto-secrets-access` を追加:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["secretsmanager:GetSecretValue"],
    "Resource": "arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-*"
  }]
}
```

> ARN末尾は `-cxsxWn` 完全一致ではなく `-*`（ワイルドカード）にする。キー個別取得時にARNのバリエーションが生じるため。

### STEP 7: ECSクラスター作成

- クラスター名: `gto-cluster` / インフラ: AWS Fargate（サーバーレス）

### STEP 8: ターゲットグループ → ALB作成

**8-1. ターゲットグループ先に作成**

| 項目 | 値 |
|---|---|
| **ターゲットタイプ** | **IP アドレス**（Fargateはこれ必須。インスタンスは不可） |
| ターゲットグループ名 | `gto-tg` |
| プロトコル / ポート | HTTP / 5000 |
| VPC | デフォルトVPC |
| ヘルスチェックパス | `/health` |
| ヘルスチェック間隔 | 10秒（デプロイ高速化） |
| 正常しきい値 | 2回（デプロイ高速化） |

ターゲット登録はスキップ（ECSサービスが自動登録する）。

**8-2. ALB作成**

- 名前: `gto-alb` / スキーム: インターネット向け / SG: `gto-alb-sg` / リスナー: HTTP:80 → `gto-tg`

### STEP 9: CloudWatch ロググループ作成

- ロググループ名: `/ecs/gto-app`（`awslogs-create-group: true` だけでは自動作成されない場合があるため手動で作る）

### STEP 10: タスク定義を登録

```bash
aws ecs register-task-definition --cli-input-json file://aws/task-definition.json
# または GitHub Actions の deploy.yml で自動登録される
```

### STEP 11: GitHub Actions シークレット登録

GitHub → リポジトリ → Settings → Secrets and variables → Actions

| シークレット名 | 値 |
|---|---|
| `AWS_ACCESS_KEY_ID` | STEP1で取得したアクセスキーID |
| `AWS_SECRET_ACCESS_KEY` | STEP1で取得したシークレットキー |

mainブランチにpushすると自動でビルド・ECR push・ECSデプロイ。

### STEP 12: ECSサービス作成

| 項目 | 値 |
|---|---|
| 起動タイプ | Fargate |
| タスク定義 | `gto-task`（最新リビジョン） |
| サービス名 | `gto-service` |
| 必要なタスク数 | 1 |
| セキュリティグループ | `gto-ecs-sg` |
| パブリックIP | **有効**（ECRからのpullに必要） |
| ロードバランサー | `gto-alb` → `gto-tg` |

### STEP 13: 動作確認

```
□ http://[ALB-DNS]/health → {"status":"ok"} が返る
□ ランディングページが表示される
□ Googleログインが機能する（Firebase承認済みドメインにALB DNSを追加すること）
□ /sessions でセッション一覧が表示される
□ 解析パイプラインが動く
□ CloudWatch Logs (/ecs/gto-app) にエラーがないか確認
```

---

## PostgreSQL追加手順（Phase 19）

### STEP 19-1: RDS作成

- インスタンス名: `gto-db` / タイプ: `db.t4g.micro` / PostgreSQL 18
- VPC内・パブリックアクセス: なし
- セキュリティグループ: `gto-rds-sg`（`gto-ecs-sg` からのポート5432のみ許可）

### STEP 19-2〜6: コード追加（実施済み）

- `requirements.txt`: `sqlalchemy` / `psycopg2-binary` / `alembic` を追加
- `scripts/postgres_utils.py`: firebase_utils と同じシグネチャで実装
- `scripts/db.py`: USE_POSTGRES フラグで振り分けるラッパー
- `routes/*.py`: import を firebase_utils → db に変更

### STEP 19-7: Secrets Manager にキー追加（実施済み）

- `DATABASE_URL`: `postgresql://user:pass@エンドポイント:5432/dbname`
- `USE_POSTGRES`: `true`（falseにするとFirebaseに即切り戻し可能）

### STEP 19-8: ECSタスク定義更新（実施済み）

`aws/task-definition.json` の secrets セクションに DATABASE_URL・USE_POSTGRES を追加。

### STEP 19-9: 動作確認

- `/admin/analytics` で「✓ PostgreSQLモード」バナーが出ること
- 拡張機能からハンドを送信 → PostgreSQL に保存されること

---

## PostgreSQL テーブル設計

```sql
users         (id PK, firebase_uid VARCHAR UNIQUE NOT NULL, email, created_at, deleted_at)
hands         (id PK, user_id FK→users, hand_id VARCHAR UNIQUE NOT NULL,
               hand_json JSONB NOT NULL, captured_at, saved_at NOT NULL)
analyses      (id PK, user_id FK→users, job_id VARCHAR UNIQUE NOT NULL,
               created_at NOT NULL, hand_count, blue_count, red_count, pf_count,
               categories JSONB, classified_snapshot TEXT, snapshot_encoding,
               active_cart JSONB, deleted_at)
ai_results    (id PK, analysis_id FK→analyses, hand_number NOT NULL,
               ai_text TEXT, analyzed_at)
carts         (id PK, user_id FK→users, cart_id VARCHAR UNIQUE NOT NULL,
               job_id NOT NULL, name, hand_numbers JSONB, created_at NOT NULL)
user_settings (id PK, user_id FK→users UNIQUE, encrypted_api_key TEXT,
               needs_api_auto_cart BOOL DEFAULT FALSE, updated_at)
analysis_hands(id PK, analysis_id FK→analyses, hand_number INT NOT NULL,
               line VARCHAR(10), category_label VARCHAR(100), position VARCHAR(10),
               captured_at TIMESTAMPTZ)
```

**インデックス:** `hands(user_id, saved_at DESC)` / `analyses(user_id, created_at DESC)` / `analysis_hands(line, position, captured_at)`

**Alembicコマンド:**
```bash
alembic upgrade head                          # 最新スキーマ適用
alembic revision --autogenerate -m "説明"     # 変更ファイル作成
alembic current                               # 現在の状態確認
alembic downgrade -1                          # 1つ前に戻す
```

**スキーマ設計の注意点:**
- インデックス漏れ → user_id / saved_at / job_id には必ず INDEX
- 全カラム TIMESTAMPTZ（UTC）に統一
- 論理削除は `deleted_at TIMESTAMPTZ NULL` で管理
- 外部キー制約を必ず設定（孤立レコード防止）

---

## PostgreSQL で解決できること（Firestoreでは不可能な集計）

```sql
-- ① 全ユーザーblue/red率ランキング（RANK()ウィンドウ関数）
SELECT
    u.email,
    ROUND(SUM(a.red_count)::numeric / NULLIF(SUM(a.hand_count),0) * 100, 1) AS red_rate,
    RANK() OVER (ORDER BY SUM(a.red_count)::float / NULLIF(SUM(a.hand_count),0) DESC) AS red_rank
FROM users u
JOIN analyses a ON a.user_id = u.id
GROUP BY u.id, u.email;

-- ② 先週比赤線率悪化ユーザー（CTE + 期間比較）
WITH this_week AS (
    SELECT user_id, SUM(red_count)::float / NULLIF(SUM(hand_count),0) AS red_rate
    FROM analyses
    WHERE created_at >= DATE_TRUNC('week', NOW()) AND deleted_at IS NULL
    GROUP BY user_id
),
last_week AS (
    SELECT user_id, SUM(red_count)::float / NULLIF(SUM(hand_count),0) AS red_rate
    FROM analyses
    WHERE created_at >= DATE_TRUNC('week', NOW()) - INTERVAL '7 days'
      AND created_at <  DATE_TRUNC('week', NOW()) AND deleted_at IS NULL
    GROUP BY user_id
)
SELECT u.email,
       ROUND((tw.red_rate * 100)::numeric, 1) AS this_week_pct,
       ROUND((lw.red_rate * 100)::numeric, 1) AS last_week_pct,
       ROUND(((tw.red_rate - lw.red_rate) * 100)::numeric, 1) AS diff_pct
FROM this_week tw
JOIN last_week lw ON lw.user_id = tw.user_id
JOIN users u ON u.id = tw.user_id
WHERE tw.red_rate - lw.red_rate > 0.05
ORDER BY diff_pct DESC;
```

---

## コスト分析

### 月額概算（フル稼働時）

| サービス | 月額 | 備考 |
|---|---|---|
| ALB | ~$16 | 無料枠対象外 |
| ECS Fargate | ~$3 | 無料枠対象外 |
| RDS t4g.micro | ~$1 | |
| VPC / SM他 | ~$2 | |
| **合計** | **~$15〜20/月** | |

### クレジット残高（2026-04-30時点）

- 残高: $112.29 → 月$15〜20で約6〜8ヶ月（2026年10月〜12月頃まで）
- 予算アラート設定済み: $30超で 9p96d9@gmail.com / 69pdp69@gmail.com に通知

### コスト削減の選択肢

| 構成 | 月額目安 | 手間 |
|---|---|---|
| 現状（Fargate + ALB） | ~$15〜20 | 最小 |
| EC2 + Elastic IP（ALBなし） | ~$8〜10 | デプロイスクリプト変更が必要 |
| App Runner | ~$1〜5 | Puppeteer動作確認が必要 |

**今の結論:** クレジット残高が $30〜50 になったら再検討。

---

## Railway vs AWS

| 観点 | Railway | AWS |
|---|---|---|
| コスト | $5〜20/月 | $15〜20/月（クレジット消化中） |
| セットアップ | ほぼゼロ | VPC・IAM・ALB等の設定が必要 |
| デプロイ | git push → 即完了 | git push → 3〜5分 |
| PostgreSQL | 提供あり | RDS（より高機能） |
| 学習価値 | 低い | **高い（業界標準）** |

---

## トラブルシューティング記録

| エラー / 症状 | 原因 | 解決策 |
|---|---|---|
| ターゲットグループ選択エラー | instance型TGはFargateで使えない | IP型で `gto-tg` を新規作成 |
| ResourceNotFoundException | タスク定義のARNサフィックス誤字（`-cxxWn`） | CLIで正確なARNを取得して修正 |
| シークレット名形式エラー | `secretname:key::` はSSM形式と判定される | Secrets Manager使用時はフルARN必須 |
| AccessDeniedException（logs） | CloudWatchロググループが存在しない | `/ecs/gto-app` を手動作成 |
| 503エラー | ALBリスナーが古いTGを向いていた | デフォルトルールを `gto-tg` に変更 |
| GetSecretValue拒否 | IAMインラインのResourceが完全一致指定 | ARN末尾を `-*` のワイルドカードにする |
| サーバー起動しない（404） | Dockerfile CMD の `&&` でalembic失敗→server.pyが起動しない | `if [ -n "$DATABASE_URL" ]; then alembic ...; fi && python server.py` |
| PostgreSQLモードにならない | USE_POSTGRESがSecretsManagerで未設定 or ECSが旧タスク定義で動いている | SM確認後「新しいデプロイの強制」 |

---

## 本番環境値

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
| RDS SG | gto-rds-sg |
| AWSクレジット残高 | $112.29（2026-10月下旬まで） |
| Railway停止済み | 2026-05-15 |

---

## 今後の方針

### 短期

- [ ] `/admin/analytics` で「✓ PostgreSQLモード」を確認
- [ ] 拡張機能からハンドを送信 → PostgreSQL に保存されることを確認
- [ ] analysis_hands にデータが溜まったら 3D可視化リアルタイム化（Phase 20系）着手

### 中長期

- [ ] クレジット残高を月次で確認
- [ ] 有料サービスとして成立しているか評価（成立していれば費用は売上でカバー）
- [ ] 成立していなければ App Runner or EC2 に移行してコスト削減
