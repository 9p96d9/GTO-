"""
classify.py - parse.py の出力JSONを読み込み、青線/赤線分類を追加する
使用法: python scripts/classify.py <input.json> <output.json>
スタンドアロン確認: python scripts/classify.py data/upload.json data/upload_classified.json
"""

import json
import sys
import os

try:
    from treys import Card, Evaluator
    _TREYS_AVAILABLE = True
    _evaluator = Evaluator()
except ImportError:
    _TREYS_AVAILABLE = False

SUIT_TO_TREYS = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}

CATEGORY_LABELS = {
    "value_success":          "バリュー成功",
    "bluff_catch":            "ブラフキャッチ",
    "bluff_failed":           "ブラフ失敗",
    "call_lost":              "コール負け",
    "hero_aggression_won":    "アグレッション勝利",
    "bad_fold":               "バッドフォールド",
    "nice_fold":              "ナイスフォールド",
    "fold_unknown":           "フォールド(要確認)",
    "preflop_only":           "プリフロップのみ",
}


# ─── カード変換 ────────────────────────────────────────────────────────────────

def card_to_treys(card_str: str):
    """'A♠' → 'As'  （treysのCard.new()に渡す文字列）"""
    if not card_str:
        return None
    card_str = card_str.replace("\ufe0e", "").replace("\ufe0f", "")
    if len(card_str) < 2:
        return None
    rank = card_str[:-1]
    suit = card_str[-1]
    suit_char = SUIT_TO_TREYS.get(suit)
    if not suit_char:
        return None
    return rank + suit_char


# ─── ハンド情報取得ヘルパー ───────────────────────────────────────────────────

def get_hero_name(hand: dict) -> str:
    for p in hand.get("players", []):
        if p.get("is_hero"):
            return p.get("name", "")
    return ""


def hero_wins(hand: dict) -> bool:
    """heroが勝者かを判定"""
    winners = {w["name"] for w in hand.get("result", {}).get("winners", [])}
    hero_name = get_hero_name(hand)
    return bool(hero_name and hero_name in winners)


def get_last_aggressor(hand: dict):
    """最後にBet/Raiseしたのが 'hero' か 'opponent' か。なければ None"""
    hero_name = get_hero_name(hand)
    streets = hand.get("streets", {})

    for street in ("river", "turn", "flop", "preflop"):
        s = streets.get(street)
        if s is None:
            continue
        actions = s if street == "preflop" else s.get("actions", [])
        if not actions:
            continue

        last_agg = None
        for a in actions:
            if a.get("action") in ("Bet", "Raise"):
                last_agg = "hero" if a.get("name") == hero_name else "opponent"

        if last_agg:
            return last_agg

    return None


def get_all_board_cards(hand: dict) -> list:
    """フロップ〜リバーの有効なボードカードを返す"""
    board = []
    streets = hand.get("streets", {})
    for street in ("flop", "turn", "river"):
        s = streets.get(street)
        if s and isinstance(s, dict):
            board.extend(c for c in s.get("board", []) if c and c != "-")
    return board


def get_last_street_name(hand: dict) -> str:
    """最後にアクションがあったストリート名を返す"""
    streets = hand.get("streets", {})
    for street in ("river", "turn", "flop"):
        s = streets.get(street)
        if s and isinstance(s, dict) and s.get("actions"):
            return street
    return "preflop"


def is_postflop(hand: dict) -> bool:
    """フロップ以降に進んだハンドか"""
    streets = hand.get("streets", {})
    return any(streets.get(s) is not None for s in ("flop", "turn", "river"))


# ─── treys 手役比較 ────────────────────────────────────────────────────────────

def hero_would_win_treys(hand: dict):
    """
    フォールド時にtreysでheroが勝てたか判定。
    True=勝てた, False=負けてた, None=判定不能
    """
    if not _TREYS_AVAILABLE:
        return None

    hero_cards_raw = hand.get("hero_cards", [])
    opp_cards_raw = []
    for p in hand.get("players", []):
        if not p.get("is_hero") and len(p.get("hole_cards", [])) >= 2:
            opp_cards_raw = p["hole_cards"]
            break

    board_raw = get_all_board_cards(hand)

    if len(hero_cards_raw) < 2 or len(opp_cards_raw) < 2 or len(board_raw) < 3:
        return None

    try:
        hero_strs = [card_to_treys(c) for c in hero_cards_raw]
        opp_strs  = [card_to_treys(c) for c in opp_cards_raw]
        board_strs = [card_to_treys(c) for c in board_raw]

        if None in hero_strs or None in opp_strs or None in board_strs:
            return None

        hero_treys  = [Card.new(s) for s in hero_strs]
        opp_treys   = [Card.new(s) for s in opp_strs]
        board_treys = [Card.new(s) for s in board_strs]

        hero_rank = _evaluator.evaluate(board_treys, hero_treys)
        opp_rank  = _evaluator.evaluate(board_treys, opp_treys)

        if hero_rank < opp_rank:   # 低いランク = 強い手
            return True
        elif hero_rank > opp_rank:
            return False
        else:
            return None  # 引き分け

    except Exception:
        return None


