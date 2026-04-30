# AWS・PostgreSQL 学習メモ（2026-04-30）

今日の開発セッションで判明したこと・決定したことのまとめ。
学習と今後の方針決定に使う。

---

## 1. PostgreSQLにできてFirebaseにできないこと

### なぜFirebaseではダメなのか

Firestore（Firebase）の根本的な制約：
- コレクションをまたぐ JOIN が不可能
- AVG・STDDEV・RANK() などの集計関数がない
- 複数の条件を組み合わせた期間比較クエリが実質不可能
- 全ユーザーデータを集計するには「全件取得してPythonで計算」しかない

### SQLで解決できること（具体例）

```sql
-- ① 全ユーザーblue/red率ランキング（RANK()ウィンドウ関数）
-- Firestoreでは全ユーザーの全解析を読み込んでPythonで計算する必要がある
SELECT
    u.email,
    ROUND(SUM(a.red_count)::numeric / NULLIF(SUM(a.hand_count),0) * 100, 1) AS red_rate,
    RANK() OVER (ORDER BY SUM(a.red_count)::float / NULLIF(SUM(a.hand_count),0) DESC) AS red_rank
FROM users u
JOIN analyses a ON a.user_id = u.id
GROUP BY u.id, u.email;

-- ② 先週比赤線率悪化ユーザー（CTE + 期間比較）
-- Firestoreでは「先週」「今週」を別クエリで取ってPythonで差分計算するしかない
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

### 3D可視化リアルタイム化が「すぐできない」理由

`week × position × category` の3軸GROUP BYをやりたいが：

| データ | 保存場所 | 取得可否 |
|---|---|---|
| week | `hands.saved_at` | ✅ |
| position | `hands.hand_json` JSONB | ✅ |
| category | `analyses.categories` JSONB | ❌ **カウントしか入っていない** |

`analyses.categories` は `{"SB_3BET": 5}` 形式で「どのhandがどのカテゴリか」の情報がない。
→ `analysis_hands` テーブルを新設してhand単位でcategoryを持つ必要があった。

---

## 2. 今日実装したもの

### analysis_hands テーブル（Alembic 0002）

```sql
CREATE TABLE analysis_hands (
    id           SERIAL PRIMARY KEY,
    analysis_id  INT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    hand_number  INT NOT NULL,
    line         VARCHAR(10) NOT NULL,      -- 'blue' | 'red' | 'preflop'
    category_label VARCHAR(100) NOT NULL,
    position     VARCHAR(10),               -- 'BTN' | 'SB' | 'BB' etc.
    captured_at  TIMESTAMPTZ                -- 週集計に使用
);
CREATE INDEX ix_analysis_hands_analysis ON analysis_hands(analysis_id);
CREATE INDEX ix_analysis_hands_3d ON analysis_hands(line, position, captured_at);
```

**ポイント：** `save_analysis()` に `RETURNING id` を追加してanalysis_idを取得し、
解析実行ごとにDELETE→INSERT（再解析対応）。

### KPIアナリティクスダッシュボード

- URL: `/admin/analytics`
- `get_admin_analytics()` → postgres_utils.py
- `firebase_utils.py` にスタブ追加（`firebase_mode: true` を返す）
- `db.py` のエクスポートに追加
- `admin_analytics.html` 新規作成

---

## 3. AWSデプロイフロー（今日判明）

### 仕組み

```
git push origin main
  ↓ .github/workflows/deploy.yml が起動
  ↓ Docker build（1〜2分）
  ↓ ECR push（イメージをAWSに送信）
  ↓ ECSタスク定義更新
  ↓ ECSサービス再起動（コンテナ入れ替え：5〜10分）
```

### 今日適用した改善

**変更前の問題：**
- `wait-for-service-stability: true` でActionsが10〜20分ブロック
- 連続pushで複数デプロイが競合し、ECSが「安定」に到達できなかった

**変更後（deploy.yml）：**
```yaml
concurrency:
  group: production
  cancel-in-progress: true   # 古いdeployをキャンセル

