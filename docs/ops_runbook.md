# 運用ランブック — HRep (EC2 + Cloudflare Tunnel)

> 作成: 2026-05-18  
> 本番URL: https://hrep.app  
> 旧URL（停止済み）: http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com/

---

## インフラ構成（現在）

```
ユーザー (HTTPS)
    │
    ▼
Cloudflare Edge（証明書・DDoS対策は自動）
    │ Tunnel（アウトバウンドのみ）
    ▼
cloudflared デーモン（EC2上 systemd管理）
    │ http://localhost:5000
    ▼
Docker コンテナ gto-app（EC2上）
    │
    ├── ECR  273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
    ├── Secrets Manager  gto/production（gto.envに展開済み）
    └── RDS PostgreSQL（VPC内 / gto-rds-sg）

デプロイフロー:
  git push origin master:main
    → GitHub Actions（.github/workflows/deploy.yml）
    → Docker build → ECR push
    → SSH → EC2 → docker pull → docker restart
```

---

## EC2 インスタンス情報

| 項目 | 値 |
|---|---|
| インスタンスID | i-06c53e45fc140cb9c |
| タイプ | t3.micro（ap-northeast-1 / 2vCPU・1GB RAM） |
| AMI | Amazon Linux 2023 |
| **現在のパブリックIP** | **13.158.136.15** |
| キーペアファイル | C:\Users\dkb69\Desktop\gto-key.pem |
| IAMロール | gto-ec2-role（ECR読取・SM読取） |
| IAMプロファイル | gto-ec2-profile |
| セキュリティグループ | gto-ec2-sg（Port 22のみインバウンド許可） |

> ⚠️ **重要: パブリックIPは動的**  
> EC2を停止→起動するとIPが変わる。変わった場合は以下を更新すること：  
> - GitHub Secret `EC2_HOST` の値  
> - 手元のSSHコマンドのIP

---

## SSH接続

```powershell
# Windows PowerShell から
ssh -i "C:\Users\dkb69\Desktop\gto-key.pem" -o StrictHostKeyChecking=no ec2-user@13.158.136.15
```

---

## よく使うコマンド（EC2上）

### アプリ状態確認

```bash
docker ps                          # コンテナ稼働確認
docker logs gto-app --tail 50      # 直近ログ
docker logs gto-app -f             # ログをリアルタイム追跡
curl -s http://localhost:5000/     # ヘルスチェック（200なら正常）
```

### アプリ再起動

```bash
docker restart gto-app
```

### Cloudflare Tunnel 確認・再起動

```bash
sudo systemctl status cloudflared   # 状態確認
sudo systemctl restart cloudflared  # 再起動
journalctl -u cloudflared -n 50     # ログ確認
```

### 手動デプロイ（GitHub Actions を使わない場合）

```bash
# ECRログイン（IAMロールが有効なら --profile 不要）
aws ecr get-login-password --region ap-northeast-1 | \
  docker login --username AWS --password-stdin 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com

# 最新イメージ取得
docker pull 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest

# コンテナ再作成
docker stop gto-app && docker rm gto-app
docker run -d --name gto-app --restart unless-stopped \
  -p 5000:5000 \
  --env-file /home/ec2-user/gto.env \
  273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
```

### 環境変数の更新（Secrets Manager から再生成）

```bash
# EC2上で実行（IAMロールがSM読取権限を持つため認証不要）
aws secretsmanager get-secret-value \
  --secret-id 'gto/production' \
  --region ap-northeast-1 \
  --query SecretString --output text | python3 -c "
import sys, json
data = json.load(sys.stdin)
lines = []
for k, v in data.items():
    v_clean = str(v).replace('\n', '\\n')
    lines.append(f'{k}={v_clean}')
with open('/home/ec2-user/gto.env', 'w') as f:
    f.write('\n'.join(lines) + '\n')
print('Keys written:', list(data.keys()))
"

# コンテナ再起動して反映
docker restart gto-app
```

---

## EC2 を止めるときの手順

```bash
# 1. Cloudflare TunnelとDockerは自動起動設定済みなので普通に再起動してOK
sudo reboot

# 2. 完全停止（AWSコンソールまたはCLI）する場合
#    → 起動後にIPが変わるため GitHub Secret を更新すること

# IPが変わった後の作業
# AWSコンソール → EC2 → 新しいパブリックIPを確認
# GitHub → Settings → Secrets → EC2_HOST を新IPに更新
```

---

## Cloudflare Tunnel 情報

| 項目 | 値 |
|---|---|
| トンネル名 | gto-tunnel |
| トンネルID | bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7 |
| 設定ファイル | /etc/cloudflared/config.yml |
| 認証情報 | /etc/cloudflared/bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7.json |
| systemd | cloudflared.service（enabled / auto-start） |

### config.yml 内容

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

## GitHub Actions（deploy.yml）

```
トリガー: main ブランチへの push
手順:
  1. Docker build（ubuntu-latest上）
  2. ECR push（:latest タグ）
  3. SSH → EC2（secrets.EC2_HOST / EC2_SSH_KEY）
  4. docker pull → stop → rm → run

所要時間: 約2分
```

### 必要なGitHub Secrets

| Secret名 | 用途 |
|---|---|
| `AWS_ACCESS_KEY_ID` | ECRへのdocker push |
| `AWS_SECRET_ACCESS_KEY` | 同上 |
| `EC2_HOST` | SSHの接続先IP（動的。IP変更時は更新） |
| `EC2_SSH_KEY` | gto-key.pem の中身 |

---

## 旧構成（ECS+ALB時代）の記録

> 資金確保後にスケールアップする場合の参考として残す。

```
ALB（削除済み 2026-05-18）
  ARN: arn:aws:elasticloadbalancing:ap-northeast-1:273949555510:loadbalancer/app/gto-alb/9f457e4d85ee81c4
  名前: gto-alb

ターゲットグループ（削除済み 2026-05-18）
  ARN: arn:aws:elasticloadbalancing:ap-northeast-1:273949555510:targetgroup/gto-tg/7bf560fd99221680

ECSクラスター（削除済み 2026-05-18）
  クラスター: gto-cluster
  サービス: gto-service
  最終タスク定義: gto-task:35
  スペック: 0.5vCPU / 1024MB / Fargate

IAMロール（既存・EC2でも流用中）
  gto-ecs-task-execution-role → gto-ec2-roleとして継続利用

復旧手順（概要）:
  1. ECSクラスター再作成
  2. タスク定義を ECR の latest イメージで作成
  3. ALB → ターゲットグループ → ECSサービス の順で再作成
  4. Firebase 承認済みドメインに ALBのDNS名を追加
  ※ ECRにイメージは残っているため即時復旧可能
```

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

> 無料枠終了後（2027/04〜）: EC2 ~$8/月 + RDS ~$13/月 = ~$21/月  
> → 2027/04 前に Firebase 回帰 or 別プラットフォーム移行を検討すること

---

*最終更新: 2026-05-18*
