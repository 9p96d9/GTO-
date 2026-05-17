# 移行実行計画書（完了済み）
## WeasyPrint化 + EC2移行 + Cloudflare Tunnel + 独自ドメイン

> 作成: 2026-05-17  
> **完了: 2026-05-18**  
> すべてのフェーズが実施済み。本番は https://hrep.app で稼働中。

---

## 実施結果サマリー

| Phase | 内容 | 状態 | 実際の値 |
|---|---|---|---|
| Phase 1 | WeasyPrint移行（Node.js/Chromium排除） | ✅ 完了 | Day 1 (2026-05-17) |
| Phase 2 | 独自ドメイン取得 | ✅ 完了 | hrep.app / $14.20/年 / Cloudflare Registrar |
| Phase 3 | EC2セットアップ・IAMロール・RDS接続許可 | ✅ 完了 | i-06c53e45fc140cb9c / t3.micro / IP: 13.158.136.15 |
| Phase 4 | Cloudflare Tunnel設定 | ✅ 完了 | gto-tunnel / bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7 |
| Phase 5 | Firebase Auth 承認済みドメイン更新 | ✅ 完了 | hrep.app を追加 |
| Phase 6 | GitHub Actions deploy.yml 更新 | ✅ 完了 | ECS → EC2 SSH方式。run #40 成功（1m41s） |
| Phase 7 | 全機能テスト | ✅ 完了 | https://hrep.app 正常稼働確認 |
| Phase 8 | アプリ発表 | 🔄 準備中 | URL: https://hrep.app |
| Phase 9 | ALB / ECSクラスター削除 | ✅ 完了 | 2026-05-18 削除済み |

---

## 訂正事項（計画時の誤り）

### 訂正①: generate.js はGemini APIを呼んでいない

`pdf_docker_cost.md` で「generate.js がGemini APIを呼ぶため移行が複雑」と書いたが誤り。  
`fetchImprovement` 等はデッドコード。WeasyPrint移行スコープはHTML生成+Puppeteer呼び出しの置き換えのみだった。

### 訂正②: EC2無料枠は t2.micro だけでなく t3.micro も対象

当初「t2.microのみ無料枠」と記載していたが、t3.microも無料枠対象（12ヶ月、月750時間）。  
実際は t3.micro（2vCPU / 1GB RAM）で起動・稼働中。

### 訂正③: RDS接続経路の更新

`gto-rds-sg` の接続許可を `gto-ecs-sg` → `gto-ec2-sg` に変更済み。

---

## Phase 1: WeasyPrint移行 ✅ 完了

Node.js/Chromium(Puppeteer)を排除し、WeasyPrint（Python純正）に移行。

- `requirements.txt` に `weasyprint` を追加
- `Dockerfile` を修正（apt-get でWeasyPrint依存ライブラリのみ）
- `scripts/generate.py` / `scripts/generate_noapilist.py` を新規作成
- `pipelines.py` の subprocess 呼び出しを node → python に変更
- 絵文字はテキスト代替（`🔵→[青]` 等）

---

## Phase 2: 独自ドメイン取得 ✅ 完了

> **取得済み: hrep.app**
> - 取得日: 2026-05-17
> - 有効期限: 2027-05-17（自動更新設定済み）
> - レジストラ: Cloudflare Registrar
> - 年額: $14.20

---

## Phase 3: EC2 セットアップ ✅ 完了

### 実際の設定値

| 項目 | 値 |
|---|---|
| インスタンスID | i-06c53e45fc140cb9c |
| タイプ | t3.micro（ap-northeast-1） |
| AMI | Amazon Linux 2023 |
| パブリックIP | 13.158.136.15（動的） |
| キーペアファイル | C:\Users\dkb69\Desktop\gto-key.pem |
| IAMロール | gto-ec2-role |
| IAMプロファイル | gto-ec2-profile |
| セキュリティグループ | gto-ec2-sg（Port 22のみ） |

### 実施したことのメモ

