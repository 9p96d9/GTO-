# 移行実行計画書
## WeasyPrint化 + ALB廃止 + EC2移行 + 独自ドメイン
### 目標: 5月中にアプリ発表・6月以降はほぼノータッチで運用

> 作成: 2026-05-17  
> 前提: 現在 ALB + ECS Fargate で稼働中。Railway 停止済み。

---

## ⚠️ 前回分析の訂正事項

### 訂正①: generate.js はGemini APIを呼んでいない

`pdf_docker_cost.md` で「generate.js がGemini APIを呼ぶため移行が複雑」と書いたが誤り。

```js
// generate.js の main() 実際の処理
const html = buildFullHtml([
    buildTitleHtml(...),
    buildSection2Html(hands),  // 3BETテーブル
    buildSection3Html(hands),  // 全ハンドテーブル
]);
await generatePdf(html, outFile);  // Puppeteer でPDF化
```

`fetchImprovement`, `fetchStrength` 等のGemini関数は定義されているが **main() から呼ばれていない**（デッドコード）。  
→ **WeasyPrint移行スコープ: HTML生成 + Puppeteer呼び出し の置き換えのみ。**  
→ 難易度: 低〜中（大幅に楽になる）

---

### 訂正②: EC2無料枠は t2.micro のみ（東京リージョン）

東京リージョン(ap-northeast-1)には t2.micro が存在するため、  
無料枠は `t2.micro`（1vCPU, 1GB RAM）が対象。t3.micro は無料枠外（$0.014/時間）。

→ WeasyPrint化でChromium(~300MB)を排除すれば、**t2.micro 1GBで安全に動く**。

---

### 訂正③: RDS への接続経路の更新が必要

現在: `gto-rds-sg` は `gto-ecs-sg` からのポート5432のみ許可  
変更後: EC2の新SGからの接続を `gto-rds-sg` に追加する必要あり

---

### 訂正④: カットオーバー戦略（ダウンタイムゼロ）

ALBのDNSを切り替えるのではなく、**新ドメインで完全に別URLとして立ち上げ、発表時から新URLを使う**。

```
旧URL: gto-alb-xxx.ap-northeast-1.elb.amazonaws.com  ← 告知しない
新URL: yourdomain.app                                 ← 発表から使用
```

ALBは発表後1週間は並行稼働のまま残し、問題がなければ停止。  
**ダウンタイム: 実質ゼロ**。

---

## 全体スケジュール（5日間）

```
Day 1 (今日): WeasyPrint移行・コード作業・本番デプロイ・PDF動作確認
Day 2      : ドメイン取得・EC2セットアップ・RDS疎通確認
Day 3      : Cloudflare Tunnel・Firebase更新・GitHub Actions変更・全機能テスト
Day 4      : 最終確認・アプリ発表 🎉
Day 5〜7   : 安定確認後にALB削除（月$7の節約確定）
```

---

## Phase 0: 精査チェックリスト（30分・今すぐ）

Secrets Manager の値を手元にメモしておく（EC2のenv設定で使う）。

```bash
aws secretsmanager get-secret-value \
  --secret-id gto/production \
  --query SecretString \
  --output text
```

→ 出力をテキストエディタに保存（後で使う）。  
**注意: この値は外部に出さない・GitHubにpushしない。**

---

## Phase 1: WeasyPrint移行（Day 1 / 3〜4時間）

### 1-1. requirements.txt に weasyprint を追加

```
weasyprint
```

### 1-2. Dockerfile を修正

```dockerfile
FROM python:3.11-slim

# WeasyPrint用ライブラリ（Chromiumの14個 → 6個に削減）
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    fonts-ipafont-gothic \
    fonts-ipafont-mincho \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python依存インストール
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Node.js インストール行 → 削除
# COPY package.json package-lock.json → 削除
# RUN npm ci → 削除

# プロジェクトファイルをコピー
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY extension/ ./extension/
COPY templates/ ./templates/
COPY html_pages/ ./html_pages/
COPY routes/ ./routes/
COPY state.py pipelines.py server.py ./
COPY alembic/ ./alembic/
COPY alembic.ini ./

RUN mkdir -p input/done output data

ENV PYTHONIOENCODING=utf-8

CMD ["sh", "-c", "if [ -n \"$DATABASE_URL\" ]; then python -m alembic upgrade head; fi && python server.py"]
```

