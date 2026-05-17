"""
scripts/export_powerbi.py - Firestore の解析データを Power BI 用 CSV にエクスポート

使用法:
  1. FIREBASE_SERVICE_ACCOUNT_JSON 環境変数を設定する
     PowerShell:
       $env:FIREBASE_SERVICE_ACCOUNT_JSON = (Get-Content path\to\sa.json -Raw)

  2. 実行 (GTO- フォルダのルートから):
       python scripts/export_powerbi.py --uid <あなたのFirebase UID> --out hands.csv

  Firebase UID の確認場所:
    Firebase Console → Authentication → Users → UID 列
"""

import os
import sys
import json
import csv
import gzip
import base64
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIELDNAMES = [
    "job_id", "saved_at", "source_file",
    "hand_id", "hand_number", "datetime", "game", "bb_size",
    "hero_position", "hero_card_1", "hero_card_2",
    "hero_result_bb", "hero_won",
    "is_3bet_pot", "went_to_showdown",
    "last_street", "line", "category", "category_label",
    "flop_board", "flop_pot_bb",
    "turn_card", "turn_pot_bb",
    "river_card", "river_pot_bb",
    "player_count", "rake_bb",
]


def decompress_snapshot(snapshot_str: str, encoding: str) -> dict:
    if encoding == "gzip_b64":
        raw = gzip.decompress(base64.b64decode(snapshot_str)).decode("utf-8")
    else:
        raw = snapshot_str
    return json.loads(raw)


def hand_to_row(hand: dict, job_id: str, source_file: str, saved_at: str) -> dict:
    streets  = hand.get("streets", {})
    clf      = hand.get("bluered_classification", {})
    result   = hand.get("result", {})
    hero_cards = hand.get("hero_cards", [])

    flop  = streets.get("flop")  or {}
    turn  = streets.get("turn")  or {}
    river = streets.get("river") or {}

    flop_board  = " ".join(flop.get("board", []))
    turn_card   = (turn.get("board")  or [""])[0]
    river_card  = (river.get("board") or [""])[0]

    winners    = {w["name"] for w in result.get("winners", [])}
    hero_name  = next((p["name"] for p in hand.get("players", []) if p.get("is_hero")), "")
    hero_won   = (hero_name in winners) if hero_name else False

    return {
        "job_id":           job_id,
        "saved_at":         saved_at,
        "source_file":      source_file,
        "hand_id":          hand.get("hand_id", ""),
        "hand_number":      hand.get("hand_number", ""),
        "datetime":         hand.get("datetime", ""),
        "game":             hand.get("game", ""),
        "bb_size":          hand.get("blinds", {}).get("bb", ""),
        "hero_position":    hand.get("hero_position", ""),
        "hero_card_1":      hero_cards[0] if len(hero_cards) > 0 else "",
        "hero_card_2":      hero_cards[1] if len(hero_cards) > 1 else "",
        "hero_result_bb":   hand.get("hero_result_bb", ""),
        "hero_won":         hero_won,
        "is_3bet_pot":      hand.get("is_3bet_pot", False),
        "went_to_showdown": hand.get("went_to_showdown", False),
        "last_street":      clf.get("last_street", ""),
        "line":             clf.get("line", ""),
        "category":         clf.get("category", ""),
        "category_label":   clf.get("category_label", ""),
        "flop_board":       flop_board,
        "flop_pot_bb":      flop.get("pot_bb", ""),
        "turn_card":        turn_card,
        "turn_pot_bb":      turn.get("pot_bb", ""),
        "river_card":       river_card,
        "river_pot_bb":     river.get("pot_bb", ""),
        "player_count":     len(hand.get("players", [])),
        "rake_bb":          result.get("rake_bb", ""),
    }


def export(uid: str, out_path: str, limit: int) -> None:
    from scripts.firebase_utils import get_db
    db = get_db()

    print(f"Firestore から解析データを取得中 (uid={uid}, limit={limit})...")

    # UID存在確認
    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists:
        print(f"  [ERROR] users/{uid} が存在しません。UIDが正しいか確認してください。")
        print()
        print("  Firestoreに存在するユーザー一覧:")
        for u in db.collection("users").limit(10).stream():
            print(f"    - {u.id}")
        return

    # analyses サブコレクション確認
    analyses_count = len(list(db.collection("users").document(uid).collection("analyses").limit(5).stream()))
    print(f"  analyses ドキュメント数(先頭5件確認): {analyses_count}")

    ref = (
        db.collection("users").document(uid)
          .collection("analyses")
          .order_by("saved_at")
          .limit(limit)
    )

    rows = []
    for doc in ref.stream():
        d        = doc.to_dict()
        snapshot = d.get("classified_snapshot")
        if not snapshot:
            print(f"  [SKIP] {doc.id}: スナップショットなし")
            continue

        try:
            data = decompress_snapshot(snapshot, d.get("snapshot_encoding", ""))
        except Exception as e:
            print(f"  [WARN] {doc.id}: 展開エラー: {e}")
            continue

        saved_at = d.get("saved_at")
        saved_at_str = saved_at.isoformat() if hasattr(saved_at, "isoformat") else str(saved_at or "")
        source_file  = data.get("source_file", "")
        hands        = data.get("hands", [])

        for hand in hands:
            rows.append(hand_to_row(hand, doc.id, source_file, saved_at_str))

        print(f"  [{doc.id}] {len(hands)} hands  ({source_file})")

    if not rows:
        print("エクスポートするデータがありません。")
        return

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n完了: {len(rows)} 行 → {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Power BI 用 CSV エクスポート")
    ap.add_argument("--uid",   required=True,
                    help="Firebase UID (Firebase Console → Authentication → Users で確認)")
    ap.add_argument("--out",   default="hands_export.csv",
                    help="出力CSVファイルパス (デフォルト: hands_export.csv)")
    ap.add_argument("--limit", type=int, default=500,
                    help="取得する解析数の上限 (デフォルト: 500)")
    args = ap.parse_args()

    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
        print("エラー: 環境変数 FIREBASE_SERVICE_ACCOUNT_JSON が設定されていません。")
        print()
        print("設定方法 (PowerShell):")
        print("  $env:FIREBASE_SERVICE_ACCOUNT_JSON = (Get-Content C:\\path\\to\\sa.json -Raw)")
        sys.exit(1)

    export(args.uid, args.out, args.limit)


if __name__ == "__main__":
    main()
