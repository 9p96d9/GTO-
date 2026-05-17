# AWS インフラ構成・運用ガイド
## HRep — EC2 + Cloudflare Tunnel 構成（2026-05-18〜）

> 旧ECS+ALB構成は末尾の「過去構成」セクションに記録。

---

## 全体構成図（現在）

```
ユーザー (HTTPS)
    │
    ▼
Cloudflare Edge（証明書・DDoS対策・キャッシュ）
    │ Cloudflare Tunnel（アウトバウンドのみ・インバウンド不要）
    ▼
cloudflared デーモン（EC2上 / systemd管理）
    │ http://localhost:5000
    ▼
Docker コンテナ gto-app（EC2上）
    ├── ECR  273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
    ├── Secrets Manager  gto/production（gto.env に展開済み）
    └── RDS PostgreSQL（VPC内 / gto-rds-sg）

デプロイフロー:
  git push origin master:main
    → GitHub Actions (.github/workflows/deploy.yml)
    → Docker build → ECR push
    → SSH → EC2 → docker pull → docker stop/rm → docker run
    → 所要時間: 約2分
```

---

## AWSサービス一覧（現在）

| サービス | 役割 | 設定値 |
|---|---|---|
| EC2 t3.micro | アプリ本体実行 | i-06c53e45fc140cb9c / ap-northeast-1 / Amazon Linux 2023 |
| ECR | Dockerイメージ倉庫 | リポジトリ名: gto-app |
| RDS db.t4g.micro | PostgreSQL管理 | gto-db / 無料枠〜2027/04/24 |
| Secrets Manager | 環境変数の金庫 | シークレット名: gto/production |
| IAM Role | EC2がECR/SMにアクセスする許可証 | gto-ec2-role / gto-ec2-profile |
| SG gto-ec2-sg | EC2用ファイアウォール | Port 22のみインバウンド許可 |
| SG gto-rds-sg | RDS用ファイアウォール | gto-ec2-sgからの5432のみ許可 |
| Cloudflare Tunnel | HTTPS終端・トンネル | gto-tunnel / 永久無料 |

---

## EC2 インスタンス詳細

| 項目 | 値 |
|---|---|
| インスタンスID | i-06c53e45fc140cb9c |
| タイプ | t3.micro（2vCPU・1GB RAM） |
| AMI | Amazon Linux 2023 |
| パブリックIP | 13.158.136.15（**動的 — 停止→起動でIPが変わる**） |
| キーペアファイル | C:\Users\dkb69\Desktop\gto-key.pem |
| IAMプロファイル | gto-ec2-profile（gto-ec2-roleを含む） |
| セキュリティグループ | gto-ec2-sg（Port 22のみ） |
| 無料枠期限 | 2027/04/24 |

> **IPが変わった場合の必須作業:**  
> GitHub → Settings → Secrets → `EC2_HOST` を新IPに更新

---

## Cloudflare Tunnel 詳細

| 項目 | 値 |
|---|---|
| トンネル名 | gto-tunnel |
| トンネルID | bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7 |
| 設定ファイル | /etc/cloudflared/config.yml |
| 認証情報 | /etc/cloudflared/bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7.json |
| systemd | cloudflared.service（enabled / auto-start） |
| ドメイン | hrep.app / www.hrep.app |

### config.yml

```yaml
tunnel: bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7
credentials-file: /etc/cloudflared/bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7.json
ingress:
  - hostname: hrep.app
    service: http://localhost:5000
  - hostname: www.hrep.app
    service: http://localhost:5000
  - service: http_status:404
```

---

## Secrets Manager

**シークレット名:** `gto/production`  
**ARN:** `arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn`

| キー名 | 内容 |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase SA JSON（1行に圧縮） |
| `FIREBASE_API_KEY` | Firebase Web APIキー |
| `FIREBASE_AUTH_DOMAIN` | Firebaseドメイン |
| `FIREBASE_PROJECT_ID` | FirebaseプロジェクトID |
| `ADMIN_UID` | 管理者Firebase UID |
| `GEMINI_API_KEY` | Gemini APIキー |
| `GROQ_API_KEY` | Groq APIキー |
| `PORT` | `5000` |
| `DATABASE_URL` | PostgreSQL接続文字列 |
| `USE_POSTGRES` | `true` |

---

## IAM 構成

**ロール名:** `gto-ec2-role`  
**インスタンスプロファイル:** `gto-ec2-profile`

アタッチポリシー:
- `AmazonEC2ContainerRegistryReadOnly` — ECRからのdocker pull
- `SecretsManagerReadWrite` — Secrets Manager読み書き
- `gto-secrets-access`（インライン）— 特定シークレットの GetSecretValue

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

---

## GitHub Actions（deploy.yml）

**トリガー:** mainブランチへのpush

```
1. Docker build（ubuntu-latest）
2. ECR push（:latest タグ）
3. appleboy/ssh-action で EC2にSSH
4. docker pull → stop → rm → run
```

**必要なGitHub Secrets:**

| Secret名 | 用途 |
|---|---|
| `AWS_ACCESS_KEY_ID` | ECRへのdocker push |
| `AWS_SECRET_ACCESS_KEY` | 同上 |
| `EC2_HOST` | SSHの接続先IP（**動的。IP変更時は更新**） |
| `EC2_SSH_KEY` | gto-key.pem の中身 |

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

---

## RDS 詳細

| 項目 | 値 |
|---|---|
| インスタンス名 | gto-db |
| タイプ | db.t4g.micro |
| エンジン | PostgreSQL 18 |
| エンドポイント | gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432 |
| セキュリティグループ | gto-rds-sg（gto-ec2-sgからのPort 5432のみ許可） |
| 無料枠期限 | 2027/04/24 |

