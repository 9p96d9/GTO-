"""
analyze.py - ハンドのGTO評価をローカルルールベースで生成し、JSONに追記する
使用法: python scripts/analyze.py data/file.json

# --- API版（無効化中）---
# import time
# from google import genai
# MODEL = "gemini-2.5-flash"
# BATCH_SIZE = 10
# RETRY_WAIT_ON_429 = 5.0
# MAX_RETRIES_429 = 3
"""

import json
import sys
import os
from datetime import datetime

# from dotenv import load_dotenv
# from google import genai
# load_dotenv()


# ─── ローカルルールベース評価 ─────────────────────────────────────────────────

def rule_based_evaluate(hand: dict) -> dict:
    """
    APIなしでルールベースの簡易GTO評価を返す。
    戻り値: {"gto_rating": str, "ichi": str, "detail": str, "kaizen": str, "ev_loss": str}
    """
    preflop = hand.get("streets", {}).get("preflop", [])
    hero_pos = hand.get("hero_position", "")
    hero_result = hand.get("hero_result_bb", 0.0)
    is_3bet = hand.get("is_3bet_pot", False)
    went_sd = hand.get("went_to_showdown", False)
    hero_cards = hand.get("hero_cards", [])

    # ヒーローのアクションのみ抽出
    hero_pf_actions = [a for a in preflop if a.get("name") and
                       any(p["name"] == a["name"] and p["is_hero"]
                           for p in hand.get("players", []))]
    hero_actions_all = []
    for street in ["preflop", "flop", "turn", "river"]:
        s = hand["streets"].get(street)
        acts = s if street == "preflop" else (s.get("actions", []) if s else [])
        for a in acts:
            if any(p["name"] == a.get("name") and p["is_hero"]
                   for p in hand.get("players", [])):
                hero_actions_all.append((street, a))

    pf_raise = any(a.get("action") == "Raise" for a in hero_pf_actions)
    pf_call  = any(a.get("action") == "Call"  for a in hero_pf_actions)
    pf_fold  = any(a.get("action") == "Fold"  for a in hero_pf_actions)

    # ポストフロップのチェック/コール/ベット
    post_actions = [(st, a) for st, a in hero_actions_all if st != "preflop"]
    post_fold = any(a.get("action") == "Fold" for _, a in post_actions)

    # ─ 判定ロジック ─
    # 1. プリフロップ3BETポットでオープン/コールが多い → 良好
    if is_3bet and pf_raise:
        return {
            "gto_rating": "✅良好",
            "ichi": "3BETアグレッション",
            "detail": f"{hero_pos}から3BETポットを構築。アグレッシブなライン。",
            "kaizen": "",
            "ev_loss": "",
        }

    # 2. SB/BBでのコール → 守備的だが許容範囲
    if hero_pos in ("SB", "BB") and pf_call and not went_sd and hero_result < 0:
        return {
            "gto_rating": "⚠️改善",
            "ichi": "ブラインドからのコール後フォールド",
            "detail": f"{hero_pos}でコールしてポストフロップでフォールド。頻度を要確認。",
            "kaizen": "3BETかフォールドのレンジを広げる",
            "ev_loss": "",
        }

    # 3. BTN/COでフォールド（スティール機会の見逃し）
    if hero_pos in ("BTN", "CO") and pf_fold:
        return {
            "gto_rating": "⚠️改善",
            "ichi": "有利ポジションでフォールド",
            "detail": f"{hero_pos}はスティール有利ポジション。オープンレンジを広げ検討。",
            "kaizen": "BTN/COではオープンレンジを拡大",
            "ev_loss": "",
        }

    # 4. ショーダウン到達して勝利
    if went_sd and hero_result > 0:
        return {
            "gto_rating": "✅良好",
            "ichi": "SDで勝利",
            "detail": f"{hero_pos}でショーダウンまで持ち込み勝利。バリューライン良好。",
            "kaizen": "",
            "ev_loss": "",
        }

    # 5. ショーダウン到達して敗北（大きな負け）
    if went_sd and hero_result < -5:
        return {
            "gto_rating": "🎲クーラー",
            "ichi": "SD敗北（クーラー候補）",
            "detail": f"{hero_pos} SD到達も敗北。ハンド強度と相手レンジを要確認。",
            "kaizen": "",
            "ev_loss": "",
        }

    # 6. プリフロップレイズで勝利
    if pf_raise and hero_result > 0:
        return {
            "gto_rating": "✅良好",
            "ichi": "アグレッシブに勝利",
            "detail": f"{hero_pos}からレイズし勝利。アグレッション良好。",
            "kaizen": "",
            "ev_loss": "",
        }

    # 7. デフォルト（プレイ継続して負け）
    if hero_result < -3:
        return {
            "gto_rating": "⚠️改善",
            "ichi": "損失ハンド",
            "detail": f"{hero_pos}で{hero_result:.1f}bb損失。ライン選択を見直し。",
            "kaizen": "ポジションとレンジを再確認",
            "ev_loss": f"{hero_result:.1f}bb",
        }

    return {
        "gto_rating": "✅良好",
        "ichi": "標準的なプレイ",
        "detail": f"{hero_pos}での標準ライン。大きな問題なし。",
        "kaizen": "",
        "ev_loss": "",
    }


