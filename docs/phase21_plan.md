# Phase 21 開発計画書

**作成:** 2026-05-20  
**ステータス:** 草案・レビュー中  
**背景:** AWS課金の最適化 + データモデル刷新 + スケール設計の見直し

---

## 問題意識と方向転換

### 現状の課題

| 問題 | 詳細 |
|---|---|
| コスト | EC2($8) + RDS($12) = $20/月。ECS/ALB削除後も高止まり |
| `classified_snapshot` 設計 | 1解析ぶんの全ハンド詳細を最大900KBのblobで保存。スケールしない |
| 3D可視化 | `classified_snapshot` 依存（19-Bが未着手のまま）|
| ユーザー価値 | 「セッションごとの解析を見返す」より「全蓄積ハンドの傾向を掴む」方が実際の使い方に近い |

### 方向転換の核心

```
今まで: 解析ごとにsnapshotを保存 → 見返す
これから: ハンドを溜める → 統計を取る → PDFや3Dでローカル保存
```

EVグラフはTen-Fourの本家が提供するので不要。  
HandReporterの独自価値 = **GTO分類統計**（青線/赤線・ポジション・カテゴリ）。  
AI解析 = カートで任意のN手だけ（現状維持）。

---

## Phase 21-A: インフラ移行（t3.small + EC2 PostgreSQL）

### 変更内容

```
Before: EC2 t3.micro ($8) + RDS db.t4g.micro ($12) = $20/月
After:  EC2 t3.small ($15) + PostgreSQL on EC2    = $15/月
```

**メモリ余裕の確保：**

| プロセス | t3.micro(1GB) | t3.small(2GB) |
|---|---|---|
| OS + Docker | 150MB | 150MB |
| FastAPI/uvicorn | 250MB | 250MB |
| PostgreSQL（設定次第） | 250MB ⚠️ | 250MB ✅ |
| 解析処理ピーク | 余裕なし ❌ | ~1.3GB余裕 ✅ |

### 実装手順

#### Step 1: EC2インスタンスタイプ変更（ダウンタイム約5分）

```bash
# EC2停止
aws ec2 stop-instances --instance-ids i-06c53e45fc140cb9c

# タイプ変更
aws ec2 modify-instance-attribute \
  --instance-id i-06c53e45fc140cb9c \
  --instance-type t3.small

# 起動
aws ec2 start-instances --instance-ids i-06c53e45fc140cb9c
# ※ IPが変わるのでGitHub Secrets の EC2_HOST を更新
```

#### Step 2: EC2上にPostgreSQL構築

Docker Composeで管理する（現行の `docker run` から移行）。

**docker-compose.yml（EC2上に配置）:**
```yaml
version: '3.9'
services:
  db:
    image: postgres:18-alpine
    restart: always
    environment:
      POSTGRES_DB: gto_db
      POSTGRES_USER: gto_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"  # ループバックのみ公開

  app:
    image: 273949555510.dkr.ecr.ap-northeast-1.amazonaws.com/gto-app:latest
    restart: always
    env_file: /home/ec2-user/gto.env
    ports:
      - "5000:5000"
    depends_on:
      - db

volumes:
  pgdata:
```

**⚠️ 盲点: EBSボリューム容量確認必須**

現在のEBS容量を確認し、PostgreSQLデータ用に余裕があるか確認する。

```bash
df -h  # EC2上で実行
# /dev/xvda が少ない場合はEBSを拡張（8GB→30GBなど、+$0.8/月程度）
```

**PostgreSQL設定（t3.small向けチューニング）:**
```
shared_buffers = 128MB       # デフォルト128MBのまま（2GB環境では適切）
work_mem = 4MB
maintenance_work_mem = 64MB
max_connections = 30
```

#### Step 3: RDSからデータ移行

```bash
# EC2上で実行
pg_dump $RDS_DATABASE_URL > /tmp/gto_backup.sql

# EC2 PostgreSQLにリストア
docker exec -i gto-db psql -U gto_user -d gto_db < /tmp/gto_backup.sql

# 接続確認
docker exec -it gto-db psql -U gto_user -d gto_db -c "SELECT count(*) FROM hands;"
```

