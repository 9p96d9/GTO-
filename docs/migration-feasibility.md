# 移行可能性検証レポート — AWS → 無料/低コスト代替

> 作成: 2026-05-15  
> 目的: 現在のAWS構成（月$15〜20）を無料または$0に近づけることが本当に可能か検証する

---

## 結論サマリー

| プラン | 月額 | 移行難易度 | 可能性 | 推奨度 |
|---|---|---|---|---|
| **A: Oracle Cloud Free Tier** | **$0** | 高 | ⚠️ 条件付き可能 | 将来検討 |
| **B: Fly.io + Neon** | **$5〜7** | 低 | ✅ 確実に可能 | クレジット切れ時の第一候補 |
| **C: AWS内でALBだけ外す** | **$3〜8** | 中 | ✅ 確実に可能 | クレジット切れ直前に実施 |
| **D: Render.com（無料枠）** | **$0** | 低 | ❌ 不可（スリープ問題） | 非推奨 |
| **現状維持（AWS Fargate+ALB）** | **$15〜22** | なし | ✅ 動作中 | クレジットある間はこれ |

**今すぐ動かすべきこと: なし。クレジット$112.29 → 残り約5〜6ヶ月。**  
**移行判断タイミング: クレジット残高が$40を切った時点（≒2026年9月頃）**

---

## このアプリ固有の制約（移行難易度を左右する要因）

### ① メモリ: 最低1GBが必要
```
task-definition.json:
  "cpu": "512"
  "memory": "1024"   ← 1GB確定
```
Puppeteer（Chromium）がメモリを多く消費するため。  
→ 256MB や 512MB 環境では起動するが、PDF生成時にOOM（Out of Memory）が発生する可能性大。

### ② Dockerイメージサイズ: 約1.5〜2GB
```
Dockerfile構成:
  python:3.11-slim
  + Chromiumシステムライブラリ（apt-get 14パッケージ）
  + Node.js 20
  + puppeteer ^24.37.5 ← Chromiumをnpm install時に自動ダウンロード（〜300MB）
  + Pythonライブラリ（firebase-admin, sqlalchemy等）
```
イメージが大きいため、デプロイ時間やレジストリ転送コストに影響。

### ③ 常時稼働が必要
- ポーカー中にリアルタイムでハンドデータが送られてくる
- スリープ（15分無通信で停止）は致命的
- Render無料枠・Fly.ioのscale-to-zeroは使えない

### ④ PostgreSQL: 現在RDS（サイズ不明だが増加傾向）
- Neon無料枠は **0.5GB上限**
- ハンドログが蓄積し続けるため、0.5GBは数ヶ月〜1年で枯渇する可能性
- 解決策: Neon有料($19/月)、またはVM内にPostgreSQLを自己ホスト

### ⑤ 環境変数: 10個（Firebase系・AI API・DB接続）
- Secrets Manager依存は新環境では不要（各PaaSに同等機能あり）
- 移行コストは低い（コード変更なし、環境変数名はそのまま）

### ⑥ アーキテクチャ（ARM vs x86）
- 現在のFargate: x86（amd64）
- Oracle Cloud A1: **ARM（aarch64）** ← 注意点
- python:3.11-slim と puppeteer はARM対応済み
- ビルド時に `--platform linux/amd64` を外す必要あり

---

## プラン別詳細検証

---

### プランD: Render.com 無料枠 ← まず除外

**結論: ❌ 本番用途には使えない**

| 項目 | 内容 |
|---|---|
| 無料Webサービス | 512MB RAM / 750時間/月（常時稼働OK） |
| スリープ | **15分無通信で停止** ← 致命的 |
| Docker対応 | あり |
| Puppeteer | 公式ドキュメントあり（動作確認済み） |
| PostgreSQL無料 | あり（90日のみ、以降$7/月） |

Uptime Robotで定期pingする回避策はあるが、プロダクション用途としてリスクが高い。  
**→ 除外確定。**

---

### プランB: Fly.io + Neon PostgreSQL

**結論: ✅ 確実に動く。月$5〜7。**

#### Fly.ioの現状（2026年時点）
- **無料枠は廃止済み**（2024年に終了）
- 新規登録で$5トライアルクレジットのみ
- その後はクレジットカード必須・従量課金

#### コスト試算
| リソース | スペック | 月額 |
|---|---|---|
| Fly Machine | shared-cpu-2x / 1GB RAM | ~$7.20 |
| Fly Machine | shared-cpu-1x / 512MB RAM | ~$3.19（OOMリスクあり） |
| Neon PostgreSQL | 無料枠（0.5GB） | $0 |
| **合計** | | **$3〜7** |

