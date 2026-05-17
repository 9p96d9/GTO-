# PDF生成・Docker最適化によるコスト削減検討

> 作成: 2026-05-17  
> 目的: Puppeteer/Node.js依存を見直し、Docker軽量化とコスト削減が可能か検証する

---

## 結論サマリー

| 観点 | 現状 | WeasyPrint移行後 |
|---|---|---|
| Dockerイメージサイズ | ~1.5〜2GB | **~1.0GB**（500MB削減） |
| Fargate必要メモリ | 1GB（Chromiumのため） | **512MB**（半分に） |
| EC2 t2.micro（1GB）での稼働 | ❌ OOMリスク大 | **✅ 安全** |
| PDF品質 | Chromiumレンダリング | WeasyPrintレンダリング |
| 月額直接削減 | — | **~$2〜4**（Fargate費用） |
| ALB廃止との相乗効果 | — | **EC2無料枠が現実的に** |

**重要:** 直接の月額削減より「EC2 t2.micro（無料枠）での安全な稼働が可能になる」ことが最大メリット。

---

## 現状分析：なぜDockerが重いか

### PDF生成の現在のフロー

```
ユーザーが解析実行
    ↓
Python（pipelines.py）が subprocess で Node.js を呼ぶ
    ↓
node scripts/generate.js  ←── JavaScriptでHTMLを組み立て
    ↓
Puppeteer が headless Chromium を起動
    ↓
Chromium が HTML → PDF をレンダリング
    ↓
PDFファイル保存
```

呼び出し箇所（pipelines.py:88〜91）:
```python
subprocess.run(
    ["node", str(SCRIPTS / "generate.js"), str(OUTPUT_DIR), str(json_path)],
    ...
)
```

### Dockerfileに入っているもの（現状）

```dockerfile
# ① Chromiumが必要とするシステムライブラリ（14個）
libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2
libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2
libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libxext6

# ② Node.js 20（nodesource経由でインストール）
→ 約120MB

# ③ puppeteer が npm install 時に Chromium を自動ダウンロード
→ 約300MB

# ④ 日本語フォント
fonts-ipafont-gothic fonts-ipafont-mincho
```

**合計増加分: 約500〜600MB**
これがなければ Python slim ベースで ~700MB 台のイメージになれる。

---

## なぜコストに直結するか

### ① Fargateのメモリ要件

Chromium は起動時に約200〜300MB のメモリを消費する。  
このため Fargate タスクを **1GB** に設定せざるを得ない。

```
現状:  0.5vCPU + 1024MB = 月約$7〜8（Tokyo Fargate料金）
変更後: 0.5vCPU + 512MB  = 月約$5〜6（推定）
差額: 月$2〜3の削減
```

### ② EC2無料枠が使えない

EC2 t2.micro は RAM 1GB しかない。  
Chromiumが常駐するとOSや他プロセスの余裕がなく、PDF生成時にOOMになるリスクが高い。

→ **Node.js・Chromiumを排除すれば、t2.micro（無料枠対象）で安全に稼働できる。**  
→ ALB廃止 + EC2無料枠 = 実質 **月コスト $0〜1** になる構成が現実的になる。

### ③ ECRストレージコスト

```
現状: イメージ約1.8GB → 500MBの無料枠を超えて約$0.13/月
変更後: イメージ約1.0GB → 約$0.05/月（わずかだが半減）
```

---

## 改善提案：WeasyPrint（Python製PDF生成）への移行

### WeasyPrintとは

- Pythonライブラリ。HTML + CSS を PDF に変換する
- Chromiumではなく **Cairo + Pango**（すでにDockerfileに入っている）を使う
- Node.js / npm / Chromiumダウンロード が不要になる

### 重要な発見：必要なシステムライブラリはすでに入っている

```dockerfile
# 現在のDockerfileに既にある（Chromiumのために入れた）
libpango-1.0-0  ← WeasyPrintが使う
libcairo2       ← WeasyPrintが使う
```

WeasyPrint が必要なのは `libpango` と `libcairo` だけ。  
この2つはすでにインストール済みなので、**apt の追加はほぼ不要**。

### 変更後のDockerfile（イメージ）

```dockerfile
FROM python:3.11-slim

# WeasyPrintに必要なライブラリ（Chromiumの14個→4個に削減）
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libcairo2 \
    libgdk-pixbuf2.0-0 libffi-dev \
    fonts-ipafont-gothic fonts-ipafont-mincho \
    fonts-noto-color-emoji \       ← 絵文字レンダリング用
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node.js インストール → 削除
# npm ci → 削除

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# WeasyPrintを requirements.txt に追加するだけ

# 以降は変更なし
```

