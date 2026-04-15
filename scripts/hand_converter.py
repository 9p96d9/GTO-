"""
hand_converter.py - fastFoldTableState JSON → parse.py 互換 JSON 変換器

Firestoreに保存された hand_json（fastFoldTableState）を
classify.py が受け取れる形式（parse.py 出力形式）に変換する。
"""

import re
import sys
from datetime import datetime

SUIT_MAP = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}

POSITIONS = {"SB", "BB", "UTG", "UTG+1", "LJ", "HJ", "CO", "BTN"}

ACTION_MAP = {
    "FOLD":  "Fold",
    "CHECK": "Check",
    "CALL":  "Call",
    "BET":   "Bet",
    "RAISE": "Raise",
}


def convert_card(card_str: str) -> str | None:
    """'As' → 'A♠', 'Td' → 'T♦'。変換不能は None"""
    if not card_str or card_str == "**" or len(card_str) < 2:
        return None
    rank = card_str[:-1]
    suit = SUIT_MAP.get(card_str[-1].lower())
    if not suit:
        return None
    return f"{rank}{suit}"


def parse_bb(s: str) -> float:
    """'4bb' / '4.5BB' / '4' → float（大文字小文字を区別しない）"""
    try:
        return float(re.sub(r'[bB]+$', '', str(s).strip()))
    except (ValueError, AttributeError):
        return 0.0


def parse_action_history(action_history: list, pos_to_name: dict) -> dict:
    """
    actionHistory リストをパースして streets / result / is_3bet_pot を返す。
    pos_to_name: position → playerName のマッピング
    """
    streets = {"preflop": [], "flop": None, "turn": None, "river": None}
    result = {"winners": [], "rake_bb": 0.0, "allin_ev": {}}

    current_street = None
    current_actions = []
    current_board = []   # board は communityCards から後で設定
    current_pot = 0.0
    in_results = False

    def flush_street():
        nonlocal current_street, current_actions, current_board, current_pot
        if current_street == "preflop":
            streets["preflop"] = current_actions[:]
        elif current_street in ("flop", "turn", "river"):
            streets[current_street] = {
                "board": current_board[:],
                "pot_bb": current_pot,
                "actions": current_actions[:]
            }
        current_actions = []
        current_board = []
        current_pot = 0.0

    for line in action_history:
        line = line.strip()
        if not line:
            continue

        # --- # で始まるヘッダー・コメント行 ---
        if line.startswith("#"):
            content = line[1:].strip()

            # ストリート開始: "PREFLOP" / "FLOP (8bb)" / "TURN (12bb)" / "RIVER (20bb)"
            m = re.match(r'^(PREFLOP|FLOP|TURN|RIVER)(?:\s+\((\d+\.?\d*)bb?\))?$', content)
            if m:
                flush_street()
                in_results = False
                current_street = m.group(1).lower()
                if m.group(2):
                    current_pot = float(m.group(2))
                continue

            # 勝者: "BB wins 31bb"
            m = re.match(r'^(\w[\w+]*)\s+wins\s+([\d.]+)bb?$', content)
            if m:
                pos = m.group(1)
                amount = float(m.group(2))
                name = pos_to_name.get(pos, pos)
                result["winners"].append({"name": name, "amount_bb": amount})
                continue

            # RESULTS セクション開始
            if content == "RESULTS":
                flush_street()
                in_results = True
                continue

            continue

        # --- RESULTS セクション内 ---
        if in_results:
            # Rake
            m = re.match(r'^Rake:\s*([\d.]+)bb?$', line)
            if m:
                result["rake_bb"] = float(m.group(1))
            # プレイヤー結果行（"BB: +12.5bb" など）は handResults で取得済みのためスキップ
            continue

        # --- アクション行: "POSITION ACTION [AMOUNT]" ---
        parts = line.split()
        if len(parts) < 2:
            continue

        pos = parts[0]
        action_upper = parts[1].upper()
        action = ACTION_MAP.get(action_upper)

        if action and current_street:
            name = pos_to_name.get(pos, pos)
            entry = {"position": pos, "name": name, "action": action}
            # BET/RAISE/CALL の金額（あれば）
            if action in ("Bet", "Raise", "Call") and len(parts) >= 3:
                entry["amount_bb"] = parse_bb(parts[2])
            current_actions.append(entry)
            continue

        # POST（ブラインド）は記録しない

    # 末尾ストリートをフラッシュ
    if current_street and current_actions:
        flush_street()

    # 3BETポット判定（プリフロップで Raise が 2 回以上）
    raise_count = sum(1 for a in streets["preflop"] if a.get("action") == "Raise")
    is_3bet_pot = raise_count >= 2

    return {"streets": streets, "result": result, "is_3bet_pot": is_3bet_pot}


def _calc_hero_investment(streets: dict, hero_pos: str) -> float:
    """
    アクション履歴からHeroの総投資額を計算する。
    GGPokerがフォールドプレイヤーのprofitを0で返す場合の補正用。
    """
    # ブラインドポスト（プリフロップ開始前の強制投資）
    if hero_pos == "BB":
        total = 1.0
    elif hero_pos == "SB":
        total = 0.5
    else:
        total = 0.0

    # プリフロップ: フェイシングベットを追跡し、Hero の最終コミットを記録
    facing = 1.0  # BB が 1bb をポストしているため最低ベットは 1bb
    for a in streets.get("preflop", []):
        action = a.get("action", "")
        amount = a.get("amount_bb", 0.0)
        if action in ("Raise", "Bet") and amount > 0:
            facing = amount
        if a.get("position") == hero_pos and action in ("Raise", "Bet", "Call"):
            committed = amount if amount > 0 else facing
            total = max(total, committed)

    # ポストフロップ（フロップ/ターン/リバー）
    for st_key in ("flop", "turn", "river"):
        s = streets.get(st_key)
        if not s or not isinstance(s, dict):
            continue
        st_facing = 0.0
        st_invested = 0.0
        for a in s.get("actions", []):
            action = a.get("action", "")
            amount = a.get("amount_bb", 0.0)
            if action in ("Raise", "Bet") and amount > 0:
                st_facing = amount
            if a.get("position") == hero_pos:
                if action in ("Bet", "Raise"):
                    st_invested = amount
                    st_facing = amount
                elif action == "Call":
                    st_invested = amount if amount > 0 else st_facing
        total += st_invested

    return total