#### Step 4: DATABASE_URL更新

Secrets Manager の `DATABASE_URL` を変更：
```
# 変更前
postgresql://gto_user:xxx@gto-db.c5suwic8avyn.ap-northeast-1.rds.amazonaws.com:5432/gto_db

# 変更後
postgresql://gto_user:xxx@localhost:5432/gto_db
```

#### Step 5: GitHub Actions 更新

現行の `docker stop/rm/run` コマンドを `docker compose up -d` に変更。

#### Step 6: RDS削除

動作確認後にRDSインスタンスを削除。

```bash
aws rds delete-db-instance \
  --db-instance-identifier gto-db \
  --skip-final-snapshot
```

### バックアップ戦略（⚠️ RDS廃止で自動バックアップが消える）

```bash
# EC2上にcronを設定
# /etc/cron.d/pg-backup
0 3 * * * ec2-user docker exec gto-db pg_dump -U gto_user gto_db \
  | gzip > /home/ec2-user/backups/gto_$(date +\%Y\%m\%d).sql.gz
# 7日分保持
0 4 * * * ec2-user find /home/ec2-user/backups -mtime +7 -delete
```

---

## Phase 21-B: データモデル刷新

### 現状の問題

`classified_snapshot` は1解析分の全ハンド詳細JSONをgzip+base64した最大900KBのblob。  
26,173手を解析すると圧縮後でも数MB超になりうる。スケールしない。

### 既存資産の確認

**`analysis_hands` テーブルは Phase 19-10 で実装済み。**  
これが新モデルの中心になる。

```sql
analysis_hands (
  analysis_id  FK→analyses,
  hand_number  INT,
  line         VARCHAR(10),      -- 'blue' | 'red' | 'preflop'
  category_label VARCHAR(100),   -- 'value_success' | 'fold_unknown' など
  position     VARCHAR(10),      -- 'BTN' | 'SB' | 'BB' | 'CO' | 'HJ' | 'UTG'
  captured_at  TIMESTAMPTZ
)
```

このテーブルで統計はほぼ全て取れる。  
`hands` テーブルに生JSONがあるため、AI解析・PDF生成も引き続き可能。

### 方針：classified_snapshotを段階的に廃止

#### フェーズ分け

| フェーズ | 内容 |
|---|---|
| 21-B1 | 新規解析でclassified_snapshotを書かない（analysis_handsのみ） |
| 21-B2 | 既存snapshotを使っている箇所を analysis_hands + hands で代替 |
| 21-B3 | analyses テーブルから classified_snapshot カラムを削除 |

#### 影響箇所の整理

| 機能 | 現状 | 変更後 |
|---|---|---|
| `classify_result/{job_id}` 表示 | snapshot から復元 | `hands` + `analysis_hands` から再構築 |
| 3D可視化 | snapshot 依存（19-B 未着手） | `analysis_hands` から集計（19-B 完了） |
| PDF生成 | snapshot のデータを使用 | `hands` + `analysis_hands` から生成 |
| セッション一覧 | 解析履歴リンク | 統計サマリー表示に変更 |
| AI解析カート | 不変（`hands.hand_json` を参照） | 変更なし |

#### ⚠️ 最大の盲点：classify_result ページの復元

現状、サーバー再起動後は `classified_snapshot` から復元している。  
これを廃止した場合、`classify_result/{job_id}` を開くたびに：

**選択肢A（推奨）:** `hands` テーブルから対象ハンドを読み込み → メモリ上でclassify → 表示  
→ 初回表示に2〜5秒かかる可能性あり。ローディング表示が必要。

**選択肢B:** `analysis_hands` テーブルのサマリーのみ表示（全ハンド詳細は省略）  
→ 大幅なUI変更が必要。AI解析カートが機能するには全ハンド詳細が必要なので不完全。

**→ 選択肢Aで進める。ただし classify_result の「全ハンドリスト」表示が重くなる可能性あり。1万手規模では pagination 必須。**

