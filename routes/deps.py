"""
routes/deps.py - 共通依存関数
"""

from fastapi import Request


def get_uid_from_request(request: Request) -> str:
    """
    Authorization: Bearer {idToken} ヘッダーから uid を取得。
    失敗時は ValueError を送出。
    """
    from scripts.firebase_utils import verify_id_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Authorization ヘッダーがありません")
    id_token = auth_header.removeprefix("Bearer ").strip()
    decoded = verify_id_token(id_token)
    return decoded["uid"]
