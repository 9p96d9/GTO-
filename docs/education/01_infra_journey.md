# インフラ遍歴と技術解説
## 個人開発者のためのクラウドインフラ学習ノート
### HRep（旧: PokerGTO）開発を通じて学んだこと

> 対象: エンジニア経験なしで個人開発をしている自分自身への解説  
> 目的: 「なぜその技術を選んだか」「どんな問題があったか」を体系的に理解する  
> 作成: 2026-05-18

---

## 目次

1. [アプリの全体像](#1-アプリの全体像)
2. [時代①: Railway（2025年〜2026年4月）](#2-時代-railway)
3. [時代②: AWS ECS + ALB（2026年4月〜5月）](#3-時代-aws-ecs--alb)
4. [時代③: AWS EC2 + Cloudflare Tunnel（2026年5月〜現在）](#4-時代-aws-ec2--cloudflare-tunnel)
5. [技術用語辞典](#5-技術用語辞典)
6. [コスト比較表](#6-コスト比較表)
7. [学んだこと・失敗談](#7-学んだこと失敗談)
8. [なぜDockerが重かったか（WeasyPrint移行の詳細）](#8-なぜdockerが重かったかweasyprint移行の詳細)
9. [AWSコストの読み方](#9-awsコストの読み方)
10. [2027年以降の移行先選択肢](#10-2027年以降の移行先選択肢)

---

## 1. アプリの全体像

**HRep（旧: PokerGTO）** は、ポーカーのハンド履歴を自動で分析・PDF化するWebアプリ。

### データの流れ

```
T4ポーカーサイト（ゲーム画面）
    │ WebSocket通信
    ▼
Chrome拡張機能（interceptor.js）
    │ ハンドデータをキャプチャ
    ▼
Firebase Firestore（クラウドDB）
    │
    ▼
HRepサーバー（FastAPI / Python）
    │
    ├── AI解析（Groq llama-3.3-70b / Gemini 2.5 Flash）
    ├── GTO分類（青線/赤線）
    └── PDF生成（WeasyPrint）
```

### 技術スタック

| レイヤー | 技術 | 役割 |
|---|---|---|
| バックエンド | FastAPI + Python 3.11 | Webサーバー・API |
| AI | Groq / Gemini API | ハンド評価・アドバイス |
| 認証 | Firebase Auth（Google OAuth） | ユーザーログイン |
| DB | Firebase Firestore + PostgreSQL | ハンドデータ・解析結果 |
| PDF | WeasyPrint（現在） | レポート生成 |
| 拡張 | Chrome MV3 | ポーカーサイトのデータ収集 |
| インフラ | AWS EC2 + Cloudflare Tunnel | ホスティング |

---

## 2. 時代①: Railway（2025年〜2026年4月）

### Railwayとは

Railwayは「GitHubにpushするだけでデプロイできる」クラウドサービス。  
インフラの知識がゼロでもWebアプリを公開できる。

### 構成

```
GitHub（main ブランチ）
    │ 自動デプロイ
    ▼
Railway（コンテナを自動管理）
    │ 自動HTTPS・自動ドメイン
    ▼
gto-production.up.railway.app
```

### 良かった点

- **設定がほぼゼロ**。GitHubと連携するだけで動いた
- 無料クレジットがあり、最初はほぼ無料で運用できた
- データベース（PostgreSQL）もRailway上に追加できた

### 問題になったこと

- 無料クレジットが枯渇 → 月$5かかるようになった
- PDFをPuppeteer（Chrome内蔵）で生成していたため、Dockerイメージが重かった（~500MB extra）
- Railway自体のコスト対効果が悪化してきた

### なぜ辞めたか

月$5を払い続けることへの疑問 + AWSに移行して本格的なインフラを学びたいという動機。

---

## 3. 時代②: AWS ECS + ALB（2026年4月〜5月）

### AWSとは

Amazon Web Servicesの略。世界最大のクラウドサービス。  
「クラウドで何かするなら大体AWSにある」くらい種類が豊富。

### この時代の構成

```
インターネット
    │ HTTP:80
    ▼
ALB（Application Load Balancer）
  ← ここがインターネットとアプリの「玄関」
    │
    ▼
ECS Fargate（コンテナ）
  ← アプリが動いている場所。サーバーを自分で管理しなくていい
    │
    ├── ECR（コンテナイメージの倉庫）
    ├── Secrets Manager（パスワード管理）
    └── RDS PostgreSQL（データベース）
```

### 各サービスの役割（用語解説）

#### ALB（Application Load Balancer）= 玄関係

インターネットからのアクセスを受け付けて、アプリに振り分ける役割。  
「`gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com`」というURLがここのアドレスになる。

**なぜ必要か:**  
ECS FargateはコンテナのIPが毎回変わる。ALBが「窓口」になることで、  
アプリが再起動してもURLが変わらない。

**コスト:** ~$7〜8/月（固定費）← これが最大の問題だった

#### ECS Fargate = サーバーレスコンテナ

Dockerコンテナを「サーバー（EC2）なし」で動かせるサービス。  
「0.5vCPU・1GB RAM」という小さなスペックで1台稼働させていた。

**Dockerとは何か:**  
アプリと必要なソフトをひとまとめにした「箱（コンテナ）」のこと。  
この箱さえあれば、どのサーバーでも同じように動く。  
Dockerfileという設計図を書き、GitHub Actionsがビルド（箱を作る）してECRに保存する。

**Fargateのコスト:** ~$5〜7/月

#### ECR（Elastic Container Registry）= Dockerイメージの倉庫

GitHubにコードを保存するように、Dockerの「箱（イメージ）」を保存する場所。  
ビルドするたびにここに保存され、ECSはここから取り出して動かす。

#### RDS PostgreSQL = データベース

Firebase Firestoreとは別に、サーバー側でも解析結果を保存するために追加（Phase 19）。  
`gto-rds-sg` というセキュリティグループで守られ、VPC内からしかアクセスできない。

#### Secrets Manager = パスワード管理

APIキーやDB接続情報などの「秘密の値」を暗号化して保管するサービス。  
アプリ起動時にここから読み取って環境変数として渡す。  
`gto/production` というシークレット名で管理。月 ~$0.40

#### VPCとセキュリティグループ = ファイアウォール

VPC（Virtual Private Cloud）は「AWS上の自分専用ネットワーク」。  
セキュリティグループは「どこからの通信を許可するか」のルール。

```
gto-alb-sg  → インターネットからの80番ポートを受け付ける
gto-ecs-sg  → ALBからの5000番ポートのみ受け付ける
gto-rds-sg  → ECS（またはEC2）からの5432番ポートのみ受け付ける
```

これにより、DBが直接インターネットに晒されるのを防ぐ。

### GitHub Actions（CI/CD）

「push したら自動でデプロイ」の仕組み。`.github/workflows/deploy.yml` に書く。

```
git push origin main
  └→ GitHub Actions 起動
       ├→ Docker build（コードから箱を作る）
       ├→ ECR push（倉庫に保存）
       └→ ECS ローリングアップデート（無停止で新バージョンに切り替え）
```

### IAMとは（権限管理）

IAM = Identity and Access Management。  
「誰が何をできるか」を管理するAWSの仕組み。

例: ECSコンテナが「ECRからイメージを取得したい」「Secrets Managerを読みたい」  
→ `gto-ecs-task-execution-role` というIAMロールに権限を付与する  
→ ECSはそのロールを「着込む」ことで権限を得る

インスタンスプロファイル = EC2にIAMロールを着込ませる仕組み（後述）

### この時代の問題

| 問題 | 内容 |
|---|---|
| コストが高い | ALB $7+ ECS $5 = 月$12以上。Firebase移行前より高かった |
| PDFにChromium必要 | Puppeteer（=Chrome内蔵）でPDF生成。Dockerが重くビルドに時間がかかる |
| ALBは固定コスト | 使わなくても$7かかる |
| 独自ドメインなし | 長いAWS URLが使いにくい |

---

## 4. 時代③: AWS EC2 + Cloudflare Tunnel（2026年5月〜現在）

### 移行の目的

**目標: 月額コストを限りなく0に近づける**

ALBとECS Fargateをやめる → EC2（仮想サーバー）を直接使う  
Cloudflare Tunnelを使う → ALBなしでHTTPS化できる

### WeasyPrint移行（PDF生成の刷新）

PDFをPuppeteer（Node.js + Chrome）からWeasyPrint（Python）に変更。

**なぜ変えたか:**  
- Chromiumは約300MB。Dockerイメージが重かった  
- WeasyPrintはPythonのライブラリ。別途ブラウザ不要  
- PythonアプリなのにわざわざNode.jsを入れる必要がなくなる

**変更内容:**  
```
変更前: Dockerfile に Node.js 20 + Chromium系ライブラリ14個
変更後: WeasyPrint用ライブラリ6個（libpango, libcairo2, etc.）
        scripts/generate.js → scripts/generate.py
        scripts/generate_noapilist.js → scripts/generate_noapilist.py
```

### EC2とは

EC2（Elastic Compute Cloud）= AWSの「仮想サーバー」。  
物理的なサーバーをAWSが用意してくれて、好きなサイズで借りられる。

**t3.micro:**  
- 2 vCPU（仮想CPU）  
- 1GB RAM  
- 無料枠: 750時間/月（24時間×31日 = 744時間 ≒ ずっと無料）  
- 無料期間: アカウント作成から12ヶ月

**ECSとの違い:**  
- ECS Fargate: 「サーバー管理不要」の代わりにコスト高  
- EC2: サーバーを自分でセットアップするが、安い（無料枠あり）

### Cloudflare Tunnelとは

Cloudflare TunnelはALBの代わりになる「無料のトンネル」サービス。

**仕組み:**

```
ユーザー → HTTPS → Cloudflare（証明書・セキュリティ担当）
                        │ 暗号化トンネル（アウトバウンドのみ）
                        ▼
              cloudflared デーモン（EC2上で動くプログラム）
                        │
                        ▼
              http://localhost:5000（アプリ）
```

**ポイント:**  
- EC2のポート（80/443）を開けなくていい  
- セキュリティグループはSSH（22番）だけでOK  
- HTTPS証明書はCloudflareが自動発行・更新  
- **完全無料**（ALBは月$7〜8かかっていた）

**なぜアウトバウンドだけでいいの?**  
cloudflaredがCloudflareのサーバーに向かって「接続しに行く」。  
接続が確立したあと、Cloudflare側がそのトンネルを経由してアプリにリクエストを送る。  
外からEC2に「入ってくる」通信がないため、インバウンドを開ける必要がない。

### hrep.appドメイン

独自ドメインを取得した理由:
1. ALBのURLは長くて使いにくい
2. Googleログイン（Firebase Auth）に独自ドメインが必要
3. `https://` を強制する `.app` ドメインはセキュリティ的にも良い

`.app` はGoogleがスポンサーのTLD（トップレベルドメイン）で、  
HSTS（HTTP Strict Transport Security）がプリロードされている。  
つまりブラウザが「このドメインは必ずHTTPS」と知っている。

**取得情報:**
- レジストラ: Cloudflare Registrar（DNSも同じCloudflareで管理 → 相性最高）
- 年額: $14.20（¥2,200相当）
- 有効期限: 2027-05-17（自動更新）

### EC2セットアップの実際の手順記録

```
1. EC2インスタンス起動（t3.micro / Amazon Linux 2023）
   → キーペア gto-key.pem をダウンロード

2. IAMロール作成（gto-ec2-role）
   → ポリシー: AmazonEC2ContainerRegistryReadOnly / SecretsManagerReadWrite
   → インスタンスプロファイル(gto-ec2-profile)作成・アタッチ

3. SSH接続して Docker インストール
   sudo dnf install -y docker
   sudo systemctl enable --now docker

4. ECR ログイン → イメージ pull
   aws ecr get-login-password | docker login ...
   docker pull 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest

5. Secrets Manager から gto.env 生成（Python スクリプト）

6. Docker コンテナ起動
   docker run -d --name gto-app --restart unless-stopped ...

7. cloudflared インストール・トンネル作成・systemd登録

8. Firebase Auth に hrep.app を追加

9. GitHub Actions の deploy.yml を ECS → EC2 SSH方式に変更
```

### GitHub Actions（EC2 SSH方式）

```yaml
# 変更前: ECS ローリングアップデート
- name: Deploy to ECS
  uses: aws-actions/amazon-ecs-deploy-task-definition@v2

# 変更後: SSH でEC2に入ってdocker restart
- name: Deploy to EC2 via SSH
  uses: appleboy/ssh-action@v1
  with:
    host: ${{ secrets.EC2_HOST }}
    key: ${{ secrets.EC2_SSH_KEY }}
    script: |
      docker pull .../gto-app:latest
      docker stop gto-app && docker rm gto-app
      docker run -d --name gto-app ...
```

### 現在のコスト

| サービス | 月額 | 備考 |
|---|---|---|
| EC2 t3.micro | $0 | 無料枠（〜2027/04） |
| RDS db.t4g.micro | $0 | 無料枠（〜2027/04） |
| Cloudflare Tunnel | $0 | 永久無料 |
| Secrets Manager | $0.40 | 1シークレット固定 |
| ECR | $0.10 | 1.5GBのイメージ保存 |
| hrep.app | $1.18 | $14.20/年の月割り |
| Railway | $0 | 2026-05-18解約 |
| **合計** | **~$1.68** | **旧 $14.7/月から98%削減** |

---

## 5. 技術用語辞典

### インフラ系

| 用語 | 意味 |
|---|---|
| **クラウド** | インターネット経由で借りるサーバー・ストレージ等 |
| **コンテナ** | アプリと依存ライブラリを一緒に梱包した「箱」。Dockerが代表 |
| **Docker** | コンテナを作る・動かすための仕組み |
| **Dockerfile** | コンテナの設計図。何をインストールし何のコマンドで起動するか書く |
| **イメージ** | Dockerfileからビルドした「箱の実体」。ECRに保存する |
| **コンテナ** | イメージを実際に動かしたもの |
| **ECR** | AWSのDockerイメージ倉庫 |
| **ECS** | AWSのコンテナ管理サービス |
| **Fargate** | EC2を使わずコンテナを動かすECSの動作モード |
| **EC2** | AWSの仮想サーバー |
| **ALB** | AWSのロードバランサー（玄関）|
| **RDS** | AWSのマネージドDB |
| **VPC** | AWS上の自分専用ネットワーク |
| **セキュリティグループ** | VPC内のファイアウォール（通信許可ルール） |
| **IAM** | AWSの権限管理 |
| **IAMロール** | 「この操作を許可する」権限セット |
| **インスタンスプロファイル** | EC2にIAMロールを着込ませる仕組み |
| **Secrets Manager** | AWSの秘密情報管理サービス |
| **CloudShell** | AWSコンソール上で使えるブラウザ内ターミナル |

### Web・ネットワーク系

| 用語 | 意味 |
|---|---|
| **HTTP** | Webの通信プロトコル。暗号化なし |
| **HTTPS** | HTTPの暗号化版。SSL/TLS証明書が必要 |
| **DNS** | ドメイン名（hrep.app）をIPアドレスに変換する仕組み |
| **CNAME** | DNSのレコード種類。「このドメイン名はあのドメインを指す」 |
| **TLD** | .app や .com などドメインの末尾部分 |
| **HSTS** | 「このドメインは必ずHTTPS」とブラウザに記憶させる仕組み |
| **Cloudflare Tunnel** | EC2のポートを開けずにHTTPS公開できる無料サービス |
| **cloudflared** | Cloudflare TunnelのEC2側デーモン（常駐プログラム） |
| **systemd** | Linuxのサービス管理。OS起動時に自動実行させる仕組み |

### 開発系

| 用語 | 意味 |
|---|---|
| **GitHub Actions** | GitHubのCI/CD。pushを検知して自動でビルド・デプロイ |
| **CI/CD** | 継続的インテグレーション/デリバリー。自動テスト・デプロイの仕組み |
| **deploy.yml** | GitHub Actionsの設定ファイル |
| **Firebase** | Googleのモバイル/Web開発プラットフォーム |
| **Firebase Auth** | Googleログイン等の認証機能 |
| **Firestore** | Firebaseのリアルタイムデータベース |
| **FastAPI** | PythonのWebフレームワーク。高速・型安全 |
| **uvicorn** | FastAPIを動かすASGIサーバー |
| **WeasyPrint** | PythonのHTML→PDF変換ライブラリ |
| **Puppeteer** | Node.jsからChromeを操作するライブラリ（旧PDF生成方法） |
| **Alembic** | PythonのDBマイグレーションツール |

---

## 6. コスト比較表

| 時代 | 構成 | 月額 | 期間 |
|---|---|---|---|
| Railway時代 | Railway（Hobby） | $5 | 〜2026/04 |
| AWS ECS+ALB | ALB+Fargate+RDS | ~$14.7 | 2026/04〜05 |
| **現在** | **EC2+Cloudflare+RDS** | **~$1.68** | 2026/05〜 |
| 無料枠切れ後 | EC2+RDS（実費） | ~$21 | 2027/04〜 |

---

## 7. 学んだこと・失敗談

### ① t2.microとt3.microの違いで詰まった

EC2の無料枠は「t2.micro 750時間/月」が公式の案内だが、  
このAWSアカウントでは t2.micro が対象外で t3.micro が対象だった。  
→ 教訓: 無料枠は**自分のアカウント設定ページで確認**すること

### ② IAMインスタンスプロファイルを作り忘れた

IAMロール（gto-ec2-role）を作っただけでは EC2 から使えない。  
**インスタンスプロファイル（gto-ec2-profile）** を別途作成してロールを追加し、  
EC2にアタッチして初めてEC2がIAMロールを使える。  
→ CloudShell で3コマンド必要だった

### ③ Secrets ManagerのシークレットIDに注意

シークレット名: `gto/production`  
ARN に含まれる末尾のランダム文字列（`-cxsxWn`）はARNの一部であり、シークレット名ではない。

### ④ Dockerのenv-fileでの改行処理

FIREBASE_SERVICE_ACCOUNT_JSON（JSONの中に改行を含む）を env-file に書くとき、  
改行を `\n` に置換して1行にする必要がある。  
Pythonスクリプトで `str(v).replace('\n', '\\n')` で対応。

### ⑤ Chrome Safe Browsing がZIPをブロック

HTTP（暗号化なし）のサイトからZIPをダウンロードすると、  
Chrome 117以降はデフォルトでブロックする。  
→ HTTPS化（Cloudflare Tunnel導入）で解決

### ⑥ ECSとEC2を同時に動かしていてコストが増えた

EC2移行作業中、ECSとALBを止めずに両方動かしていた。  
5月のコストが $38（18日間）に膨らんだ。  
→ 移行完了後すぐにECS停止・ALB削除すること

### ⑦ Cost ExplorerはGrossコスト（クレジット前）を表示する

Cost Explorerで高い金額が表示されても、クレジット（無料枠）が適用されれば実際の請求額は低い。  
「クレジット」ページで実際の消費・残高を確認すること。

### ⑧ cloudflared のインストールURLが変わっていた

公式ドキュメントにあるRPMインストール方法が機能しないことがある。  
GitHubリリースから直接バイナリをダウンロードする方が確実：  
```bash
curl -fsSL 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64' \
  -o /tmp/cloudflared && sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared
```

### ⑨ PostgreSQL移行後: JSONBカラムの展開ミス

Firebase の `doc.to_dict()` は `{"hand_json": {...}}` という形でデータを返す。  
PostgreSQLから取得した行を `**r[1]` で展開すると `hand_json` キーが消えて中身がトップレベルに出てきてしまう。

```python
# NG: hand_json の中身がトップレベルに展開されてしまう
d = {"hand_id": r[0], **r[1], "captured_at": r[2]}
# → hand_json というキーが消える → 後続の item.get("hand_json") が {} を返す

# OK: Firebase と同じ形式に合わせる
d = {"hand_id": r[0], "hand_json": r[1], "captured_at": r[2]}
```

**教訓:** 2つのDBの「出力形式の差異」は目に見えないので特に注意が必要。

### ⑩ SQLAlchemy の `::jsonb` 構文エラー

PostgreSQL では `CAST` の代わりに `::型名` という短縮記法が使える。  
しかし SQLAlchemy の `text()` はコロンを「バインドパラメータの開始」と解釈する。  
`:cart::jsonb` を見ると「`cart:` というパラメータ名」と誤解析してSQL構文エラーになる。

```python
# NG: SQLAlchemy が :cart: とパラメータ名を誤解析する
text("UPDATE analyses SET active_cart = :cart::jsonb WHERE ...")

# OK: CAST() 形式を使う
text("UPDATE analyses SET active_cart = CAST(:cart AS jsonb) WHERE ...")
```

### ⑪ race condition（競合状態）の実例

「同時に2つの処理が走って片方の結果がもう片方に上書きされる」問題を race condition と言う。  
このアプリで実際に踏んだケースが2つある。

**ケース①: カート「カートが空です」エラー**

カートに手を追加してすぐ「解析実行」を押すと 400エラーになっていた。  
原因: カートの変更はDBへの書き込みを600ms遅らせる非同期処理。「解析実行」がDBを読んだ時点でまだ空だった。  
解決: 解析実行ボタン押下時に「今カートに入っているハンド番号」をリクエストボディに乗せて送るようにした。サーバーはDBを読まずボディの値を使う。

**ケース②: 拡張機能の認証 race condition**

ゲーム開始直後のハンドが保存されないことがあった。  
原因: Firebase の認証完了を待たずにハンド保存処理を動かしていた。`_user` が null のままなのでスキップされていた。  
解決: 認証完了を Promise で待つようにした。

```js
// NG: onAuthStateChanged を登録するだけで _user 確定を待たない
onAuthStateChanged(_auth, user => { _user = user; });

// OK: Promise で _user 確定まで await する
await new Promise(resolve => {
  onAuthStateChanged(_auth, user => {
    _user = user;
    resolve();
  });
});
```

**教訓:** 非同期処理の「完了前に次の処理が走る」パターンは再現が難しく、デバッグに時間がかかる。  
「保存してすぐ読む」「ログインしてすぐ操作する」といった操作のタイミングで起きやすい。

---

## 8. なぜDockerが重かったか（WeasyPrint移行の詳細）

### Puppeteer時代のPDF生成フロー

```
Python（pipelines.py）
    │ subprocess で呼び出す
    ▼
node scripts/generate.js
    │ JavaScriptでHTMLを組み立て
    ▼
Puppeteer（Node.jsライブラリ）
    │ headless Chromium を起動
    ▼
Chromium（内蔵ブラウザ）
    │ HTML → PDF をレンダリング
    ▼
PDFファイル保存
```

PythonアプリなのにNode.jsとChromiumを経由するという、かなり遠回りな構成だった。

### Dockerイメージが重かった原因の内訳

```
python:3.11-slim ベース:          約700MB
+ Chromium用システムライブラリ14個: 約+100MB
+ Node.js 20（nodesourceから）:   約+120MB
+ puppeteer（npm install時に
  Chromiumを自動ダウンロード）:    約+300MB
─────────────────────────────────────────
合計:                             約1.5〜2GB
```

Chromiumは「ブラウザ」なので当然重い。PDFを作るためだけにブラウザを丸ごと入れていた状態。

### WeasyPrintで解決した理由

WeasyPrintはPythonのライブラリ。HTMLをCSSとともにPDFに変換する。  
必要なのは `libpango`（文字レイアウト）と `libcairo`（描画）という2つのライブラリだけ。

**気づき:** この2つはすでにDockerfileに入っていた（Chromiumのために）。  
つまり `requirements.txt` に `weasyprint` を追加するだけで、apt の追加はほぼ不要だった。

```
WeasyPrint移行後のイメージ構成:
python:3.11-slim:              約700MB
+ WeasyPrint用ライブラリ6個:   約+80MB
─────────────────────────────────────────
合計:                          約780MB（約1GBの削減）
```

### Fargateのメモリ要件への影響

Chromiumは起動するだけで200〜300MBのメモリを使う。  
そのため Fargate タスクを 1GB 設定にしなければならなかった。

```
変更前: Fargate 0.5vCPU + 1GB → 月約$7〜8
変更後: Fargate 0.5vCPU + 512MB → 月約$5〜6（節約）
さらにEC2無料枠（RAM 1GB）でも安全に動くようになる → ALB廃止と合わせると月約$0
```

### PDF品質の比較

| 項目 | Puppeteer（Chrome） | WeasyPrint |
|---|---|---|
| テーブルレイアウト | 完全対応 | ほぼ同等 |
| 日本語フォント | IPAフォント使用 | 同じフォント使用可能 |
| 絵文字 | カラー表示 | テキスト代替（`🔵→[青]`）|
| 全体的な品質 | 高い | 実用上問題なし |

絵文字の扱いだけが違うが、PDFの内容として読む分には問題ない。

---

## 9. AWSコストの読み方

AWSの費用は複数の画面に分散していて、見方を知らないと正確な金額がわからない。  
毎月1日に以下の3ページを確認する習慣をつけると良い。

### ページ①: クレジット残高（最重要）

```
AWSコンソール → 請求とコスト管理 → クレジット
```

AWSには「クレジット」という無料利用残高がある。  
このアカウントには新規登録ボーナス ($88.74) + 特定サービス利用ボーナス ($20×2) の合計 ~$128が付いていた。  
クレジットがある間は実際の請求額がゼロになる。

**確認項目:**
- 残高の変化（先月比）
- 有効期限（このアカウントは 2027/04/24）

### ページ②: Cost Explorer（サービス別内訳）

```
AWSコンソール → コストと使用状況の分析 → Cost Explorer → グループ化: サービス
```

**重要な注意:** Cost Explorer は **クレジット適用前のGrossコスト** を表示する。  
「$38かかってる！」と思っても、実際の請求額はクレジットで相殺されてゼロかもしれない。  
実際の支払額はクレジットページで確認する。

| 表示 | 意味 |
|---|---|
| Cost Explorer の金額 | 定価（クレジット適用前） |
| クレジットページの残高変化 | 実際にかかった費用 |
| 請求書 | 本当の請求額（最終確認） |

### ページ③: 無料利用枠の使用状況

```
AWSコンソール → 請求とコスト管理 → 無料利用枠
```

RDS が無料枠対象かどうかの確認方法: 「Explore AWS: RDS作成」クレジットの使用額が $0.00 なら無料枠対象確定。  
（RDSに課金があればこのクレジットが使われるはずのため。）

### コスト異常検知アラート

```
AWSコンソール → 請求とコスト管理 → 予算 → 予算を作成
```

このアカウントは月$30超で 9p96d9@gmail.com にメール通知が届く設定済み。  
アラートが来たら Cost Explorer で原因サービスを特定する。

### クレジット残高の「持ち方」

クレジットが枯渇してから動くのでは遅い。残高の減り方で先を予測する。

```
残高 ÷ 月間消費額 = 残り月数
例: $100 ÷ $14.7/月 ≒ 6.8ヶ月
```

**判断の目安:**
- 残高 $80 以下 → 移行計画を本格始動  
- 残高 $30 以下 → 緊急移行（次セクション参照）

---

## 10. 2027年以降の移行先選択肢

AWS無料枠が終わる2027年4月以降、または自費運用に切り替えるときのための比較。

### このアプリ固有の制約（移行先選定で考慮すること）

| 制約 | 内容 | 影響 |
|---|---|---|
| 常時稼働必須 | ポーカー中にリアルタイムでデータが送られる | スリープ機能のある無料枠は使えない |
| RAM 1GB以上 | WeasyPrint移行後は512MBでも動くが余裕を見て1GB | Render無料枠（512MB）は不安 |
| PostgreSQLが必要 | 解析結果・ハンドデータを保存 | DB費用が別途かかる場合がある |

### 選択肢の比較

| プラン | 月額 | 難易度 | 向いているケース |
|---|---|---|---|
| **Fly.io + Neon** | $5〜7 | 低 | クレジット切れ後の第一候補。Railway感覚でデプロイできる |
| **EC2継続（実費）** | ~$21 | 低 | 現構成のまま。アプリが収益化できていれば許容範囲 |
| **Oracle Cloud A1** | $0 | 高 | 永久無料だが東京リージョンは取得困難。ARM64対応も必要 |
| **Render.com** | $0〜7 | 低 | ❌ 無料枠は15分スリープあり・本番用途不可 |

### Fly.ioへの移行イメージ

Fly.ioはRailwayに近い感覚で使える。GitHub Actionsの変更も最小限。

```bash
# 1. Fly CLI インストール
# （公式サイトからインストーラーをダウンロード）

# 2. アプリ初期化（Dockerfileを自動検出）
flyctl launch --name gto-app --region nrt  # nrt = 東京

# 3. 環境変数を設定（Secrets Managerの10個を移行）
flyctl secrets set \
  FIREBASE_API_KEY="..." \
  DATABASE_URL="postgresql://neon接続文字列" \
  USE_POSTGRES="true" \
  PORT="5000"
  # ... 残りも同様

# 4. デプロイ
flyctl deploy

# GitHub Actions は deploy.yml の「SSH deploy部分」を
# 「flyctl deploy」1行に置き換えるだけ
```

DBについては Neon（無料枠0.5GB）を使うか、Fly.io上にPostgreSQLをセルフホストするかを選ぶ。

### Oracle Cloudの注意点

永久無料で4コア・24GB RAMという破格のスペックだが、課題がある。

- **東京リージョンの取得困難**: 「キャパシティ不足」エラーが常態化。自動リトライを数日回し続ける必要がある
- **ARM64アーキテクチャ**: 現在のDockerイメージはx86用。ARM向けに再ビルドが必要
- **アイドル回収**: CPU使用率が7日間平均20%未満だとVMを回収されることがある

興味があれば「余裕があれば試す」程度の位置づけで。

### 判断のタイミング

```
今（2026年）:   AWS無料枠があるので何もしなくてOK
2027年1月頃:   クレジット残高を確認・残り月数を計算
2027年3月頃:   移行先を確定・テスト環境を構築
2027年4月24日: AWS無料枠終了 → 移行済みであれば月$5〜7で継続
               移行できていなければ月$21の実費課金開始
```

---

*最終更新: 2026-05-18*  
*作者: 9p96d9（個人開発者）+ Claude Code（Anthropic）*