---

## コスト（2026-05-18 現在）

| サービス | 月額 | 期限 |
|---|---|---|
| EC2 t3.micro | $0（無料枠） | 2027/04/24 |
| RDS db.t4g.micro | $0（無料枠） | 2027/04/24 |
| Cloudflare Tunnel | $0（永久無料） | — |
| ECR（~1.5GB） | ~$0.10 | — |
| Secrets Manager | ~$0.40 | — |
| hrep.app ドメイン | ~$1.18/月（$14.20/年） | 2027-05-17 |
| **合計** | **~$1.68/月** | |

> **無料枠終了後（2027/04〜）:** EC2 ~$8/月 + RDS ~$13/月 = ~$21/月  
> → 2027/04 前に移行先を検討すること（Firebase回帰 or Fly.io等）

---

## トラブルシューティング記録

| エラー / 症状 | 原因 | 解決策 |
|---|---|---|
| IAM認証エラー | インスタンスプロファイル未作成 | `aws iam create-instance-profile` → `add-role-to-instance-profile` → EC2にアタッチ |
| ECR AccessDenied | gto-ec2-roleにECRポリシー未付与 | `AmazonEC2ContainerRegistryReadOnly` をアタッチ |
| SM ResourceNotFoundException | シークレット名に ARN サフィックスを含めていた | `aws secretsmanager list-secrets` で正確な名前を確認（`gto/production`） |
| cloudflared RPM 404 | yumリポジトリが機能しない | バイナリを直接ダウンロード: `curl -fsSL ...cloudflared-linux-amd64 -o /tmp/cloudflared` |
| git push rejected | ローカルmaster ≠ リモートmain | `git push origin master:main` |
| boto3 not found | Amazon Linux 2023 は boto3 標準なし | AWS CLI + Python パイプ方式を使う |

---

## 本番環境値（クイックリファレンス）

| 項目 | 値 |
|---|---|
| AWS アカウントID | 273949555510 |
| リージョン | ap-northeast-1（東京） |
| EC2 インスタンスID | i-06c53e45fc140cb9c |
| ECRリポジトリ | gto-app |
| Secrets Manager | gto/production（ARN末尾: -cxsxWn） |
| IAMロール | gto-ec2-role |
| RDS インスタンス | gto-db（db.t4g.micro・PostgreSQL 18） |
| RDS エンドポイント | gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432 |
| Cloudflare Tunnel ID | bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7 |
| 本番URL | https://hrep.app |

---

---

# 過去構成（ECS + ALB 時代）の記録

> 削除済み: 2026-05-18  
> 資金確保後にスケールアップする場合の参考として残す。

## 旧構成図（ECS + ALB）

```
インターネット
    │ HTTP:80
    ▼
ALB（gto-alb）                ← 固定入口。FargateはIPが毎回変わるため必要だった
    │ HTTP:5000
    ▼
ECS Fargate（gto-app）        ← コンテナを常時1台管理・再起動自動化
    ├── ECR                   ← Dockerイメージ保管庫（現在も継続利用）
    ├── Secrets Manager       ← 環境変数の金庫（現在も継続利用）
    └── RDS PostgreSQL        ← （現在も継続利用）

セキュリティグループ:
  gto-alb-sg … ALB用。80/443をインターネットから許可
  gto-ecs-sg … ECS用。gto-alb-sgからの5000のみ許可
  gto-rds-sg … RDS用。gto-ecs-sgからの5432のみ許可（現在はgto-ec2-sgに変更）

デプロイ:
  git push → GitHub Actions → Docker build → ECR push → ECSローリングアップデート
```

## 削除済みリソース

| リソース | ARN / ID | 削除日 |
|---|---|---|
| ALB | arn:aws:elasticloadbalancing:ap-northeast-1:273949555510:loadbalancer/app/gto-alb/9f457e4d85ee81c4 | 2026-05-18 |
| ターゲットグループ | arn:aws:elasticloadbalancing:ap-northeast-1:273949555510:targetgroup/gto-tg/7bf560fd99221680 | 2026-05-18 |
| ECSクラスター | gto-cluster | 2026-05-18 |
| ECSサービス | gto-service | 2026-05-18 |
| 最終タスク定義 | gto-task:35 / 0.5vCPU・1024MB / Fargate | 2026-05-18 |

## 旧コスト

| サービス | 月額 |
|---|---|
| ALB | ~$16 |
| ECS Fargate | ~$3 |
| RDS | ~$1 |
| Secrets Manager他 | ~$2 |
| **合計** | **~$22/月** |

ALBは「Fargateのコンテナが再起動するたびIPが変わる」問題を解決するためだけに存在していた。  
Cloudflare Tunnelはアウトバウンドのみで動作するため、ALBが不要になった。

## 旧タスク定義でのSecrets Manager参照形式

```json
"valueFrom": "arn:aws:secretsmanager:ap-northeast-1:273949555510:secret:gto/production-cxsxWn:キー名::"
```

> タスク定義でのSM参照はフルARN必須（コンソール表示のサフィックスなし版では動かない）。

## ECS復旧手順（概要）

ECRにイメージは残っているため即時復旧可能:

```
1. ECSクラスター再作成（gto-cluster）
2. タスク定義を ECR の latest イメージで作成
3. ALB → ターゲットグループ(IP型) → ECSサービス の順で再作成
4. Firebase 承認済みドメインに ALBのDNS名を追加
```

---

*最終更新: 2026-05-18*