#### Puppeteer動作確認
- Fly.io公式ドキュメントに **Puppeteer デプロイガイドあり**（2024〜2025年も更新継続）
- コミュニティでも多数の動作報告
- Dockerfileに `--no-sandbox` フラグ追加が必要（Chromium起動オプション）

#### デプロイ方法（GitHub Actions）
```yaml
# .github/workflows/deploy.yml の変更差分イメージ
- name: Deploy to Fly.io
  uses: superfly/flyctl-actions/setup-flyctl@master
- run: flyctl deploy --remote-only
  env:
    FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```
→ AWS関連の5ステップが1ステップに置き換わる。学習コスト: **低**

#### Neon無料枠の懸念点
- 0.5GB上限: ハンドデータが増えると数ヶ月で枯渇する可能性
- auto-suspend（5分無通信でcompute停止）: クエリは遅延するが**データは消えない**
- 解決策候補: Neon有料($19/月)、またはFly PostgreSQL（Fly VM上に自己ホスト）

#### 移行手順ドラフト
```bash
# 1. Fly CLI インストール
brew install flyctl
flyctl auth login

# 2. アプリ初期化（Dockerfileを自動検出）
flyctl launch --name gto-app --region nrt  # nrt = 東京

# 3. メモリを1GBに設定
flyctl scale memory 1024

# 4. 環境変数を設定（Secrets Managerの10個を移行）
flyctl secrets set \
  FIREBASE_API_KEY="..." \
  FIREBASE_AUTH_DOMAIN="..." \
  FIREBASE_PROJECT_ID="..." \
  FIREBASE_SERVICE_ACCOUNT_JSON="..." \
  ADMIN_UID="..." \
  GEMINI_API_KEY="..." \
  GROQ_API_KEY="..." \
  DATABASE_URL="postgresql://neon接続文字列" \
  USE_POSTGRES="true" \
  PORT="5000"

# 5. デプロイ
flyctl deploy

# 6. Firebase承認済みドメインにFly.ioのURLを追加
# Firebase Console → Authentication → Authorized domains
# → gto-app.fly.dev を追加

# 7. GitHub Actions secrets更新
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY → 削除
# FLY_API_TOKEN → 追加
```

---

### プランA: Oracle Cloud Free Tier (OCI)

**結論: ⚠️ $0だが、条件付き。本当に0円を目指すなら最終的にここ。**

#### スペック（永久無料）
| リソース | 内容 |
|---|---|
| Compute | Ampere A1 Flex: 4 OCPU + 24GB RAM（ARM64） |
| Storage | 200GB ブロックストレージ |
| 帯域 | 10TB/月 アウトバウンド |
| PostgreSQL | VM内に自己ホスト（制限なし）|
| 月額 | **$0** |