def convert_hand_json(hand_json: dict, captured_at: str, hand_index: int = 1) -> dict:
    """fastFoldTableState → parse.py 出力形式の dict に変換する"""

    hand_results = hand_json.get("handResults") or []
    seats = hand_json.get("seats") or []
    my_seat_index = hand_json.get("mySeatIndex", -1)
    community_cards_raw = hand_json.get("communityCards") or []

    # position → playerName マッピング（actionHistory パース用）
    pos_to_name = {r.get("position", ""): r.get("playerName", "") for r in hand_results}

    # プレイヤー一覧を handResults から構築
    players = []
    for r in hand_results:
        seat_index = r.get("seatIndex", -1)
        position = r.get("position", "")
        player_name = r.get("playerName", "")
        profit = r.get("profit", 0.0)
        is_hero = (seat_index == my_seat_index)

        # ホールカード（"As" → "A♠" 変換）
        hole_cards = []
        for c in r.get("hand", []):
            converted = convert_card(c)
            if converted:
                hole_cards.append(converted)

        players.append({
            "position":  position,
            "name":      player_name,
            "is_hero":   is_hero,
            "hole_cards": hole_cards,
            "result_bb": float(profit),
        })

    # actionHistory パース
    action_history = hand_json.get("actionHistory") or []
    parsed = parse_action_history(action_history, pos_to_name)
    streets = parsed["streets"]
    result = parsed["result"]

    # ボードカード（communityCards）を各ストリートに補完
    board = [convert_card(c) for c in community_cards_raw if convert_card(c)]
    if len(board) >= 3 and streets.get("flop") is not None and not streets["flop"]["board"]:
        streets["flop"]["board"] = board[:3]
    if len(board) >= 4 and streets.get("turn") is not None and not streets["turn"]["board"]:
        streets["turn"]["board"] = [board[3]]
    if len(board) >= 5 and streets.get("river") is not None and not streets["river"]["board"]:
        streets["river"]["board"] = [board[4]]

    # ショーダウン判定: isFolded == False の席が 2 つ以上 かつ 手が完了
    is_complete = not hand_json.get("isHandInProgress", True)
    active_count = sum(
        1 for s in seats
        if s.get("playerName") and not s.get("isFolded", True)
    )
    went_to_showdown = is_complete and active_count >= 2

    # Hero 情報
    hero = next((p for p in players if p["is_hero"]), None)
    hero_position = hero["position"] if hero else ""
    hero_cards = hero["hole_cards"] if hero else []
    hero_result_bb = hero["result_bb"] if hero else 0.0

    # GGPokerはフォールドしたプレイヤーのprofitを0で返すことがある。
    # Hero が勝者でなく profit==0 の場合、アクション履歴から投資額を補正する。
    hero_is_winner = hero and any(
        w.get("name") == (hero.get("name") or hero_position)
        for w in result.get("winners", [])
    )
    if hero and hero_result_bb == 0.0 and not hero_is_winner:
        hero_result_bb = -_calc_hero_investment(streets, hero_position)

    # result.winners が actionHistory でパースできなかった場合 handResults で補完
    if not result["winners"]:
        for r in hand_results:
            if r.get("isWinner"):
                result["winners"].append({
                    "name": r.get("playerName", ""),
                    "amount_bb": float(r.get("profit", 0.0)),
                })

    # datetime
    try:
        dt_str = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).isoformat()
    except Exception:
        dt_str = captured_at

    return {
        "hand_number":    hand_index,
        "hand_id":        hand_json.get("tableId", ""),
        "datetime":       dt_str,
        "game":           "6-Max NLH Fast Fold",
        "blinds":         {"sb": 0.5, "bb": 1.0},
        "players":        players,
        "streets":        streets,
        "showdown":       [],
        "result":         result,
        "hero_position":  hero_position,
        "hero_cards":     hero_cards,
        "hero_result_bb": hero_result_bb,
        "is_3bet_pot":    parsed["is_3bet_pot"],
        "went_to_showdown": went_to_showdown,
        "analyzed":       False,
        "gto_evaluation": "",
        "has_gto_error":  False,
        "is_good_play":   False,
    }


def convert_hands_batch(hands_data: list) -> dict:
    """
    Firestoreから取得した hands リストを一括変換し、parse.py 出力形式の dict を返す。
    hands_data: [{"hand_json": {...}, "captured_at": "...", "hand_id": "..."}, ...]
    """
    converted = []
    for i, item in enumerate(hands_data, 1):
        try:
            hand = convert_hand_json(
                item.get("hand_json", {}),
                item.get("captured_at", ""),
                hand_index=i,
            )
            converted.append(hand)
        except Exception as e:
            print(f"  [WARN] convert error (hand_id={item.get('hand_id', '?')}): {e}", file=sys.stderr)

    return {
        "source_file": "realtime_hands",
        "parsed_at":   datetime.now().isoformat(),
        "hero_name":   "",
        "hands":       converted,
    }
