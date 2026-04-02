"""
quick_analyzer.py - クイック解析: parse.py のJSONから統計を計算する（APIなし）
"""
from collections import Counter, defaultdict


RANKS = ['A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2']
SUITS = '♠♥♦♣'


# ── ヒーロー検出 ─────────────────────────────────────────────────────────────

def detect_hero(hands: list) -> str:
    """全ハンドで最も多く登場するプレイヤー名をヒーローとして返す"""
    name_counts: Counter = Counter()
    for hand in hands:
        for p in hand.get("players", []):
            name = p.get("name", "").strip()
            if name:
                name_counts[name] += 1
    return name_counts.most_common(1)[0][0] if name_counts else ""


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def get_hero_result(hand: dict, hero_name: str) -> float:
    for p in hand.get("players", []):
        if p.get("name") == hero_name:
            return float(p.get("result_bb", 0.0))
    return 0.0


def get_hero_cards(hand: dict, hero_name: str) -> list:
    for p in hand.get("players", []):
        if p.get("name") == hero_name:
            return p.get("hole_cards", [])
    return []


def went_to_showdown(hand: dict) -> bool:
    return len(hand.get("showdown", [])) > 0


def iter_street_actions(hand: dict):
    """全ストリートのアクションを (street_name, action) で順に返すジェネレータ"""
    streets = hand.get("streets", {})
    pf = streets.get("preflop", [])
    if isinstance(pf, list):
        for a in pf:
            yield "preflop", a
    for sname in ("flop", "turn", "river"):
        s = streets.get(sname)
        if isinstance(s, dict):
            for a in s.get("actions", []):
                yield sname, a


def get_last_street(hand: dict) -> str:
    """ハンドが決着したストリートを返す"""
    streets = hand.get("streets", {})
    for sname in ("river", "turn", "flop"):
        s = streets.get(sname)
        if isinstance(s, dict) and (s.get("actions") or went_to_showdown(hand)):
            return sname
    return "preflop"


# ── カード → コンボキー ────────────────────────────────────────────────────

def _parse_card(c: str):
    """'A♠' → ('A', '♠')"""
    c = c.strip()
    for s in SUITS:
        if c.endswith(s):
            return c[:-1], s
    return c, ""


def cards_to_combo_key(cards: list) -> str:
    """['A♠', 'K♥'] → 'AKo'  /  ['A♠', 'K♠'] → 'AKs'  /  ['A♠', 'A♥'] → 'AA'"""
    if len(cards) != 2:
        return ""
    r1, s1 = _parse_card(cards[0])
    r2, s2 = _parse_card(cards[1])
    if r1 not in RANKS or r2 not in RANKS:
        return ""
    # 高いランクを先頭に
    if RANKS.index(r1) > RANKS.index(r2):
        r1, r2 = r2, r1
        s1, s2 = s2, s1
    if r1 == r2:
        return r1 + r2            # ポケットペア
    suited = "s" if s1 == s2 else "o"
    return r1 + r2 + suited


# ── ベットサイジング ────────────────────────────────────────────────────────

_SIZING_BUCKETS = ["〜33%", "〜66%", "〜100%", "オーバーベット"]


def _size_bucket(ratio: float) -> str:
    if ratio <= 0.33:
        return "〜33%"
    if ratio <= 0.66:
        return "〜66%"
    if ratio <= 1.0:
        return "〜100%"
    return "オーバーベット"


def compute_bet_sizing_stats(hands: list, hero_name: str) -> list:
    """フロップ以降のヒーローBet/Raiseをポット比で分類して集計"""
    buckets = {k: {"wins": 0, "total": 0, "bb_sum": 0.0} for k in _SIZING_BUCKETS}

    for hand in hands:
        hero_result = get_hero_result(hand, hero_name)
        won = hero_result > 0
        streets = hand.get("streets", {})

        for sname in ("flop", "turn", "river"):
            street = streets.get(sname)
            if not isinstance(street, dict):
                continue
            running_pot = float(street.get("pot_bb", 0.0))

            for action in street.get("actions", []):
                atype = action.get("action", "")
                amount = float(action.get("amount_bb", 0.0))

                if (action.get("name") == hero_name
                        and atype in ("Bet", "Raise")
                        and amount > 0
                        and running_pot > 0):
                    key = _size_bucket(amount / running_pot)
                    buckets[key]["total"] += 1
                    buckets[key]["bb_sum"] += hero_result
                    if won:
                        buckets[key]["wins"] += 1

                # ランニングポット更新
                if atype in ("Bet", "Call", "Raise") and amount > 0:
                    running_pot += amount

    result = []
    for label in _SIZING_BUCKETS:
        d = buckets[label]
        n = d["total"]
        result.append({
            "range": label,
            "winrate": round(d["wins"] / n, 3) if n >= 5 else None,
            "avg_bb":  round(d["bb_sum"] / n, 2) if n >= 5 else None,
            "count": n,
        })
    return result