### スキーマ変更（Alembic）

```sql
-- 21-B1: analyses テーブルの classified_snapshot をNULL許容に（既にNULL許容のはず）
-- 確認のみ、変更不要の可能性が高い

-- 21-B2: analysis_hands に bb_size, pot_size を追加（統計精度向上）
ALTER TABLE analysis_hands ADD COLUMN bb_size NUMERIC;
ALTER TABLE analysis_hands ADD COLUMN pot_size_bb NUMERIC;
ALTER TABLE analysis_hands ADD COLUMN street_reached VARCHAR(10);  -- 'preflop'|'flop'|'turn'|'river'

-- 21-B3: classified_snapshot カラム削除（最終フェーズ）
ALTER TABLE analyses DROP COLUMN classified_snapshot;
ALTER TABLE analyses DROP COLUMN snapshot_encoding;
```

---

## Phase 21-C: 機能刷新

### 21-C1: 3D可視化リアルタイム化（19-B 完了）

Phase 19-B がそのまま着手可能。`analysis_hands` テーブルから直接集計する。  
SPEC.md の 19-B 仕様を参照。

**追加考慮：** `?mode=realtime` パラメータではなく、PostgreSQLモード時は常にリアルタイム集計にする。  
classified_snapshot を廃止するので、リアルタイムしかなくなる。

### 21-C2: 全ハンド統計ページ（新機能）

**URL:** `/stats`（ログイン必須）  
**概念:** セッション単位ではなく、**全蓄積ハンドの傾向を一覧で把握する**

```
┌─────────────────────────────────────────┐
│ 📊 あなたの統計                           │
│ 総ハンド数: 26,173   解析済み: 24,800     │
│                                         │
│ ── GTO分類 ────────────────────────────  │
│ 青線: 62% ██████████░░░░░░              │
│ 赤線: 24% ████░░░░░░░░░░░░              │
│ PF止: 14% ██░░░░░░░░░░░░░░              │
│                                         │
│ ── ポジション別赤線率 ──────────────────── │
│ UTG 18% / HJ 20% / CO 22%              │
│ BTN 19% / SB 31% ⚠️ / BB 28%           │
│                                         │
│ ── カテゴリ上位（改善候補）───────────────  │
│ fold_unknown    823手   → AI解析を推奨   │
│ hero_aggression 612手                   │
│                                         │
│ ── 時系列トレンド ──────────────────────  │
│ [月次グラフ: 青線率の推移]               │
└─────────────────────────────────────────┘
```

**バックエンドSQL（イメージ）:**
```sql
-- 全解析にまたがるカテゴリ集計
SELECT
  ah.line,
  ah.category_label,
  ah.position,
  COUNT(*) AS hand_count
FROM analysis_hands ah
JOIN analyses a ON a.id = ah.analysis_id
WHERE a.user_id = :uid AND a.deleted_at IS NULL
GROUP BY ah.line, ah.category_label, ah.position;
```

### 21-C3: セッション画面リデザイン（/sessions）

classified_snapshot 廃止に伴い、セッション一覧の役割が変わる。

**変更前:** 解析結果のリンク集（全ハンド詳細に飛べる）  
**変更後:** 解析履歴の記録 + サマリー表示 + 全ハンド統計へのリンク

```
[セッション一覧]
  2026-05-19  312手  青63% 赤22%  [詳細] [削除]
  2026-05-15  248手  青58% 赤28%  [詳細] [削除]
  ...
  [📊 全期間の統計を見る →]
```

### 21-C4: classify_result の pagination（⚠️ 必須）

1万手規模でclassify_resultを開く場合、全ハンドをページ内に展開するとブラウザが固まる。

**対応：**
- 青線/赤線/全ハンドそれぞれで pagination（50手/ページ程度）
- または仮想スクロール
- カート追加は pagination をまたいで機能する必要がある

---

## 見落とし・盲点まとめ