### 1-3. scripts/generate_noapilist.py を新規作成（generate_noapilist.js の Python版）

generate_noapilist.js の処理を Python + WeasyPrint で再実装する。  
→ **この作業は Claude が直接コードを書く。**

### 1-4. scripts/generate.py を新規作成（generate.js の Python版）

generate.js の処理を Python + WeasyPrint で再実装する。  
→ **この作業は Claude が直接コードを書く。**

### 1-5. pipelines.py を修正

```python
# 変更前
subprocess.run(["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), str(json_path)], ...)
# 変更後
subprocess.run([sys.executable, str(SCRIPTS / "generate.py"), str(OUTPUT_DIR), str(json_path)], ...)

# 変更前
subprocess.run(["node", str(SCRIPTS / "generate_noapilist.js"), str(OUTPUT_DIR), str(classified_path)], ...)
# 変更後
subprocess.run([sys.executable, str(SCRIPTS / "generate_noapilist.py"), str(OUTPUT_DIR), str(classified_path)], ...)
```

### 1-6. 絵文字について

WeasyPrint は `fonts-noto-color-emoji` があればカラー絵文字をレンダリングできる。  
ただしフォント追加で+50MB（Docker容量増）。  
**今回の判断: 絵文字をテキストに置換してシンプルに対応。**

```
🔵 → [青]  🔴 → [赤]  ✅ → ○  ❌ → ×  ⚠️ → △  🎲 → C
```

### 1-7. ローカルテスト（任意・Windowsでは省略可）

Dockerfileを使ってローカルビルド→テストするのが理想だが、  
WSL2環境がなければスキップして本番でテストしてよい。

### 1-8. デプロイ・PDF動作確認

```bash
git add scripts/generate.py scripts/generate_noapilist.py pipelines.py Dockerfile requirements.txt
git commit -m "feat: WeasyPrint移行・Node.js/Chromium排除"
git push origin master:main
```

→ GitHub Actions が自動で ECR push + ECS deploy（既存フローのまま）  
→ 5分後に `/sessions` からPDF生成を試して動作確認  
→ PDFが正常生成されれば Phase 1 完了

---

## Phase 2: 独自ドメイン取得（Day 2 / 30分）

### 2-1. Cloudflare アカウント作成

https://dash.cloudflare.com/sign-up

### 2-2. Cloudflare Registrar でドメイン購入

```
Cloudflare ダッシュボード → Domain Registration → Register Domains
→ 希望ドメインを検索
→ 購入（クレジットカード必要）
```

**推奨TLD:**
- `.app`: ¥1,570/年。HTTPS強制（HSTS preload）。アプリらしさが出る。
- `.com`: ¥1,514/年。汎用・信頼感。

**ドメイン名の考え方:**
- 短い・覚えやすい・ポーカー/GTOが伝わる
- 例: `[サービス名].app` または `[サービス名].com`
- 事前に3〜5候補を考えておく（売り切れあり）

### 2-3. DNSゾーンは Cloudflare が自動管理

ドメイン購入後、CloudflareのDNSゾーンが自動で作成される。  
**この時点ではDNSレコードは何も設定しない。**（Tunnel設定時に設定する）

---

## Phase 3: EC2 セットアップ（Day 2 / 2〜3時間）

### 3-1. EC2 インスタンス起動

AWSコンソール → EC2 → インスタンスを起動