# ── 勝利パターン分類 ───────────────────────────────────────────────────────

_STRONG_HAND_KW = [
    "three", "straight", "flush", "full", "four", "quads", "set", "trips",
    "スリー", "ストレート", "フラッシュ", "フルハウス", "フォー", "セット", "トリップ",
]
_TOP_PAIR_KW = [
    "top pair", "two pair", "overpair", "top two",
    "トップペア", "ツーペア", "オーバーペア",
]


def compute_win_type(hand: dict, hero_name: str) -> str | None:
    """
    value       : 勝ち + SD + 強いハンド（トップペア以上）
    bluff       : 勝ち + no SD + 最終アクションが Bet/Raise
    bluff_catch : 勝ち + SD + 最終アクションが Call
    None        : 負け or 判定困難
    """
    if get_hero_result(hand, hero_name) <= 0:
        return None

    sd = went_to_showdown(hand)
    last_hero_action = None
    for _, action in iter_street_actions(hand):
        if action.get("name") == hero_name:
            last_hero_action = action.get("action")

    if sd:
        # ショーダウン時: 役名テキストで強度判定
        hero_hand_name = ""
        for entry in hand.get("showdown", []):
            if entry.get("name") == hero_name:
                hero_hand_name = entry.get("hand_name", "").lower()
                break

        if last_hero_action == "Call":
            return "bluff_catch"
        hn = hero_hand_name
        if any(kw in hn for kw in _STRONG_HAND_KW + _TOP_PAIR_KW):
            return "value"
        return None
    else:
        if last_hero_action in ("Bet", "Raise"):
            return "bluff"
        return None


# ── メイン集計 ─────────────────────────────────────────────────────────────

def compute_quick_stats(parsed_data: dict) -> dict:
    hands = parsed_data.get("hands", [])
    if not hands:
        return {"error": "ハンドが見つかりませんでした"}

    hero_name = detect_hero(hands)
    hero_hands = [
        h for h in hands
        if any(p.get("name") == hero_name for p in h.get("players", []))
    ]
    if not hero_hands:
        return {"error": f"ヒーロー（{hero_name}）のハンドが見つかりませんでした"}

    total_hands = len(hero_hands)
    total_bb = sum(get_hero_result(h, hero_name) for h in hero_hands)
    bb_per_100 = total_bb / total_hands * 100 if total_hands else 0.0

    # ── 1. タイムライン ──────────────────────────────────────────────────────
    timeline = []
    cumulative = 0.0
    for i, hand in enumerate(hero_hands):
        cumulative += get_hero_result(hand, hero_name)
        timeline.append({"hand": i + 1, "cumulative": round(cumulative, 2)})

    # ── 2. ストリート別決着 ──────────────────────────────────────────────────
    street_counts = {"preflop": 0, "flop": 0, "turn": 0, "river": 0}
    street_sd     = {"preflop": 0, "flop": 0, "turn": 0, "river": 0}
    for hand in hero_hands:
        ls = get_last_street(hand)
        street_counts[ls] += 1
        if went_to_showdown(hand):
            street_sd[ls] += 1

    # ── 3. ベットサイジング ──────────────────────────────────────────────────
    bet_sizing = compute_bet_sizing_stats(hero_hands, hero_name)

    # ── 4. 勝利パターン ──────────────────────────────────────────────────────
    win_types = {"value": 0, "bluff": 0, "bluff_catch": 0, "other": 0}
    for hand in hero_hands:
        wt = compute_win_type(hand, hero_name)
        if wt in win_types:
            win_types[wt] += 1
        else:
            win_types["other"] += 1

    # ── 5. コンボヒートマップ ────────────────────────────────────────────────
    combo_acc: dict = defaultdict(lambda: {"count": 0, "bb_sum": 0.0, "wins": 0})
    for hand in hero_hands:
        key = cards_to_combo_key(get_hero_cards(hand, hero_name))
        if not key:
            continue
        r = get_hero_result(hand, hero_name)
        combo_acc[key]["count"] += 1
        combo_acc[key]["bb_sum"] += r
        if r > 0:
            combo_acc[key]["wins"] += 1

    combos = {}
    for key, d in combo_acc.items():
        n = d["count"]
        combos[key] = {
            "count": n,
            "bb": round(d["bb_sum"], 2),
            "winrate": round(d["wins"] / n, 3) if n > 0 else 0.0,
        }

    return {
        "hero_name": hero_name,
        "summary": {
            "total_hands": total_hands,
            "total_bb":    round(total_bb, 2),
            "bb_per_100":  round(bb_per_100, 1),
        },
        "timeline":   timeline,
        "streets": {
            "counts":   street_counts,
            "showdown": street_sd,
        },
        "bet_sizing": bet_sizing,
        "win_types":  win_types,
        "combos":     combos,
    }