# ─── 分類メイン ────────────────────────────────────────────────────────────────

def classify_hand(hand: dict) -> dict:
    """
    ハンドを青線/赤線に分類して bluered_classification dict を返す。
    フィールド:
        line: 'blue' | 'red' | 'preflop_only'
        category: カテゴリ文字列
        category_label: 日本語ラベル
        needs_api: bool
        showdown: bool
        last_street: 最後のストリート名
    """
    if not is_postflop(hand):
        # 3BET+ポットでPF終了 → 赤線に分類（勉強価値あり）
        if hand.get("is_3bet_pot"):
            if hero_wins(hand):
                return {
                    "line": "red",
                    "category": "hero_aggression_won",
                    "category_label": CATEGORY_LABELS["hero_aggression_won"],
                    "needs_api": True,
                    "showdown": False,
                    "last_street": "preflop",
                }
            else:
                # Heroがフォールド（ボードなしのためtreys判定不可）
                return {
                    "line": "red",
                    "category": "fold_unknown",
                    "category_label": CATEGORY_LABELS["fold_unknown"],
                    "needs_api": True,
                    "showdown": False,
                    "last_street": "preflop",
                }
        return {
            "line": "preflop_only",
            "category": "preflop_only",
            "category_label": CATEGORY_LABELS["preflop_only"],
            "needs_api": False,
            "showdown": False,
            "last_street": "preflop",
        }

    went_to_showdown = hand.get("went_to_showdown", False)
    won  = hero_wins(hand)
    last_agg = get_last_aggressor(hand)
    last_street = get_last_street_name(hand)

    if went_to_showdown:
        # ── 青線 ──
        if won:
            cat = "value_success" if last_agg == "hero" else "bluff_catch"
        else:
            cat = "bluff_failed" if last_agg == "hero" else "call_lost"

        return {
            "line": "blue",
            "category": cat,
            "category_label": CATEGORY_LABELS[cat],
            "needs_api": False,
            "showdown": True,
            "last_street": last_street,
        }

    else:
        # ── 赤線 ──
        if won:
            # 相手がfoldしたケース
            return {
                "line": "red",
                "category": "hero_aggression_won",
                "category_label": CATEGORY_LABELS["hero_aggression_won"],
                "needs_api": True,
                "showdown": False,
                "last_street": last_street,
            }
        else:
            # heroがfoldしたケース
            hero_cards_known = len(hand.get("hero_cards", [])) >= 2
            opp_cards_known  = any(
                not p.get("is_hero") and len(p.get("hole_cards", [])) >= 2
                for p in hand.get("players", [])
            )

            if hero_cards_known and opp_cards_known:
                would_win = hero_would_win_treys(hand)
                if would_win is True:
                    cat = "bad_fold"
                elif would_win is False:
                    cat = "nice_fold"
                else:
                    cat = "fold_unknown"
            else:
                cat = "fold_unknown"

            needs_api = cat == "fold_unknown"
            return {
                "line": "red",
                "category": cat,
                "category_label": CATEGORY_LABELS[cat],
                "needs_api": needs_api,
                "showdown": False,
                "last_street": last_street,
            }


# ─── ファイル処理 ──────────────────────────────────────────────────────────────

def classify_file(input_path: str, output_path: str) -> dict:
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hands = data.get("hands", [])
    blue_count = red_count = pf_count = needs_api_count = 0

    for hand in hands:
        clf = classify_hand(hand)
        hand["bluered_classification"] = clf

        if clf["line"] == "blue":
            blue_count += 1
        elif clf["line"] == "red":
            red_count += 1
            if clf["needs_api"]:
                needs_api_count += 1
        else:
            pf_count += 1

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = blue_count + red_count
    print(f"  Classified {total} postflop hands: {blue_count} blue, {red_count} red "
          f"({needs_api_count} needs_api), {pf_count} preflop-only → {output_path}")
    if not _TREYS_AVAILABLE:
        print("  [INFO] treysライブラリ未インストール: フォールド判定はfold_unknownにフォールバック")

    return data


# ─── メイン ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/classify.py <input.json> <output.json>")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = sys.argv[2]

    if not os.path.exists(input_path):
        print(f"Error: {input_path} が見つかりません", file=sys.stderr)
        sys.exit(1)

    classify_file(input_path, output_path)


if __name__ == "__main__":
    main()
