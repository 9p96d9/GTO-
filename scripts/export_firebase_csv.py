"""
export_firebase_csv.py - Firestore全データをCSVエクスポート

【実行方法】
  cd GTO-
  set FIREBASE_SERVICE_ACCOUNT_JSON=<サービスアカウントJSONの中身>
  python scripts/export_firebase_csv.py

【出力ファイル（output/firebase_export/ に保存）】
  users.csv            - ユーザー一覧（uid・email・作成日時）
  hands.csv            - 全ハンド一覧（uid別）
  analyses.csv         - 解析サマリー（uid・カテゴリ集計）
  analysis_hands.csv   - 解析内ハンド詳細（line・category・position別）
"""

import os
import sys
import csv
import gzip
import base64
import json
from pathlib import Path
from datetime import datetime, timezone

# プロジェクトルートをパスに追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

OUTPUT_DIR = ROOT / "output" / "firebase_export"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def init_firebase():
    import firebase_admin
    from firebase_admin import credentials, firestore, auth

    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        # ファイルパスとして試みる
        sa_path = ROOT / "firebase_service_account.json"
        if sa_path.exists():
            sa_json = sa_path.read_text(encoding="utf-8")
        else:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON 環境変数が未設定です。\n"
                "または GTO-/firebase_service_account.json を置いてください。"
            )

    sa_dict = json.loads(sa_json)
    cred = credentials.Certificate(sa_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    db = firestore.client()
    return db, auth


def ts_to_iso(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def decompress_snapshot(snapshot: str, encoding: str) -> list:
    if not snapshot:
        return []
    try:
        if encoding == "gzip_b64":
            data = json.loads(gzip.decompress(base64.b64decode(snapshot)))
        else:
            data = json.loads(snapshot)
        return data.get("hands", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"    ⚠ snapshot展開失敗: {e}")
        return []


def export_users(db, auth_client) -> list[str]:
    """Firebase Authから全ユーザーを取得 → users.csv"""
    print("▶ ユーザー一覧を取得中...")
    rows = []
    uids = []

    page = auth_client.list_users()
    while page:
        for user in page.users:
            rows.append({
                "uid":          user.uid,
                "email":        user.email or "",
                "display_name": user.display_name or "",
                "created_at":   datetime.fromtimestamp(
                    user.user_metadata.creation_timestamp / 1000, tz=timezone.utc
                ).isoformat() if user.user_metadata.creation_timestamp else "",
                "last_sign_in": datetime.fromtimestamp(
                    user.user_metadata.last_sign_in_timestamp / 1000, tz=timezone.utc
                ).isoformat() if user.user_metadata.last_sign_in_timestamp else "",
            })
            uids.append(user.uid)
        page = page.get_next_page()

    _write_csv("users.csv", rows, ["uid", "email", "display_name", "created_at", "last_sign_in"])
    print(f"  → {len(rows)} ユーザー")
    return uids


def _parse_hand_json(hj: dict) -> dict:
    """hand_json（fastFoldTableState）から分析に使えるフィールドを抽出"""
    hand_results  = hj.get("handResults") or []
    my_seat_index = hj.get("mySeatIndex", -1)
    community     = hj.get("communityCards") or []
    action_hist   = hj.get("actionHistory") or []

    # ヒーロー情報
    hero_result = next(
        (r for r in hand_results if r.get("seatIndex") == my_seat_index), {}
    )
    hero_position = hero_result.get("position", "")
    hero_name     = hero_result.get("playerName", "")
    hero_profit   = float(hero_result.get("profit", 0.0))

    # プレイヤー数・ポット
    num_players = len(hand_results)

    # ボードの深さ（フロップ/ターン/リバー到達判定）
    board_cards = len([c for c in community if c])
    went_to_flop  = board_cards >= 3
    went_to_turn  = board_cards >= 4
    went_to_river = board_cards >= 5

    # 3BETポット判定（プリフロップのRaiseが2回以上）
    raise_count = sum(
        1 for line in action_hist
        if isinstance(line, str) and " RAISE " in line.upper()
    )
    is_3bet_pot = raise_count >= 2

    # ウィナー判定
    hero_is_winner = hero_result.get("isWinner", False)

    return {
        "hero_name":     hero_name,
        "hero_position": hero_position,
        "hero_profit_bb":hero_profit,
        "num_players":   num_players,
        "went_to_flop":  int(went_to_flop),
        "went_to_turn":  int(went_to_turn),
        "went_to_river": int(went_to_river),
        "is_3bet_pot":   int(is_3bet_pot),
        "hero_won":      int(hero_is_winner),
    }


def export_hands(db, uids: list[str]):
    """全ユーザーのhands → hands.csv"""
    print("▶ ハンドデータを取得中...")
    rows = []

    for uid in uids:
        hands_ref = (
            db.collection("users").document(uid)
              .collection("hands")
              .order_by("saved_at")
        )
        docs = list(hands_ref.stream())
        print(f"  uid={uid[:8]}... {len(docs)} hands")

        for doc in docs:
            d  = doc.to_dict()
            hj = d.get("hand_json") or {}
            parsed = _parse_hand_json(hj)

            rows.append({
                "uid":         uid,
                "hand_id":     doc.id,
                "saved_at":    ts_to_iso(d.get("saved_at")),
                "captured_at": d.get("captured_at", ""),
                "table_id":    hj.get("tableId", ""),
                **parsed,
            })

    _write_csv("hands.csv", rows, [
        "uid", "hand_id", "saved_at", "captured_at", "table_id",
        "hero_name", "hero_position", "hero_profit_bb", "num_players",
        "went_to_flop", "went_to_turn", "went_to_river",
        "is_3bet_pot", "hero_won",
    ])
    print(f"  → 合計 {len(rows)} ハンド")


def export_analyses(db, uids: list[str]):
    """全ユーザーのanalyses → analyses.csv + analysis_hands.csv"""
    print("▶ 解析データを取得中...")
    analysis_rows = []
    hand_rows = []

    for uid in uids:
        analyses_ref = (
            db.collection("users").document(uid)
              .collection("analyses")
              .order_by("created_at")
        )
        docs = list(analyses_ref.stream())
        print(f"  uid={uid[:8]}... {len(docs)} analyses")

        for doc in docs:
            d = doc.to_dict()
            job_id = doc.id
            categories = d.get("categories", {})

            analysis_rows.append({
                "uid":        uid,
                "job_id":     job_id,
                "created_at": ts_to_iso(d.get("created_at")),
                "hand_count": d.get("hand_count", 0),
                "blue_count": d.get("blue_count", 0),
                "red_count":  d.get("red_count", 0),
                "pf_count":   d.get("pf_count", 0),
                "blue_rate":  round(d.get("blue_count", 0) / max(d.get("hand_count", 1), 1) * 100, 1),
                "red_rate":   round(d.get("red_count", 0) / max(d.get("hand_count", 1), 1) * 100, 1),
                "categories": json.dumps(categories, ensure_ascii=False),
                "deleted_at": ts_to_iso(d.get("deleted_at")),
            })

            # classified_snapshot からハンド詳細を展開
            snapshot  = d.get("classified_snapshot", "")
            encoding  = d.get("snapshot_encoding", "")
            hands     = decompress_snapshot(snapshot, encoding)

            for hand in hands:
                clf = hand.get("bluered_classification") or {}
                hand_rows.append({
                    "uid":            uid,
                    "job_id":         job_id,
                    "analysis_date":  ts_to_iso(d.get("created_at")),
                    "hand_number":    hand.get("hand_number", ""),
                    "hero_position":  hand.get("hero_position", ""),
                    "hand_datetime":  hand.get("datetime", ""),
                    "line":           clf.get("line", ""),
                    "category_label": clf.get("category_label", ""),
                    "sub_category":   clf.get("sub_category", ""),
                })

    _write_csv("analyses.csv", analysis_rows, [
        "uid", "job_id", "created_at", "hand_count",
        "blue_count", "red_count", "pf_count",
        "blue_rate", "red_rate", "categories", "deleted_at",
    ])
    _write_csv("analysis_hands.csv", hand_rows, [
        "uid", "job_id", "analysis_date",
        "hand_number", "hero_position", "hand_datetime",
        "line", "category_label", "sub_category",
    ])
    print(f"  → 解析 {len(analysis_rows)} 件 / ハンド詳細 {len(hand_rows)} 件")


def _write_csv(filename: str, rows: list[dict], fieldnames: list[str]):
    path = OUTPUT_DIR / filename
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  保存: {path}")


def main():
    print("=== Firebase → CSV エクスポート ===\n")
    db, auth_client = init_firebase()

    uids = export_users(db, auth_client)
    export_hands(db, uids)
    export_analyses(db, uids)

    print(f"\n✅ 完了。出力先: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
