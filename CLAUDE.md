# CLAUDE.md — PokerGTO 開発ガイド

> Claude Codeが会話開始時に自動で読み込む設定ファイル。
> 詳細仕様はすべて **SPEC.md** を参照。このファイルは作業ルールのみ。

---

## 作業ルール

- **実装前に SPEC.md を読む**（推測で進めない）
- **実装後に SPEC.md を更新**してからコミット（「実装だけしてSPEC更新しない」は禁止）
- 大きな変更の前に方針を一言確認する
- デバッグ用エンドポイント・ログは問題解決後に必ず削除する

---

## フェーズ状況（詳細は SPEC.md §開発フェーズ）

| フェーズ | 状態 |
|---|---|
| Phase 1〜4, 7〜10, 12〜14 | ✅ 完了 |
| **Phase 15** | ⬜ 次回（Gemini→Groq表記統一・トークン見積もり・ハンド情報増量・ソート） |
| Phase 5, 6, 11 | ⬜ 未着手 |

---

## よくあるミスと対処法

### Firestoreのソート
```python
# NG: captured_at は一部ドキュメントに欠落
order_by("captured_at")

# OK: 全件に存在する
order_by("saved_at")
```

### `get()` と None の罠
```python
# NG: value が null だと [] にならない
hand_results = hand_json.get("handResults", [])

# OK
hand_results = hand_json.get("handResults") or []
```

### chrome.runtime の罠
- `/sessions` ページのコンソールから拡張機能APIは呼べない
- 拡張機能コンソール（chrome://extensions → background）と別物

---

## デプロイ手順

```bash
# 構文チェック（必須）
python -c "
import ast, pathlib
for f in ['server.py','state.py','pipelines.py','routes/pages.py','routes/api.py','routes/cart.py','routes/legacy.py','html/pages.py']:
    p = pathlib.Path(f)
    if p.exists(): ast.parse(p.read_text(encoding='utf-8')); print(f'OK: {f}')
"

# コミット & プッシュ（Railway が main ブランチを自動デプロイ）
git add <files>
git commit -m "feat/fix/docs: 変更内容"
git push origin master:main
```

---

## effortレベル

| 場面 | レベル |
|---|---|
| バグ診断・DB設計・セキュリティ | `/effort high` |
| CSS・HTML・ドキュメント更新 | デフォルト |

---

## 会話再開テンプレート（/clear後）

```
CLAUDE.mdとSUMMARY.mdを読んでから作業を始めてください。
細部が必要な場合はSPEC.mdも読む。
今日やりたいこと：[ここに作業内容]
```
