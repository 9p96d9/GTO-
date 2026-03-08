"""
parse.py - ポーカーハンドログ → JSON パーサー
使用法: python scripts/parse.py input/file.txt data/file.json
"""

import re
import json
import sys
import os
from datetime import datetime


HERO_NAMES = {"Guest"}
HERO_PATTERN = re.compile(r'^Weq\*+$')

POSITIONS = {"SB", "BB", "UTG", "UTG+1", "LJ", "HJ", "CO", "BTN"}

ACTIONS = {"Fold", "Check", "Bet", "Call", "Raise"}

SUITS_MAP = {"♠": "spade", "♥": "heart", "♦": "diamond", "♣": "club"}


def is_hero(name: str) -> bool:
    return name in HERO_NAMES or bool(HERO_PATTERN.match(name))


def parse_amount(s: str) -> float:
    """±0bb / +4.17bb / -0.5bb → float"""
    s = s.strip()
    if s.startswith("±"):
        return 0.0
    s = s.rstrip("bb").replace("bb", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_card(s: str) -> bool:
    """カード文字列かどうかを判定（例: K♠, T♥, 2♦）"""
    if len(s) < 2:
        return False
    suits = "♠♥♦♣"
    ranks = "23456789TJQKA"
    # 1〜2文字のランク + スート
    if s[-1] in suits and s[:-1] in ranks:
        return True
    return False


def is_amount_line(s: str) -> bool:
    s = s.strip()
    return bool(re.match(r'^[±+\-]?\d+\.?\d*bb$', s))


def is_street_line(s: str) -> bool:
    return s in ("Preflop",) or bool(re.match(r'^(Flop|Turn|River)\d', s))


def split_hands(lines: list[str]) -> list[list[str]]:
    """============行 + ハンドN/M行 でハンドを分割する"""
    hands = []
    current = []
    sep_pattern = re.compile(r'^={10,}$')
    hand_header_pattern = re.compile(r'^ハンド\s+(\d+)\s*/\s*(\d+)$')

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if sep_pattern.match(line):
            # 次の行がハンドN/M行かチェック
            if i + 1 < len(lines) and hand_header_pattern.match(lines[i + 1].strip()):
                if current:
                    hands.append(current)
                current = []
                i += 1  # skip separator
                current.append(lines[i].strip())  # add "ハンド N / M"
                i += 1
                continue
        current.append(line)
        i += 1

    if current:
        hands.append(current)

    return hands


SKIP_LINES = {
    "ハンドヒストリー詳細",
    "×",
    "All-in EV",
    "Result",
    "SD",
}


def parse_hand(raw_lines: list[str]) -> dict:
    """1ハンド分の行リストをパースしてdictを返す"""
    lines = [l.strip() for l in raw_lines if l.strip()]

    hand = {
        "hand_number": 0,
        "hand_id": "",
        "datetime": "",
        "game": "",
        "blinds": {"sb": 0.5, "bb": 1.0},
        "players": [],
        "streets": {
            "preflop": [],
            "flop": None,
            "turn": None,
            "river": None,
        },
        "showdown": [],
        "result": {
            "winners": [],
            "rake_bb": 0.0,
            "allin_ev": {}
        },
        "hero_position": "",
        "hero_cards": [],
        "hero_result_bb": 0.0,
        "is_3bet_pot": False,
        "went_to_showdown": False,
        "analyzed": False,
        "gto_evaluation": "",
        "has_gto_error": False,
        "is_good_play": False,
    }

    idx = 0

    # ハンド番号行
    m = re.match(r'^ハンド\s+(\d+)\s*/\s*(\d+)$', lines[idx])
    if m:
        hand["hand_number"] = int(m.group(1))
        idx += 1

    # スキップ行を飛ばしながらハンドID・日時・ゲーム情報を取得
    while idx < len(lines):
        line = lines[idx]

        if line in ("ハンドヒストリー詳細", "×"):
            idx += 1
            continue

        # Hand ID
        m = re.match(r'^Hand #(.+)$', line)
        if m:
            hand["hand_id"] = m.group(1)
            idx += 1
            continue

        # 日時・ゲーム情報行
        m = re.match(r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s+·\s+(.+?)\s+\((.+?)\)$', line)
        if m:
            dt_str = m.group(1)
            game = m.group(2).strip()
            blinds_str = m.group(3).strip()
            try:
                hand["datetime"] = datetime.strptime(dt_str, "%Y/%m/%d %H:%M").isoformat()
            except ValueError:
                hand["datetime"] = dt_str
            hand["game"] = game

            # ブラインド解析: "0.5/1"
            blind_parts = blinds_str.split("/")
            if len(blind_parts) == 2:
                try:
                    hand["blinds"]["sb"] = float(blind_parts[0])
                    hand["blinds"]["bb"] = float(blind_parts[1])
                except ValueError:
                    pass
            idx += 1
            break

        idx += 1

    # プレイヤーブロック解析（Preflop行の前まで）
    players = []
    while idx < len(lines):
        line = lines[idx]
        if line == "Preflop":
            break
        if line in SKIP_LINES or line.startswith("Hand #") or re.match(r'^\d{4}/', line):
            idx += 1
            continue

        # ポジション行かチェック
        if line in POSITIONS:
            pos = line
            if idx + 4 < len(lines):
                name_line = lines[idx + 1]
                card1_line = lines[idx + 2]
                card2_line = lines[idx + 3]
                result_line = lines[idx + 4]

                if is_card(card1_line) and is_card(card2_line) and is_amount_line(result_line):
                    player = {
                        "position": pos,
                        "name": name_line,
                        "is_hero": is_hero(name_line),
                        "hole_cards": [card1_line, card2_line],
                        "result_bb": parse_amount(result_line),
                    }
                    players.append(player)
                    idx += 5
                    continue

        idx += 1

    hand["players"] = players

    # ヒーロー情報設定
    for p in players:
        if p["is_hero"]:
            hand["hero_position"] = p["position"]
            hand["hero_cards"] = p["hole_cards"]
            hand["hero_result_bb"] = p["result_bb"]
            break

    # ストリート解析
    street_data = parse_streets(lines, idx)
    hand["streets"] = street_data["streets"]
    hand["showdown"] = street_data["showdown"]
    hand["result"] = street_data["result"]
    hand["went_to_showdown"] = len(street_data["showdown"]) > 0

    # 3BETポット判定
    hand["is_3bet_pot"] = detect_3bet(hand["streets"]["preflop"])

    return hand


def parse_streets(lines: list[str], start_idx: int) -> dict:
    """ストリート・ショーダウン・リザルトをパースする"""
    result = {
        "streets": {
            "preflop": [],
            "flop": None,
            "turn": None,
            "river": None,
        },
        "showdown": [],
        "result": {
            "winners": [],
            "rake_bb": 0.0,
            "allin_ev": {}
        }
    }

    current_street = None
    current_street_actions = []
    current_board = []
    current_pot = 0.0

    i = start_idx
    in_result = False
    in_allin_ev = False
    in_showdown = False
    sd_player_buf = []

    def flush_street():
        nonlocal current_street, current_street_actions, current_board, current_pot
        if current_street == "preflop":
            result["streets"]["preflop"] = current_street_actions[:]
        elif current_street == "flop":
            result["streets"]["flop"] = {
                "board": current_board[:],
                "pot_bb": current_pot,
                "actions": current_street_actions[:]
            }
        elif current_street == "turn":
            result["streets"]["turn"] = {
                "board": current_board[:],
                "pot_bb": current_pot,
                "actions": current_street_actions[:]
            }
        elif current_street == "river":
            result["streets"]["river"] = {
                "board": current_board[:],
                "pot_bb": current_pot,
                "actions": current_street_actions[:]
            }
        current_street_actions = []
        current_board = []

    while i < len(lines):
        line = lines[i]

        # All-in EV セクション
        if line == "All-in EV":
            in_allin_ev = True
            in_result = False
            i += 1
            continue

        if in_allin_ev:
            m = re.match(r'^(.+):\s*([+\-]?\d+\.?\d*bb?)$', line)
            if m:
                name = m.group(1).strip()
                amount_str = m.group(2).strip().rstrip("bb")
                try:
                    result["result"]["allin_ev"][name] = float(amount_str)
                except ValueError:
                    pass
                i += 1
                continue
            else:
                in_allin_ev = False

        # Result セクション
        if line == "Result":
            flush_street()
            in_result = True
            in_showdown = False
            i += 1
            continue

        if in_result:
            # Rake行
            m = re.match(r'^Rake:\s*([+\-]?\d+\.?\d*bb?)$', line)
            if m:
                rake_str = m.group(1).strip().rstrip("bb")
                try:
                    result["result"]["rake_bb"] = float(rake_str)
                except ValueError:
                    pass
                i += 1
                continue

            # 勝者行: "BTN Guest won 6.17bb" or "SB Guest won 11.09bb"
            m = re.match(r'^(\w+)\s+(.+?)\s+won\s+([+\-]?\d+\.?\d*bb?)$', line)
            if m:
                name = m.group(2).strip()
                amount_str = m.group(3).strip().rstrip("bb")
                try:
                    amount = float(amount_str)
                except ValueError:
                    amount = 0.0
                result["result"]["winners"].append({"name": name, "amount_bb": amount})
                i += 1
                continue

            if line == "All-in EV":
                in_allin_ev = True
                in_result = False
                i += 1
                continue

            i += 1
            continue

        # SD（ショーダウン）セクション
        sd_m = re.match(r'^SD(\d+\.?\d*bb?)$', line)
        if sd_m:
            flush_street()
            in_showdown = True
            in_result = False
            sd_player_buf = []
            i += 1
            continue

        if in_showdown:
            if line == "Result":
                # SDからResult移行
                in_showdown = False
                in_result = True
                i += 1
                continue

            if line in POSITIONS:
                sd_player_buf.append({"_pos": line})
                i += 1
                continue

            # "Name:" 行
            if line.endswith(":"):
                if sd_player_buf and "_pos" in sd_player_buf[-1]:
                    sd_player_buf[-1]["name"] = line[:-1]
                i += 1
                continue

            # 役名行（ポジションでもResultでもない）
            if sd_player_buf and "name" in sd_player_buf[-1] and "hand_name" not in sd_player_buf[-1]:
                sd_player_buf[-1]["hand_name"] = line
                entry = {"name": sd_player_buf[-1]["name"], "hand_name": line}
                result["showdown"].append(entry)
                i += 1
                continue

            i += 1
            continue

        # Preflop
        if line == "Preflop":
            flush_street()
            current_street = "preflop"
            i += 1
            continue

        # Flop/Turn/River
        street_m = re.match(r'^(Flop|Turn|River)(\d+\.?\d*bb?)$', line)
        if street_m:
            flush_street()
            street_name = street_m.group(1).lower()
            current_street = street_name
            pot_str = street_m.group(2).rstrip("bb")
            try:
                current_pot = float(pot_str)
            except ValueError:
                current_pot = 0.0
            # 次の行(s)はボードカード
            i += 1
            # フロップ3枚、ターン/リバー1枚
            if street_name == "flop":
                for _ in range(3):
                    if i < len(lines) and is_card(lines[i]):
                        current_board.append(lines[i])
                        i += 1
                    elif i < len(lines) and lines[i] == "-":
                        current_board.append("-")
                        i += 1
            else:
                if i < len(lines) and is_card(lines[i]):
                    current_board.append(lines[i])
                    i += 1
                elif i < len(lines) and lines[i] == "-":
                    current_board.append("-")
                    i += 1
            continue

        # "-" はアクションなし（All-in後ボード省略）
        if line == "-":
            i += 1
            continue

        # アクション解析：ポジション行 → 名前 → アクション → [金額]
        if current_street and line in POSITIONS:
            pos = line
            if i + 2 < len(lines):
                name_line = lines[i + 1]
                action_line = lines[i + 2]

                if action_line in ACTIONS:
                    action_entry = {
                        "position": pos,
                        "name": name_line,
                        "action": action_line,
                    }
                    # 金額が必要なアクション
                    if action_line in ("Bet", "Call", "Raise"):
                        if i + 3 < len(lines) and is_amount_line(lines[i + 3]):
                            action_entry["amount_bb"] = parse_amount(lines[i + 3])
                            i += 4
                        else:
                            action_entry["amount_bb"] = 0.0
                            i += 3
                    else:
                        i += 3
                    current_street_actions.append(action_entry)
                    continue

        i += 1

    # 最後のストリートをflush
    if current_street and current_street_actions:
        flush_street()

    return result


def detect_3bet(preflop_actions: list[dict]) -> bool:
    """プリフロップでRaise→Raiseがあれば3BETポット"""
    raise_count = sum(1 for a in preflop_actions if a.get("action") == "Raise")
    return raise_count >= 2


# ── 対戦相手サマリー ──────────────────────────────────────────────────────────────

def calc_player_type(vpip_pct: float, pfr_pct: float) -> str:
    """VPIP/PFR からプレイヤータイプを判定（generate.js と同ロジック）"""
    r = pfr_pct / max(vpip_pct, 1)
    if vpip_pct > 40 and r > 0.8:  return "LAG"
    if vpip_pct > 40 and r < 0.4:  return "LP"
    if vpip_pct < 20 and r > 0.8:  return "TAG"
    if vpip_pct < 20 and r < 0.4:  return "TP"
    if vpip_pct > 40:               return "ルース"
    if vpip_pct < 20:               return "タイト"
    if r > 0.8:                     return "アグレッシブ"
    if r < 0.4:                     return "パッシブ"
    return "バランス"


def update_opponents_summary(hands: list, source_file: str, summary_path: str, session_date: str):
    """opponents_summary.json を更新する。同一ファイルの二重登録は防止する。"""
    # 既存サマリーを読み込む（なければ初期化）
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    else:
        summary = {
            "last_updated": "",
            "total_sessions": 0,
            "sessions": [],
            "processed_files": [],
            "opponents": {},
        }

    # 同じファイルが既に処理済みなら二重登録しない（Ctrl+C後の再実行対策）
    if source_file in summary.get("processed_files", []):
        print(f"  [SKIP] {source_file} は対戦相手サマリーに既に登録済み")
        return

    opponents = summary.get("opponents", {})

    for hand in hands:
        # ヒーロー名を特定
        hero_name = next((p["name"] for p in hand.get("players", []) if p.get("is_hero")), None)

        # ヒーローが勝ったかどうか
        winners = {w["name"] for w in hand.get("result", {}).get("winners", [])}
        hero_won = hero_name in winners if hero_name else False

        # プリフロップアクション
        preflop = hand.get("streets", {}).get("preflop", [])

        for p in hand.get("players", []):
            if p.get("is_hero"):
                continue
            name = p.get("name", "").strip()
            if not name:
                continue

            if name not in opponents:
                opponents[name] = {
                    "total_hands": 0,
                    "vpip_count": 0,
                    "pfr_count": 0,
                    "threebet_count": 0,
                    "hero_won": 0,
                    "vpip": 0.0,
                    "pfr": 0.0,
                    "threebet": 0.0,
                    "hero_winrate": 0.0,
                    "player_type": "バランス",
                    "sessions": [],
                }

            opp = opponents[name]
            opp["total_hands"] += 1

            # VPIP: プリフロップで Call または Raise
            opp_pf = [a for a in preflop if a.get("name") == name]
            if any(a["action"] in ("Call", "Raise") for a in opp_pf):
                opp["vpip_count"] += 1
            if any(a["action"] == "Raise" for a in opp_pf):
                opp["pfr_count"] += 1
            if hand.get("is_3bet_pot") and any(a["action"] == "Raise" for a in opp_pf):
                opp["threebet_count"] += 1
            if hero_won:
                opp["hero_won"] += 1

            # セッション日付を追加（重複なし）
            if session_date and session_date not in opp["sessions"]:
                opp["sessions"].append(session_date)
                opp["sessions"].sort()

            # レートを再計算
            n = opp["total_hands"]
            vpip_pct = opp["vpip_count"] / n * 100
            pfr_pct  = opp["pfr_count"]  / n * 100
            opp["vpip"]        = round(opp["vpip_count"]     / n, 4)
            opp["pfr"]         = round(opp["pfr_count"]      / n, 4)
            opp["threebet"]    = round(opp["threebet_count"] / n, 4)
            opp["hero_winrate"] = round(opp["hero_won"]      / n, 4)
            opp["player_type"] = calc_player_type(vpip_pct, pfr_pct)

    # サマリーメタデータを更新
    sessions_list = summary.get("sessions", [])
    if session_date and session_date not in sessions_list:
        sessions_list.append(session_date)
        sessions_list.sort()

    processed_files = summary.get("processed_files", [])
    processed_files.append(source_file)

    summary["opponents"]        = opponents
    summary["sessions"]         = sessions_list
    summary["processed_files"]  = processed_files
    summary["total_sessions"]   = len(sessions_list)
    summary["last_updated"]     = session_date or datetime.now().strftime("%Y-%m-%d")

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    opp_count = len([p for p in hands[0].get("players", []) if not p.get("is_hero")]) if hands else 0
    print(f"  [OPPONENTS] {source_file} → opponents_summary.json 更新完了")


# ── メイン処理 ──────────────────────────────────────────────────────────────────

def parse_file(input_path: str) -> dict:
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    raw_hands = split_hands(lines)

    hands = []
    for raw in raw_hands:
        if not raw:
            continue
        try:
            h = parse_hand(raw)
            if h["hand_number"] > 0 or h["hand_id"]:
                hands.append(h)
        except Exception as e:
            print(f"  [WARN] hand parse error: {e}", file=sys.stderr)

    return {
        "source_file": os.path.basename(input_path),
        "parsed_at": datetime.now().isoformat(),
        "hands": hands,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/parse.py <input.txt> <output.json>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    result = parse_file(input_path)
    hand_count = len(result["hands"])

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  Parsed {hand_count} hands → {output_path}")

    # 対戦相手サマリーを更新
    data_dir = os.path.dirname(os.path.abspath(output_path))
    summary_path = os.path.join(data_dir, "opponents_summary.json")
    source_file = os.path.basename(output_path)

    # セッション日付を取得（最初のハンドの日付）
    session_date = ""
    dates = [h.get("datetime", "")[:10] for h in result["hands"] if h.get("datetime")]
    if dates:
        session_date = min(dates)
    if not session_date:
        session_date = datetime.now().strftime("%Y-%m-%d")

    update_opponents_summary(result["hands"], source_file, summary_path, session_date)

    return hand_count


if __name__ == "__main__":
    main()