### requirements.txtへの追加

```
weasyprint   ← これだけ追加
```

### 移行が必要なコード

| ファイル | 変更内容 |
|---|---|
| `scripts/generate.js` | Python版 `scripts/generate.py` に書き直す |
| `scripts/generate_noapilist.js` | 同上 |
| `pipelines.py` | `["node", "generate.js"]` → `[sys.executable, "generate.py"]` |
| `Dockerfile` | Node.js削除、WeasyPrint用ライブラリに変更 |
| `requirements.txt` | `weasyprint` 追加 |
| `package.json / package-lock.json` | 削除可能 |

---

## WeasyPrint移行のリスク評価

### ① 絵文字の扱い ⚠️ 要対応

現在 generate.js で使っている絵文字:
```
🔵 🔴 ✅ ❌ ⚠️ 🎲 💰 📅 🃏 🎯
```

WeasyPrint は `fonts-noto-color-emoji` を入れれば絵文字を処理できるが、  
カラー絵文字は環境によっては白黒になることがある。

**対策案:**
```
絵文字 → テキスト代替
🔵 → [青]
🔴 → [赤]
✅ → ○
❌ → ×
⚠️ → △
```
PDFの読みやすさは維持できる。

### ② FlexboxのCSS対応 ⚠️ 要確認

現在の CSS:
```css
.opp-profile-body .stats { display: flex; gap: 10mm; }
```
WeasyPrint 60.0+ でflexboxに対応済み（2024年以降のバージョン）。  
pip でインストールすれば最新版が入るため、対応済みの可能性が高い。  
万が一崩れる場合は `display: inline-block` に置き換えで対処可能。

### ③ PDF品質の差異 ✅ 許容範囲

| 項目 | Puppeteer（Chrome） | WeasyPrint |
|---|---|---|
| テーブルレイアウト | 完全対応 | ほぼ同等 |
| 日本語フォント | IPAフォント使用 | 同じフォント使用可能 |
| ページ分割 | 正確 | 良好（`page-break` CSS対応） |
| 全体的な品質 | 高い | 若干異なるが実用上問題なし |

---

## コスト削減シミュレーション（WeasyPrint + ALB廃止を組み合わせた場合）

```
現状（Fargate 1GB + ALB）:
  ALB:    ~$7/月
  Fargate: ~$7/月（1GB）
  RDS:     $0（無料枠）
  SM/他:   ~$0.5/月
  合計:    ~$14.5/月

WeasyPrint移行後（Fargate 512MB + ALB）:
  ALB:    ~$7/月
  Fargate: ~$5/月（512MB）
  合計:    ~$12.5/月（月$2削減）

WeasyPrint + ALB廃止（EC2 t2.micro 無料枠）:
  EC2 t2.micro: $0（無料枠2027/04/24まで）
  RDS:          $0（無料枠）
  SM:           ~$0.4/月
  合計:         ~$0.4/月 ← ほぼ無料
```

**WeasyPrint移行の最大のメリット: ALB廃止後に EC2 t2.micro（無料枠）での安全稼働が可能になること。**

---

## 移行の難易度と工数

### 工数見積もり

| タスク | 難易度 | 工数 |
|---|---|---|
| generate.js の Python 移植 | 中 | 2〜3時間 |
| generate_noapilist.js の移植 | 中 | 1〜2時間 |
| Dockerfileの変更 | 低 | 30分 |
| テスト（PDF品質確認） | 中 | 1時間 |
| 合計 | | **4〜6時間** |

### 推奨タイミング

```
今すぐ: ❌ 発表前はリスク不要
発表後・安定したら: ✅ ALB廃止と同時に実施
→ WeasyPrint化 + ALB廃止 + EC2移行をセットで実施すると効率的
```

---

## まとめ：やる価値はあるか？

| 評価軸 | 評価 |
|---|---|
| 直接の月額削減 | $2〜4（小さい） |
| EC2無料枠活用への道 | ✅ 大きい（月$14→$0.4になれる） |
| 実装リスク | 中（PDF品質の目視確認が必要） |
| 発表前に実施すべきか | ❌ 後でよい |
| 長期的な価値 | ✅ 高い（Node.jsメンテ不要・イメージ軽量化） |

**結論: 発表後のALB廃止タイミングで一緒に実施する。今は手をつけない。**

---

*最終更新: 2026-05-17*