| 設定項目 | 値 |
|---|---|
| 名前 | `gto-ec2` |
| AMI | Amazon Linux 2023（最新） |
| インスタンスタイプ | **t2.micro**（無料枠対象） |
| キーペア | 新規作成 `gto-key` → `.pem` ファイルをダウンロード・保存 |
| ネットワーク | デフォルトVPC（既存のRDSと同じVPC） |
| パブリックIPの自動割り当て | 有効 |
| セキュリティグループ | 新規作成（次項参照） |
| ストレージ | 30GB gp3（無料枠上限） |

### 3-2. EC2用セキュリティグループ（gto-ec2-sg）

| ルール | ポート | ソース | 用途 |
|---|---|---|---|
| インバウンド | 22 (SSH) | 自分のIP/32 | SSH管理（GitHub Actions用も含む） |
| インバウンド | 22 (SSH) | 0.0.0.0/0 | GitHub Actionsから（後で制限可） |
| アウトバウンド | すべて | 0.0.0.0/0 | Cloudflare Tunnel通信・ECR pull等 |

> Cloudflare Tunnelはアウトバウンドのみで動作するため、  
> HTTP(80)/HTTPS(443)のインバウンドは不要。

### 3-3. RDS セキュリティグループを更新

AWSコンソール → EC2 → セキュリティグループ → `gto-rds-sg`

インバウンドルールを追加:
```
タイプ: PostgreSQL
ポート: 5432
ソース: gto-ec2-sg のSG-ID（IPではなくSG-IDを指定）
```

### 3-4. EC2 に IAM ロールをアタッチ

AWSコンソール → EC2 → インスタンス → `gto-ec2` → アクション → セキュリティ → IAMロールを変更

既存の `gto-ecs-task-execution-role` をアタッチする。  
（ECR読み取り + Secrets Manager読み取り の権限が含まれている）

### 3-5. EC2 に SSH 接続・初期セットアップ

```bash
# ダウンロードした .pem ファイルを使って接続
ssh -i "gto-key.pem" ec2-user@<EC2のパブリックIPアドレス>

# Docker インストール
sudo dnf update -y
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# AWS CLI は Amazon Linux 2023 に標準インストール済み
# 確認
aws --version
```

**一度ログアウトして再ログイン（dockerグループ反映のため）**

### 3-6. ECR からイメージを取得できるか確認

```bash
# ECR ログイン（IAMロールがアタッチされていれば --profile 不要）
aws ecr get-login-password --region ap-northeast-1 | \
  docker login --username AWS --password-stdin 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com

# 最新イメージを pull
docker pull 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
```

→ `Status: Image is up to date` または `Pull complete` が出ればOK

### 3-7. 環境変数ファイルを作成

```bash
# EC2上で作成
cat > /home/ec2-user/gto.env << 'EOF'
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
FIREBASE_API_KEY=xxxx
FIREBASE_AUTH_DOMAIN=xxxx.firebaseapp.com
FIREBASE_PROJECT_ID=xxxx
ADMIN_UID=xxxx
GEMINI_API_KEY=xxxx
GROQ_API_KEY=xxxx
DATABASE_URL=postgresql://user:pass@gto-db.xxxx.ap-northeast-1.rds.amazonaws.com:5432/dbname
USE_POSTGRES=true
PORT=5000
EOF

# パーミッション制限（本人のみ読める）
chmod 600 /home/ec2-user/gto.env
```

**値は Phase 0 でメモしたものを使う。**  
`FIREBASE_SERVICE_ACCOUNT_JSON` は1行に圧縮されたJSON文字列を貼る。

### 3-8. コンテナ起動テスト

```bash
docker run -d --name gto-app --restart unless-stopped \
  -p 5000:5000 \
  --env-file /home/ec2-user/gto.env \
  273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest

# ヘルスチェック
curl http://localhost:5000/health
# → {"status":"ok"} が返ればOK
```

### 3-9. RDS 疎通確認

```bash
# コンテナ内に入ってDB接続確認
docker exec -it gto-app bash
python -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    result = conn.execute(text('SELECT 1'))
    print('DB OK:', result.fetchone())
"
```

→ `DB OK: (1,)` が出れば接続成功

---

## Phase 4: Cloudflare Tunnel 設定（Day 3 / 1〜2時間）