#### 問題点① キャパシティ不足（最大の障壁）
- **東京リージョン（ap-tokyo-1）でA1インスタンスの取得がほぼ不可能**
- 「Out of host capacity」エラーが頻発
- 対処: 自動リトライスクリプトを数日〜1週間回し続けると取得できる場合がある
  - [oci-arm-host-capacity](https://github.com/hitrov/oci-arm-host-capacity) が定番ツール
- 別リージョン（大阪 ap-osaka-1）の方がやや空きあり

#### 問題点② アイドル回収
- **CPU使用率が7日間95パーセンタイルで20%未満だとOracleがVMを回収**
- 対処: `/health` エンドポイントへの定期pingや、軽いcronバッチで使用率を維持

#### 問題点③ ARMアーキテクチャ
- 現在のDockerイメージはx86（amd64）ビルド
- A1はARM64（aarch64）
- 対処: GitHub Actionsで `--platform linux/arm64` を付けてビルド
- python:3.11-slim・puppeteer・主要ライブラリはARM64対応済み（要動作確認）

#### 問題点④ 自己管理コスト
| AWSで管理 | OCIでは自分で管理 |
|---|---|
| ECS（コンテナ再起動） | systemd / docker compose restart |
| RDS（バックアップ・パッチ） | pg_dump定期バックアップ / 手動メンテ |
| ALB（ヘルスチェック） | Nginx / Caddy 設定 |
| CloudWatch（ログ） | journald / docker logs |

#### ARMでのDockerfile修正が必要な箇所
```dockerfile
# puppeteer はARMでChromiumのダウンロードパスが変わる場合がある
# 明示的にインストール済みChromiumを使う設定を追加するか要確認
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
# ただしaptでchromiumを入れた場合（現在のDockerfileでは入れていない）
```
→ **現状のDockerfileはChromiumをpuppeteer自身にダウンロードさせている。**  
→ ARM環境ではpuppeteerがChromiumをダウンロードできるが、適切なバイナリが取得されるか動作確認が必要。

---

### プランC: AWS内でALBだけ外す（EC2 + Elastic IP）

**結論: ✅ 最も安全。AWS知識の延長で実施可能。月$3〜8へ。**

#### 変更内容
```
現状:
  ALB($16) → ECS Fargate($3) → RDS($1)

変更後:
  EC2 t4g.micro($0〜3) + Nginx → Docker → RDS($1)
  ↑ Elastic IP（固定IP、$0）
```

#### コスト
| 期間 | 月額 |
|---|---|
| AWSクレジット消化中 | ほぼ$0（クレジットで吸収） |
| 無料枠内（12ヶ月） | ~$1（RDS + SM） |
| 無料枠終了後 | ~$8〜10 |

#### デプロイ方式の変更
```
現状: ECS ローリングアップデート（GitHub Actions → ECR → ECS）
変更後: SSH deploy（GitHub Actions → EC2 SSH → docker pull & restart）
```
```yaml
# GitHub Actions変更イメージ
- name: Deploy via SSH
  uses: appleboy/ssh-action@v1
  with:
    host: ${{ secrets.EC2_HOST }}
    username: ec2-user
    key: ${{ secrets.EC2_SSH_KEY }}
    script: |
      docker pull 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
      docker stop gto-app || true
      docker run -d --name gto-app --restart unless-stopped \
        -p 5000:5000 \
        --env-file /home/ec2-user/gto.env \
        273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
```

#### 懸念点
- EC2の手動管理（OS更新・再起動対応）
- ECSの自動ヘルスチェック・再起動がなくなる（Nginx + docker restart policyで補完）
- Firebase AuthのAuthorized domainsにElastic IPのDNSを追加必要

---

## データ移行（RDS → Neon）の現実的な工数

移行が必要な場合（プランB）の手順:

```bash
# 1. 現在のRDSをダンプ
pg_dump "$DATABASE_URL" > backup.sql

# 2. Neonのプロジェクト作成（Web UIで5分）
# 3. 接続文字列を取得して流し込み
psql "$NEON_DATABASE_URL" < backup.sql

# 4. DATABASE_URLを更新するだけ（コード変更不要）
```

**注意: Neonの0.5GB無料枠の確認**
```sql
-- 現在のRDSのDB容量確認（本番で実行）
SELECT pg_size_pretty(pg_database_size(current_database()));
```
→ これが500MB未満なら無料枠で移行可能。超えていれば有料Neonまたは自己ホストを選択。

---

## 推奨タイムライン

```
2026-05-15（今日）
    │ ✅ 現状維持（クレジット$112.29）
    │
2026-09月頃（クレジット残高$40切ったら）
    │ → 判断ポイント
    │   ・アプリが有料サービスとして成立している？
    │       YES → 収益でAWSコスト($15〜20)をカバー → 現状維持
    │       NO → プランBまたはCへ移行
    │
    ├── プランB（Fly.io + Neon）: 1〜2日で移行完了
    │       月$5〜7。クレジットカードが必要だが設定が簡単。
    │
    └── プランC（AWS EC2 + ALBなし）: 3〜5日で移行完了
            月$3〜8。AWSのまま。慣れた環境で変更箇所が最小。

2026年後半〜
    Oracle Cloud A1は「余裕があれば試す」レベル。
    キャパシティ取得に運と時間がかかるため、メインのバックアップ手段として温存。
```

---

## 最終的に$0にできるか？

| 条件 | 判定 |
|---|---|
| Oracle Cloud A1が東京で取得できれば | **$0/月 可能** |
| PostgreSQLをVM内自己ホストすれば | DB費用も$0 |
| Puppeteer on ARM64 が動作すれば | 追加費用なし |
| アイドル回収を防ぐ仕組みを作れば | 継続稼働可能 |

**答え: 「$0は原理的に可能だが、取得の手間と運用の自己責任が伴う」**

現実的な最安値は **Fly.io の $5〜7/月**。  
これが学習コストと信頼性のバランスが最も良い選択肢。

---

## 参考: Fly.io vs Oracle Cloud の比較

| 観点 | Fly.io ($5〜7) | Oracle Cloud ($0) |
|---|---|---|
| 初期セットアップ | `fly launch` 1コマンド | OCIコンソール操作 + Nginx + systemd |
| インスタンス取得 | 即時 | 東京は取得困難（数日〜数週間） |
| デプロイ | `fly deploy`（Railway同等） | SSH + docker pull |
| DB管理 | Neon（マネージド） | 自己ホスト（バックアップ自己責任） |
| 障害対応 | Fly側が自動再起動 | 自分で監視・再起動設定 |
| ARM問題 | なし（x86） | あり（Docker再ビルド必要） |
| 月額 | $5〜7 | $0（ただし取得できれば） |

---

*最終更新: 2026-05-15*
