"""
clear_firestore_testdata.py - テストデータをFirestoreから全削除するワンショットスクリプト

使用法:
  FIREBASE_SERVICE_ACCOUNT_JSON='...' python scripts/clear_firestore_testdata.py

削除対象:
  - users/{uid}/hands/*       （蓄積ハンドログ）
  - users/{uid}/analyses/*    （解析結果）
  - users/{uid}/sessions/*    （レガシーセッション）
"""

import os
import sys
import json

def delete_collection(col_ref, batch_size=100):
    docs = col_ref.limit(batch_size).stream()
    deleted = 0
    for doc in docs:
        # サブコレクションも再帰削除
        for sub in doc.reference.collections():
            delete_collection(sub, batch_size)
        doc.reference.delete()
        deleted += 1
    if deleted >= batch_size:
        return deleted + delete_collection(col_ref, batch_size)
    return deleted


def main():
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        print("ERROR: 環境変数 FIREBASE_SERVICE_ACCOUNT_JSON が未設定です", file=sys.stderr)
        sys.exit(1)

    import firebase_admin
    from firebase_admin import credentials, firestore

    sa_dict = json.loads(sa_json)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(sa_dict))
    db = firestore.client()

    users_ref = db.collection("users")
    users = list(users_ref.stream())

    if not users:
        print("Firestoreに users コレクションが見つかりません（データなし）")
        return

    print(f"対象ユーザー: {len(users)} 件")
    print()

    total = 0
    for user_doc in users:
        uid = user_doc.id
        print(f"  UID: {uid}")

        for col_name in ("hands", "analyses", "sessions", "carts"):
            col_ref = users_ref.document(uid).collection(col_name)
            n = delete_collection(col_ref)
            if n:
                print(f"    ✓ {col_name}: {n} 件削除")
            total += n

    print()
    print(f"完了: 合計 {total} ドキュメントを削除しました")


if __name__ == "__main__":
    confirm = input("Firestoreのテストデータを全削除します。よろしいですか？ [yes/N]: ").strip()
    if confirm != "yes":
        print("キャンセルしました")
        sys.exit(0)
    main()