| 項目 | 内容 | 対応 |
|---|---|---|
| EC2のEBS容量 | PostgreSQLデータが増え続ける。現行8GBだと1〜2年で枯渇する可能性 | 移行時に30GBに拡張（+$1/月）|
| Docker Compose移行 | 現行の `docker run` → `docker compose up` に変更。deploy.yml も更新が必要 | GitHub Actions の deploy ステップを書き換え |
| EC2のPublic IP変更 | t3.small変更時の停止→起動でIPが変わる | GitHub Secrets の `EC2_HOST` を忘れずに更新 |
| バックアップ欠如 | RDS廃止でAWS管理の自動バックアップがなくなる | cronでdump → S3 or ローカル保存を設定 |
| analysis_handsの backfill | 既存の解析（classified_snapshot あり、analysis_handsなし）は統計に含まれない | 移行スクリプトでsnapshotを展開してanalysis_handsに投入（または再解析） |
| classify_result の重さ | 1万手規模でpage内に全展開するとブラウザが固まる | pagination 必須 |
| AI解析カート | classified_snapshot 廃止後も `hands.hand_json` から動くはず。要動作確認 | 変更不要のはずだが要テスト |
| Secrets Managerコスト | RDS削除後も $0.40/月 かかる。DATABASE_URLをgto.envに移す選択肢あり | 急がないが将来的に検討 |
| PGパスワード管理 | docker-compose.ymlのPOSTGRES_PASSWORDをどこに置くか | Secrets Managerに追加 or EC2上の.envに記載 |

---

## 発展・昇華の可能性

### 近い将来（現実的）

| アイデア | 価値 | 工数 |
|---|---|---|
| **全ハンド統計 `/stats`** | コア価値。「自分の弱点ポジション」が一目でわかる | 中 |
| **改善トレンド表示** | 月次・週次で青線率が改善しているか | 小（データはある）|
| **needs_api ハンドの一括抽出** | `fold_unknown` / `hero_aggression_won` だけをまとめてカートに入れる | 小 |
| **データエクスポート（CSV）** | `analysis_hands` をCSVで落としてExcel分析 | 小 |
| **対戦相手統計（Phase 11）** | `hands.hand_json` にある相手プレイヤーデータを集計。BTNの相手のVPIPなど | 大 |

### 中期（フリーミアム構造）

```
無料: 解析・統計・PDF・3D → ローカル保存
有料: クラウド統計履歴の長期保存・エクスポート・対戦相手DB
```

ハンドログ（生データ）はユーザーの資産。それをどう活かすかがプロダクト価値。  
Ten-Fourがやっていない「GTO分類」という切り口が差別化になる。

---

## 実装順序（推奨）

```
21-A: インフラ移行（先に完了させてコストを下げる）
  ├── A1: EC2 t3.small 変更
  ├── A2: EC2上にPostgreSQL構築（Docker Compose）
  ├── A3: データ移行（RDS → EC2）
  ├── A4: DATABASE_URL更新・動作確認
  ├── A5: RDS削除
  └── A6: バックアップcron設定

21-B: データモデル刷新
  ├── B1: 新規解析でclassified_snapshot を書かない
  ├── B2: 3D可視化を analysis_hands ベースに変更（19-B 完了）
  ├── B3: classify_result を hands + analysis_hands から再構築
  ├── B4: analysis_hands に bb_size / pot_size 追加（精度向上）
  └── B5: classified_snapshot カラム削除

21-C: 機能刷新
  ├── C1: 全ハンド統計ページ /stats
  ├── C2: セッション画面リデザイン
  └── C3: classify_result pagination
```

---

## 懸念・判断待ち事項

1. **`classify_result/{job_id}` の扱い:** 廃止？それとも「再解析して表示」に変更？  
   → AI解析カートを使いたいならページは残す必要がある

2. **既存の classified_snapshot のバックフィル:** 過去のデータを `analysis_hands` に投入するか？  
   → しないと過去の解析が統計に入らない。移行スクリプトで対応するのが正攻法

3. **Secrets Manager の POSTGRES_PASSWORD:** EC2のdocker-compose.ymlに渡す方法を決める

---

*最終更新: 2026-05-20*
