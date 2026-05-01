# デバッグ覚書 2026-05-01（Phase 20c）

PostgreSQL 移行後に発覚した一連のバグと解決パターンの記録。

---

## 1. get_hands() が全ハンド空になる

**症状:** 解析結果が全件「プリフロップのみ」、ホールカード `— —`、損益 `0bb`

**原因:** `postgres_utils.get_hands()` でJSONBカラムを `**r[1]` でトップレベルに展開していた。
`convert_hands_batch` は `item.get("hand_json", {})` でアクセスするため常に `{}` になっていた。

```python
# NG: hand_json の中身がトップレベルに展開されてしまう
d = {"hand_id": r[0], **r[1], "captured_at": ...}

# OK: Firebase と同じ形式（hand_json キー下にネスト）
d = {"hand_id": r[0], "hand_json": r[1], "captured_at": ...}
```

**教訓:** PostgreSQL の JSONB カラムを SQLAlchemy で取得すると Python dict として返る。
Firebase の `doc.to_dict()` と出力形式を合わせるには明示的にキーを指定する。

---

## 2. SQLAlchemy text() での `:param::jsonb` 構文エラー

**症状:** `update_cart`・`save_analysis`・`save_cart_snapshot` が 500 エラー

**原因:** SQLAlchemy の `text()` はコロンを named parameter の開始として解析する。
`:param::jsonb` の `::` を「パラメータ名の一部」と誤認識しSQL構文エラーになる。

```python
# NG: SQLAlchemy が :cart: とパラメータ名を誤解析する
text("UPDATE analyses SET active_cart = :cart::jsonb WHERE ...")

# OK: CAST() 形式を使う（save_hand の修正と同じパターン）
text("UPDATE analyses SET active_cart = CAST(:cart AS jsonb) WHERE ...")
```

**対象ファイル:** `scripts/postgres_utils.py` 内の全 `::jsonb` 箇所を CAST() に統一済み。

---

## 3. カート解析が「カートが空です」になる

**症状:** カートに手を入れてすぐ「解析を実行」を押すと 400「カートが空です」

**原因:** カート変更時の DB 書き込み（`syncCart`）は 600ms 遅延の非同期処理。
`startAnalyze` は DB から `get_cart()` を呼ぶため、sync 完了前に呼ぶと空が返る。

**解決:** `startAnalyze` がリクエスト body に `hand_numbers` を含めて送るよう変更。
サーバー側は body の値を優先し DB への保存はベストエフォートにした。

```js
// classify_result.js: body に hand_numbers を乗せる
const resp = await fetch(`/api/cart/${JOB_ID}/analyze`, {
  method: 'POST',
  headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
  body: JSON.stringify({hand_numbers: [...cartSet]})
});
```

```python
# cart.py: body の hand_numbers を優先、update_cart 失敗でも継続
hand_numbers = [int(n) for n in body_nums]   # body から取得
try:
    update_cart(uid, job_id, hand_numbers)    # 失敗しても hand_numbers は維持
except Exception:
    pass
```

---

## 4. Firestore 直叩きコードが PostgreSQL モードで動かない

**症状:** `/api/debug/hand-sample` が PostgreSQL モードで動作しない

**原因:** `get_db()` で Firestore クライアントを取得し `.collection()` を直叩きしていた。

```python
# NG: PostgreSQL モードでは get_db() が None を返す
db = get_db()
docs = list(db.collection("users").document(uid).collection("hands")...)

# OK: db.py のラッパーを使う
hands_data = get_hands(uid, limit=30)
```

**教訓:** PostgreSQL 移行後は `db.collection()` を直接呼ぶコードは全て `scripts/db.py` のラッパー関数経由に置き換える。

---

## 5. 拡張機能 auth race condition

**症状:** ゲーム開始直後のハンドが保存されない（`_user null` でスキップ）

**原因:** `initFirebase()` が `onAuthStateChanged` の登録だけして認証確定を待たずにリターンしていた。

```js
// NG: 登録しただけで _user の確定を待たない
onAuthStateChanged(_auth, user => { _user = user; });

// OK: Promise で _user 確定まで await する
await new Promise(resolve => {
  let resolved = false;
  onAuthStateChanged(_auth, user => {
    _user = user;
    if (!resolved) { resolved = true; resolve(); }
  });
});
```

また `_initPromise` をキャッシュして複数の `handleMessage` が同時に `initFirebase()` を呼んでも1回だけ実行されるようにした。

---

## コミット履歴（2026-05-01）

| hash | 内容 |
|---|---|
| `eb634d6` | save_hand の tableId コロンによる SQL 構文エラー修正 |
| `2f4a001` | get_hands の hand_json 展開バグ修正・debug/hand-sample を get_hands() 経由に |
| `9bca25e` | 拡張機能 auth race condition 修正・apex ドメイン追加 |
| `363bbb4` | カート「カートが空です」race condition 修正 |
| `c9e1f1f` | update_cart の ::jsonb 構文エラー修正・api_update_cart に try/except 追加 |
| `54716f8` | postgres_utils の残存 ::jsonb を CAST() に統一 |