def reconstruct_evaluation(j: dict) -> str:
    """dictから評価テキストを生成（generate.jsが期待する形式）"""
    lines = [f"GTO評価: {j['gto_rating']}"]
    if j.get("ichi"):
        lines.append(f"一言: {j['ichi']}")
    if j.get("detail"):
        lines.append(f"詳細: {j['detail']}")
    if j.get("kaizen"):
        lines.append(f"改善: {j['kaizen']}")
    if j.get("ev_loss"):
        lines.append(f"EV損失推定: {j['ev_loss']}")
    return "\n".join(lines)


def apply_rating_flags(hand: dict, evaluation: str):
    """評価テキストからhas_gto_error/is_good_playを設定"""
    rating = ""
    for line in evaluation.split("\n"):
        stripped = line.strip()
        if stripped.startswith("GTO評価:"):
            rating = stripped[len("GTO評価:"):].strip()
            break
    if not rating:
        rating = evaluation

    if rating.startswith("❌"):
        hand["has_gto_error"] = True
        hand["is_good_play"] = False
    elif rating.startswith("✅") or rating.startswith("🎲"):
        hand["has_gto_error"] = False
        hand["is_good_play"] = True
    else:
        hand["has_gto_error"] = False
        hand["is_good_play"] = False


def save_json(json_path: str, data: dict):
    data["analyzed_at"] = datetime.now().isoformat()
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def analyze_file(json_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hands = data.get("hands", [])
    total = len(hands)

    pending = [(i + 1, hand) for i, hand in enumerate(hands) if not hand.get("analyzed", False)]
    cached_count = total - len(pending)

    if cached_count > 0:
        print(f"  [SKIP] {cached_count}ハンドは評価済みのためスキップ")
    if not pending:
        print(f"  [SKIP] 全{total}ハンドが評価済みです")
        return total, 0

    print(f"  [ANALYZE] {len(pending)}ハンドをローカル評価します")

    errors = 0
    for hand_idx, hand in pending:
        try:
            result = rule_based_evaluate(hand)
            evaluation = reconstruct_evaluation(result)
        except Exception as e:
            print(f"  [ERROR] ハンド{hand_idx}: {e}", file=sys.stderr)
            evaluation = "評価エラー"
            errors += 1

        hand["gto_evaluation"] = evaluation
        hand["analyzed"] = True
        apply_rating_flags(hand, evaluation)

    completed = total
    print(f"  ローカル評価完了: {completed}/{total}ハンド")
    save_json(json_path, data)

    return total, errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze.py <data.json>")
        sys.exit(1)

    json_path = sys.argv[1]
    total, errors = analyze_file(json_path)
    print(f"  Done: {total} hands analyzed, {errors} errors")


if __name__ == "__main__":
    main()