wait-for-service-stability: false  # Actionsは即完了、ECSは裏で継続
```

**トレードオフ：** ActionsのUIでは成功・失敗が分からなくなる。
確認はAWSコンソール → ECS → サービス → デプロイタブで行う。

### ALBヘルスチェック高速化（未実施・オプション）

EC2 → ターゲットグループ → ヘルスチェック編集：
- 間隔: 30秒 → 10秒
- 正常しきい値: 5回 → 2回
→ 新タスク健全判定が150秒 → 20秒に短縮、デプロイが3〜4分に

---

## 4. AWSコスト分析（2026-04-30時点）

### 4月の実費

| サービス | 月額（実費） | 備考 |
|---|---|---|
| ALB | $3.16 | 無料枠対象外 |
| ECS Fargate | $1.76 | 無料枠対象外（4/28〜の2日分） |
| VPC | $1.35 | |
| RDS t4g.micro | $1.31 | |
| Secrets Manager | $0.07 | |

**フル1ヶ月換算：約$15〜20/月**

### クレジット残高

- 残高：**$112.29**
- 月$15〜20で計算すると：**約6〜8ヶ月**（2026年10月〜12月頃まで）

### コスト削減の選択肢

| 構成 | 月額目安 | 手間 |
|---|---|---|
| 現状（Fargate + ALB） | ~$15〜20 | 最小 |
| EC2 + ALB | ~$25（無料枠後） | OS管理が必要 |
| EC2 + Elastic IP（ALBなし） | ~$8〜10 | デプロイスクリプト変更 |
| App Runner | ~$1〜5 | Puppeteer動作確認が必要 |

**今の結論：** クレジットがある間は現状維持。残高$30〜50になったら再検討。

### 攻撃への対策

| 攻撃の種類 | リスク | 対策 |
|---|---|---|
| 大量リクエスト（DDoS） | ⚠️ 中程度 | FastAPIレート制限 / AWS WAF |
| 大量ダウンロード | ⚠️ 中程度 | FastAPIレート制限 |
| ネットワーク層DDoS | ✅ 対策済み | AWS Shield Standard（無料・自動適用） |

**予算アラート設定済み（2026-04-30）：$30超で9p96d9@gmail.com・69pdp69@gmail.comに通知**

#### FastAPIレート制限（`slowapi`）

`requirements.txt`に`slowapi`を追加してIPごとのリクエスト数を制限する。無料。

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)

@app.get("/api/hands/analyze")
@limiter.limit("10/minute")  # 解析APIは厳しめ
async def analyze(...): ...
```

**デメリット：**
- 制限値を厳しくしすぎると正規ユーザーもブロックされる
- カウンターはタスクのメモリ内に保持 → 将来タスクを複数台に増やすと制限が機能しなくなる（Redisが必要）
- ALB経由のためIPは`X-Forwarded-For`ヘッダーから取得する設定が必要

**現在のFargate構成（1タスク・少数ユーザー）なら問題なし。**

#### AWS WAF（知識用・今は不採用）

ALBの前段に置くマネージドファイアウォール。

- **料金：** $5/月（固定）+ $1/100万リクエスト
- **できること：** IPレート制限・地域ブロック・SQLインジェクション検知・ボット対策
- **メリット：** アプリコードに届く前にブロックするため確実
- **デメリット：** 月$5の固定費。小規模プロジェクトには割高
- **採用タイミング：** 有料サービス化して攻撃リスクが上がったとき

---

## 5. Railway vs AWS

| 観点 | Railway | AWS |
|---|---|---|
| コスト | $5〜20/月 | $15〜20/月（クレジット消化中） |
| セットアップ | ほぼゼロ | VPC・IAM・ALB等の設定が必要 |
| デプロイ | git push → 即完了 | git push → 2〜20分 |
| PostgreSQL | 提供あり | RDS（より高機能） |
| スケール上限 | 中規模まで | 実質無制限 |
| コンプライアンス | 非対応 | SOC2・HIPAA等対応 |
| 学習価値 | 低い | **高い（業界標準）** |

**AWSに移行した本当の理由：**
- PostgreSQL・RDSの学習（Phase 19の目的に明記）
- クレジット$112がある今が一番安く学べるタイミング
- 将来の有料サービス化・スケールアップに備える

---

## 6. 今後の方針

### 短期（〜1ヶ月）

- [ ] `alembic upgrade head` をRDSに対して実行（analysis_handsテーブル作成）
- [ ] USE_POSTGRES=true に切り替えて実際にデータを蓄積
- [ ] `/admin/analytics` でKPIが動くことを確認
- [ ] 拡張機能のSERVER_URLをAWS ALBに更新・再読み込み

### 中期（〜6ヶ月）

- [ ] analysis_handsにデータが溜まったら19-B（3D可視化リアルタイム化）着手
- [ ] ALBヘルスチェック高速化（デプロイ時間短縮）
- [ ] クレジット残高を月次で確認

### 長期（クレジット切れ前）

- [ ] 有料サービスとして成立しているか評価
- [ ] 成立していれば費用は売上でカバー
- [ ] 成立していなければ App Runner or EC2 に移行してコスト削減

---

## 7. 今日わかった用語・概念

| 用語 | 説明 |
|---|---|
| **ELB** | Elastic Load Balancingの総称（ALB・NLB・CLBを含む） |
| **ALB** | Application Load Balancer。HTTP/HTTPSを理解するレイヤー7 |
| **Fargate** | サーバー管理不要のコンテナ実行環境。無料枠なし |
| **ECS** | コンテナオーケストレーション。Fargateで動かす |
| **RDS** | マネージドDB。t4g.microは12ヶ月無料枠あり |
| **Elastic IP（EIP）** | 固定IPアドレス。停止中インスタンスに割り当てると課金 |
| **App Runner** | ECS+ALBをまとめたマネージドサービス。アイドル時安い |
| **wait-for-service-stability** | ECSデプロイ完了をActionsが待つかどうか |
| **concurrency（GitHub Actions）** | 同一グループの古いrunを自動キャンセルする設定 |
| **RANK() OVER()** | SQLウィンドウ関数。全行を保持しながら順位を付ける |
| **CTE（WITH句）** | Common Table Expression。複雑なクエリを分割して書く |
| **RETURNING id** | INSERTした行のIDを即座に取得するPostgreSQL構文 |