### 4-1. cloudflared インストール（EC2上）

```bash
sudo rpm --import https://pkg.cloudflare.com/cloudflare-main.gpg
sudo curl -L https://pkg.cloudflare.com/cloudflared/rpm/cloudflare-main.repo \
  -o /etc/yum.repos.d/cloudflared.repo
sudo dnf install cloudflared -y
```

### 4-2. Cloudflare にログイン・トンネル作成

```bash
# ブラウザが開く（EC2上ではURLが表示される → ローカルブラウザでアクセス）
cloudflared tunnel login

# トンネル作成
cloudflared tunnel create gto-tunnel

# 出力例:
# Tunnel credentials written to /home/ec2-user/.cloudflared/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.json
# Created tunnel gto-tunnel with id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

→ **トンネルID** をメモする（英数字のUUID形式）

### 4-3. 設定ファイル作成

```bash
mkdir -p /home/ec2-user/.cloudflared

cat > /home/ec2-user/.cloudflared/config.yml << EOF
tunnel: <トンネルID>
credentials-file: /home/ec2-user/.cloudflared/<トンネルID>.json

ingress:
  - hostname: yourdomain.app
    service: http://localhost:5000
  - service: http_status:404
EOF
```

`yourdomain.app` の部分は取得した実際のドメインに変更する。

### 4-4. DNS レコードを設定

```bash
cloudflared tunnel route dns gto-tunnel yourdomain.app
```

→ CloudflareのDNSに CNAMEレコードが自動追加される。  
→ Cloudflare ダッシュボードの DNS タブで確認できる。

### 4-5. トンネルをサービス化（自動起動）

```bash
sudo cloudflared service install

# サービス設定ファイルを cloudflared のデフォルトパスにコピー
sudo mkdir -p /etc/cloudflared
sudo cp /home/ec2-user/.cloudflared/config.yml /etc/cloudflared/config.yml
sudo cp /home/ec2-user/.cloudflared/<トンネルID>.json /etc/cloudflared/<トンネルID>.json

sudo systemctl start cloudflared
sudo systemctl enable cloudflared
sudo systemctl status cloudflared  # active (running) であることを確認
```

### 4-6. HTTPS 動作確認

ブラウザで `https://yourdomain.app/health` にアクセス  
→ `{"status":"ok"}` が返ればトンネル成功

---

## Phase 5: Firebase Auth 更新（Day 3 / 15分）

Firebaseコンソール → Authentication → Settings → 承認済みドメイン

**追加するドメイン:**
```
yourdomain.app
```

→ 追加後、`https://yourdomain.app` でGoogleログインが動作するか確認。

---

## Phase 6: GitHub Actions 更新（Day 3 / 1時間）

### 6-1. GitHub Secrets に EC2 情報を追加

GitHubリポジトリ → Settings → Secrets and variables → Actions

| Secret名 | 値 |
|---|---|
| `EC2_HOST` | EC2のパブリックIPアドレス |
| `EC2_SSH_KEY` | gto-key.pem の中身（`-----BEGIN RSA PRIVATE KEY-----` から全文） |

### 6-2. deploy.yml を更新

```yaml
name: Deploy to AWS ECS → EC2

on:
  push:
    branches: [main]

concurrency:
  group: production
  cancel-in-progress: true

env:
  AWS_REGION: ap-northeast-1
  ECR_REPOSITORY: gto-app

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image to ECR
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
          docker tag $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG $ECR_REGISTRY/$ECR_REPOSITORY:latest
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest

      - name: Deploy to EC2 via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ec2-user
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            aws ecr get-login-password --region ap-northeast-1 | \
              docker login --username AWS --password-stdin 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com
            docker pull 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
            docker stop gto-app || true
            docker rm gto-app || true
            docker run -d --name gto-app --restart unless-stopped \
              -p 5000:5000 \
              --env-file /home/ec2-user/gto.env \
              273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
            docker image prune -f
```