- Dockerインストール: `sudo dnf install -y docker` → systemd enable
- IAMプロファイル作成: `aws iam create-instance-profile --instance-profile-name gto-ec2-profile`（コンソールからは不可。CloudShellで実行）
- gto-ec2-roleに `AmazonEC2ContainerRegistryReadOnly` を追加
- gto.env を Secrets Manager から生成（AWS CLI + Python パイプ方式）
- gto-rds-sg のインバウンドルールを gto-ec2-sg に更新

---

## Phase 4: Cloudflare Tunnel ✅ 完了

### 実際の設定値

| 項目 | 値 |
|---|---|
| トンネル名 | gto-tunnel |
| トンネルID | bf98b4b5-2f1d-45be-a9b6-1bb3890a3cf7 |
| 設定ファイル | /etc/cloudflared/config.yml |

### 実施したことのメモ

- cloudflaredバイナリを直接ダウンロード（yumリポジトリが404のため）:
  ```bash
  curl -fsSL 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64' \
    -o /tmp/cloudflared && sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared
  ```
- `cloudflared tunnel login` → ブラウザURL表示 → ローカルで認証
- `cloudflared tunnel create gto-tunnel`
- config.ymlを `/etc/cloudflared/` に作成
- `sudo cloudflared service install` でsystemd化
- `cloudflared tunnel route dns` で hrep.app / www.hrep.app のCNAME自動設定

---

## Phase 5: Firebase Auth 更新 ✅ 完了

Firebase Console → Authentication → Settings → 承認済みドメイン  
→ `hrep.app` を追加済み

---

## Phase 6: GitHub Actions 更新 ✅ 完了

`.github/workflows/deploy.yml` を ECS方式 → EC2 SSH方式に書き換え。  
`appleboy/ssh-action@v1` を使用。run #40 で成功確認（1m41s）。

**GitHub Secrets（設定済み）:**

| Secret名 | 状態 |
|---|---|
| `AWS_ACCESS_KEY_ID` | 設定済み |
| `AWS_SECRET_ACCESS_KEY` | 設定済み |
| `EC2_HOST` | 設定済み（13.158.136.15） |
| `EC2_SSH_KEY` | 設定済み（gto-key.pemの内容） |

---

## Phase 7: 全機能テスト ✅ 完了

- https://hrep.app → 200 OK・Firebase設定確認
- Googleログイン → 動作確認
- AI解析（max_tokens=4000 バグ修正済み） → 正常

---

## Phase 9: ALB / ECSクラスター削除 ✅ 完了

**削除日: 2026-05-18**

削除したリソース:
- ALB: gto-alb
- ターゲットグループ: gto-tg
- ECSサービス: gto-service
- ECSクラスター: gto-cluster

削除により月約 $16（ALB）+ $3（Fargate）= 約$19の削減。

ECRはイメージが残っており、削除していない（月~$0.10のストレージ代のみ）。  
ECS復旧時にECRのlatestイメージから即時復元可能。

---

## 移行後のコスト

| サービス | 月額 | 期限 |
|---|---|---|
| EC2 t3.micro | $0（無料枠） | 2027/04/24 |
| RDS db.t4g.micro | $0（無料枠） | 2027/04/24 |
| Cloudflare Tunnel | $0（永久無料） | — |
| ECR（~1.5GB） | ~$0.10 | — |
| Secrets Manager | ~$0.40 | — |
| hrep.app ドメイン | ~$1.18/月（$14.20/年） | 2027-05-17 |
| **合計** | **~$1.68/月** | |

移行前（ECS+ALB）の ~$22/月 から **約98%削減**。

---

## ロールバック手順（参考）

| 問題 | ロールバック手順 |
|---|---|
| EC2が落ちた | `docker restart gto-app` または EC2再起動 → Docker自動起動 |
| Cloudflare Tunnelが切れた | `sudo systemctl restart cloudflared` |
| 大規模障害 | ECRのイメージを使いECSを再作成（ops_runbook.mdの「旧構成」参照） |

---

*作成: 2026-05-17 / 完了: 2026-05-18*
