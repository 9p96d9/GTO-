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

## GTO設計思想レビュー — classify / Groq の位置取り見直し

*2026-05-20 追記・全面改訂*

> 参照: [GTO Wizard — MDF & Alpha](https://blog.gtowizard.com/mdf-alpha/) /
> [Mathematical Misconceptions in Poker](https://blog.gtowizard.com/mathematical-misconceptions-in-poker/)

---

### 1. GTOの本質

GTOはハンド単位の「正解/不正解」ではない。  
**ナッシュ均衡戦略 = どんな相手にも搾取されないレンジ構成と行動頻度の組み合わせ**。

- 均衡戦略をとれば、相手のどんな戦略に対してもEVが最大化される
- **相手がGTOから逸脱したとき初めて搾取が生まれる**（逸脱がなければ搾取できない）
- 「このハンドは勝ったか負けたか」は評価軸にならない。1手のサンプルは統計的に無意味
- 正しい問いは**「このスポットで自分の行動頻度は均衡しているか？」**

---

### 2. GTO評価の数学的基点

ベット額とポットサイズだけから3つの均衡指標が導ける。

```
α（Alpha） = Bet / (Pot + Bet)
```

| 指標 | 計算式 | 視点 | 問い |
|---|---|---|---|
| **α（必要成功率）** | `Bet / (Pot + Bet)` | ブラフアグレッサー | 「ブラフが損益分岐するために相手は何%フォールドすべきか」 |
| **MDF** | `1 - α` | ディフェンダー | 「相手のブラフを無収益にするために最低何%コールすべきか」 |
| **バリューターゲット** | `α` | バリューアグレッサー | 「相手のコールレンジに自分が勝てるハンドが何%必要か」 |

αとバリューターゲットは同じ式だが**評価する対象が異なる**。  
MDFはαの補数。ベットサイズが全てを同時に決定する。

```
例: ポット100bb / ベット50bb
  α   = 50 / 150 = 33%  → ブラフは相手が33%以上フォールドしないと赤字
  MDF = 67%              → Heroがフォールドスポットでは67%以上コールしないと過剰フォールド
```

---

### 3. 数学指標の重要な限界（設計に直結）

**ここが設計のキモ。外部調査で確認した内容。**

#### ① MDF/αは「ブラフのエクイティがゼロ」を前提とする

現実では、フロップ・ターンのブラフはドローエクイティを持つ。  
→ **リバー以外での単純なMDF適用は不正確**。フロップなら守る側はMDFより多くフォールドしてよい。

#### ② ポジションで最適防御頻度が変わる

| ポジション | MDFとの関係 | 理由 |
|---|---|---|
| IP（ポジションあり） | MDF付近 | 将来ストリートでエクイティを実現しやすい |
| OOP（ポジションなし） | MDFより多くフォールド | 将来ストリートで不利。エクイティが実現しにくい |

→ **「MDF=67%だからコール」は自動的な正解にならない**。ポジションで調整が必要。

#### ③ 相手がブラフを十分にしていない場合はMDFを無視してよい

相手がバリュー偏重なら、MDF守備は損。過剰にフォールドして正解になる。  
→ **相手のテンデンシー（傾向）が数学より優先されることがある**。

#### ④ ブラフ:バリュー比率

バランスのとれたベッティングレンジは、ベットサイズによって最適比率が変わる。

```
ポットサイズベット → 約 2:1（バリュー:ブラフ）≒ ブラフ33%
ハーフポットベット → 約 3:1（バリュー:ブラフ）≒ ブラフ25%
```

→ **自分がどのスポットで何%ブラフしているかの集計が、GTO乖離の診断になる**。

---

### 4. これを受けた各レイヤーの正しい役割

```
┌─────────────────────────────────────────────────────────┐
│  classify.py          蓄積・記録レイヤー                  │
│  役割: ハンドをカテゴリに分類して analysis_hands に積む  │
│  評価: しない。「何が起きたか」だけを記録する            │
│  → 大量のハンドが溜まってから /stats で傾向を炙り出す    │
└──────────────────────┬──────────────────────────────────┘
                       │ 大量データが溜まる
                       ▼
┌─────────────────────────────────────────────────────────┐
│  /stats ページ        集計・診断レイヤー                  │
│  役割: スポット別の行動頻度をGTO均衡と比較する           │
│  例: 「BBでリバーベットに対するフォールド率: 72%         │
│       → MDF(IP調整後)からみると過剰フォールドの疑い」    │
│  ここで初めて「傾向」が見える                            │
└──────────────────────┬──────────────────────────────────┘
                       │ ユーザーが気になった手をカートへ
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Python（build_hand_block前）  数値計算レイヤー           │
│  役割: ハンドのbet/potからα・MDF・ブラフ:バリュー比を     │
│        決定論的に計算してAIに渡す                        │
│  新規ライブラリ不要・Docker重量ゼロ増・純算術のみ        │
│  注意: ストリート（リバー/ターン/フロップ）とポジション   │
│        をタグとしてAIに渡す（限界の補正はAIに委ねる）    │
└──────────────────────┬──────────────────────────────────┘
                       │ 計算済みの数値 + ハンド文脈
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Groq / Gemini        定性解釈レイヤー                   │
│  役割: スポットのレンジ構成・ポジション・相手テンデンシー │
│        を考慮した上でMDF/αの意味を解釈する              │
│  評価軸: ハンドの勝敗ではなく「レンジ均衡との整合性」    │
│  特に: ブラフ:バリュー比・相手の逸脱・搾取戦略         │
└─────────────────────────────────────────────────────────┘
```

---

### 5. classify.py の修正方針

| 項目 | 現状 | 修正後 |
|---|---|---|
| `needs_api` UIバッジ | `★ 要AI N手` を表示 | 削除（フィールドは維持） |
| 「AI解析推奨」の考え方 | 「手が見えないからAIが必要」 | 廃止。カートはユーザーが任意選択 |
| フォールドスポットの扱い | AIが必要な特別なケース | 均衡分析の一材料（数学は単純） |

`needs_api` フィールド自体はプロンプト構築時に「フォールドスポットか否か」の文脈として使えるので削除しない。

---

### 6. analyze2.py プロンプト設計の修正方針

#### 削除

```
gto_eval: ✅良好 / ⚠️改善 / ❌ミス / 🎲クーラー
```
→ 個別ハンドへの「判定」は結果論。GTOの評価軸ではない。廃止。

```
数値禁止ルール（現 SYSTEM_PROMPT_DETAIL の「数値禁止」）
```
→ math_check には数値（bb・%）が必要。撤廃。

#### 維持・強化

```
hand_reading  → レンジ思考の核心。維持
rep           → Heroの表現レンジ。維持
kaizen        → 代替ライン。維持（ただし「ミス」のラベルなしで）
opp_gto_diff  → opp_exploit に強化（具体的な搾取戦略まで踏み込む）
```

#### 新規追加

```
spot_range:  このアクションシーケンス・ポジション・ボードで
             Heroが均衡上持ちうるレンジの概要

math_context: （Pythonが事前計算して渡す）
              スポットタイプ・ストリート・ポジション・α・MDF
              ※ AIは計算せず、この数値を解釈する

balance_note: math_contextの数値をポジション・ストリート・
              相手テンデンシーで補正した上での均衡コメント
              （例: 「OOPかつフロップなのでMDFより多めのフォールドが均衡に近い」）
```

#### 最終的なフィールド構成（案）

```
- spot_range    均衡レンジ概要
- balance_note  math_contextの文脈補正コメント
- hand_reading  各ストリートでの相手レンジ変化
- opp_exploit   相手逸脱と搾取戦略
- rep           Heroの表現レンジ
- kaizen        代替ライン（ある場合のみ）
```

---

### 7. /stats ページで見せるべき指標（GTO診断）

個別ハンドでなく**集計された行動頻度**が診断になる。

```
優先度 高:
  - ポジション × ストリート別フォールド頻度（vs MDF基準値）
  - ベットスポットでのブラフ:バリュー比（vs ベットサイズ別均衡比率）
  - CBet（コンティニュエーションベット）頻度 by ポジション

優先度 中:
  - 3betポット vs シングルレイズポット別の行動パターン差異
  - レイズ後フォールドされた割合（ブラフ成功率の代替指標）
  - ショーダウン勝率 vs ノーショーダウン勝率の比較

将来（analysis_hands にフィールド追加後）:
  - bb_size・pot_size_bb の蓄積から正確なαを計算可能に
```

---

### 8. 実装優先度

```
1. UIのneeds_apiバッジ削除（html_pages/pages.py・generate_noapilist.py）← 小変更
2. analyze2.py のgto_eval削除・balance_note追加・数値解禁  ← プロンプト改訂
3. build_hand_block にmath_context（α/MDF/spot_type）を事前計算して注入
4. /stats ページ（Phase 21-C と統合）
5. analysis_hands への bb_size / pot_size_bb 追加（Phase 21-B と統合）
```

---

*最終更新: 2026-05-20*