### 6-3. テストデプロイ

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: deploy target を ECS → EC2 SSH に変更"
git push origin master:main
```

→ GitHub Actions のログを確認  
→ 成功したら `https://yourdomain.app` で動作確認

---

## Phase 7: 全機能テスト（Day 3〜4 / 2時間）

以下をすべて実際に操作して確認する。

```
□ https://yourdomain.app → ランディングページ表示
□ Googleログイン → ログイン成功・リダイレクト正常
□ /sessions → セッション一覧表示
□ 拡張機能からハンド送信 → 解析パイプライン動作
□ PDF生成 → ダウンロード・内容確認（WeasyPrint品質）
□ AI解析 → Groq/Gemini応答
□ /admin/analytics → 管理者ページ（自分のUIDでのみ）
□ /health → {"status":"ok"}
```

---

## Phase 8: アプリ発表（Day 4）

`https://yourdomain.app` を新しい公式URLとして発表。  
**ALBのURLは告知しない**（旧URLは引き続き動いているが新規ユーザーは新URLのみ知る）。

発表後の告知に入れる情報:
- URL: `https://yourdomain.app`
- Chrome拡張の配布方法（既存の手順）

---

## Phase 9: ALB 停止（Day 5〜7 / 30分）

発表後24〜48時間問題がなければ実行。

### 9-1. ECS サービスのタスク数を 0 に

AWSコンソール → ECS → gto-cluster → gto-service → 編集  
→ 必要なタスク数: `1` → `0`

### 9-2. ALB を削除

AWSコンソール → EC2 → ロードバランサー → `gto-alb` → 削除  
（ターゲットグループ `gto-tg` も削除）

**これで月 ~$7 の削減確定。**

### 9-3. Firebase の旧ドメイン削除（任意）

Firebase Console → Authentication → 承認済みドメイン  
→ `gto-alb-xxx.ap-northeast-1.elb.amazonaws.com` を削除

### 9-4. ECS クラスター（後日）

ECR・ECSクラスターは残しておいても月額ほぼ$0なので削除しなくてよい。  
（ECSクラスター自体は無料。ECR はイメージ分のストレージ代のみ）

---

## 移行後のコスト

```
EC2 t2.micro:     $0（無料枠 〜 2027/04/24）
RDS db.t4g.micro: $0（無料枠）
Secrets Manager:  ~$0.40/月
ECR（1GB）:       ~$0.10/月
Cloudflare Tunnel:  $0（無料）
独自ドメイン:     ¥1,500〜/年（月換算 ¥125〜）
─────────────────────────────
合計（無料枠中）: ~¥185/月 ← 現状の $14.7/月から98%削減
```

**無料枠終了後（2027/04/25〜）:**
- EC2 t2.micro: ~$8/月
- 合計: ~¥1,400/月

---

## ロールバック手順

何かおかしくなったら、以下で即時復旧できる。

| 問題 | ロールバック手順 |
|---|---|
| WeasyPrint PDF が壊れた | Dockerfileを戻してpush → ECSが旧バージョンのまま(ECSはまだ稼働中) |
| EC2が落ちた | `docker restart gto-app` または EC2再起動後 Dockerが自動起動 |
| Cloudflare Tunnelが切れた | `sudo systemctl restart cloudflared` |
| 全部やめたい | Firebase の承認済みドメインから新ドメインを削除 → 旧ALB URLで引き続き稼働 |

**ECSとALBは Phase 9 まで削除しない。** いつでも旧構成に戻せる。

---

## ドキュメント更新チェックリスト

移行完了後に更新するファイル:

```
□ docs/aws.md         → EC2構成・Cloudflare Tunnel 構成に書き換え
□ docs/infra_cost_strategy.md → 月コスト$0.5/月に更新
□ CLAUDE.md           → 本番URL を yourdomain.app に更新
□ SPEC.md             → ホスティング情報更新
```

---

*作成: 2026-05-17*  
*次のアクション: Phase 1（WeasyPrint移行）→ 「generate_noapilist.py を書いて」と指示する*
