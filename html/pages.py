"""
html/pages.py - HTML生成関数・定数
各ページのHTMLはすべて templates/ 配下のJinja2テンプレートで管理。
このファイルは薄いラッパー関数のみを提供する。
"""

import json as _json
import re as _re
from pathlib import Path

_TEMPLATES_DIR = str(Path(__file__).parent.parent / "templates")


def _render(template_name: str, **ctx) -> str:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=False)
    return env.get_template(template_name).render(**ctx)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── 静的ページ（変数なし） ───────────────────────────────────────────────
def _landing_page() -> str:
    return _render("landing.html")


def _upload_page() -> str:
    return _render("upload.html")


# Backward-compatible constants (routes/pages.py が直接参照)
LANDING_PAGE = property(lambda self: _landing_page())
UPLOAD_PAGE  = property(lambda self: _upload_page())

# routes/pages.py は直接 LANDING_PAGE / UPLOAD_PAGE を文字列として参照するため
# モジュールレベルで遅延評価できないので、モジュールロード時に一度レンダリングする
import os as _os
_SKIP_RENDER = _os.environ.get("_PAGES_SKIP_RENDER", "")

if not _SKIP_RENDER:
    LANDING_PAGE = _landing_page()
    UPLOAD_PAGE  = _upload_page()


# ─── 動的ページ（変数あり） ───────────────────────────────────────────────
def progress_page(job_id: str, mode: str = "api") -> str:
    label2 = "青線/赤線を分類" if mode == "noapi" else "GTO分析（Gemini API）"
    return _render("progress.html", job_id=job_id, label2=label2)


def classify_progress_page(job_id: str) -> str:
    return _render("classify_progress.html", job_id=job_id)


def report_page(pdf_name: str) -> str:
    return _render("report.html", pdf_name=pdf_name)


ERROR_PAGE = _render("error.html", log="{log}")  # {log} placeholder for .format()


def dashboard_page(result: dict) -> str:
    data_json = _json.dumps(result, ensure_ascii=False)
    hero      = result.get("hero_name", "Hero")
    summary   = result.get("summary", {})
    total_hands = summary.get("total_hands", 0)
    total_bb    = summary.get("total_bb", 0)
    bb_per_100  = summary.get("bb_per_100", 0)
    bb_color    = "#4caf93" if total_bb >= 0 else "#e94560"
    bb100_color = "#4caf93" if bb_per_100 >= 0 else "#e94560"
    return _render(
        "dashboard.html",
        hero=_esc(hero),
        total_hands=total_hands,
        bb_color=bb_color,
        total_bb_sign="+" if total_bb >= 0 else "",
        total_bb=total_bb,
        bb100_color=bb100_color,
        bb_per_100_sign="+" if bb_per_100 >= 0 else "",
        bb_per_100=bb_per_100,
        data_json=data_json,
    )


# ─── 認証・セッション系ページ ─────────────────────────────────────────────
_LOGIN_PAGE_HTML    = _render("login.html")
_SESSIONS_PAGE_HTML = _render("sessions.html")

# restore は job_id が必要なので文字列テンプレートとして保持
_RESTORE_PAGE_HTML = (
    Path(__file__).parent.parent / "templates" / "restore.html"
).read_text(encoding="utf-8")


# ─── 3d_view ─────────────────────────────────────────────────────────────────
def three_d_view_page(job_id: str, hands: list) -> str:
    filtered = []
    for h in hands:
        clf  = h.get("bluered_classification", {})
        line = clf.get("line", "")
        if line not in ("blue", "red"):
            continue
        filtered.append({
            "hand_number": h.get("hand_number"),
            "position":    h.get("hero_position", "?"),
            "line":        line,
            "category":    clf.get("category_label", "?"),
            "profit":      float(h.get("hero_result_bb", 0)),
            "is_3bet":     bool(h.get("is_3bet_pot", False)),
            "last_street": clf.get("last_street", "?"),
        })
    blue_count = sum(1 for h in filtered if h["line"] == "blue")
    red_count  = sum(1 for h in filtered if h["line"] == "red")
    hands_json = _json.dumps(filtered, ensure_ascii=False)
    return _render("3d_view.html",
        job_id=job_id,
        hands_json=hands_json,
        total_count=len(filtered),
        blue_count=blue_count,
        red_count=red_count,
    )


# ─── classify_result（Jinja2テンプレートに複雑なHTML断片を渡す） ─────────────
def classify_result_page(
    job_id: str,
    total_hands: int,
    blue_count: int,
    red_count: int,
    pf_count: int,
    categories: dict,
    allin_ev_diffs: dict,
    classified_path: str,
    json_path: str,
    hands: list = None,
) -> str:

    # カテゴリ行HTML（白背景グリッドスタイル）
    cat_rows = ""
    _CAT_CLS_MAP = {
        "バリュー/ブラフ成功": "cat-blue",
        "ブラフキャッチ": "cat-blue",
        "アグレッション勝利": "cat-blue",
        "ブラフ失敗": "cat-red",
        "コール負け": "cat-red",
        "バッドフォールド": "cat-red",
        "ナイスフォールド": "cat-gray",
        "フォールド(要確認)": "cat-warn",
        "プリフロップのみ": "cat-gray",
    }
    for label, count in sorted(categories.items(), key=lambda x: -x[1]):
        cc = _CAT_CLS_MAP.get(label, "cat-gray")
        cat_rows += f'<div class="cat-item {cc}"><span class="cat-label">{_esc(label)}</span><span class="cat-count">{count}</span></div>\n'

    # オールインEV差HTML（Heroのみ表示）
    ev_html = ""
    if allin_ev_diffs:
        player, diff = next(iter(allin_ev_diffs.items()))
        ev_count = sum(
            1 for h in (hands or [])
            for p in h.get("players", [])
            if p.get("is_hero") and h.get("result", {}).get("allin_ev", {})
        )
        sign = "+" if diff >= 0 else ""
        if diff > 0:
            ev_color = "#e94560"
            ev_verdict = "運が悪かった（EV より実収支が悪い）"
            ev_detail = f"Heroはオールインで期待値通りなら {sign}{diff:.2f}bb 多く取れていた"
        else:
            ev_color = "#4caf93"
            ev_verdict = "運が良かった（EV より実収支が良い）"
            ev_detail = f"Heroはオールインで期待値より {abs(diff):.2f}bb 多く得た"
        ev_color_txt = "#c0392b" if diff > 0 else "#2e7d32"
        ev_html = f"""
  <div class="summary-ev" style="padding:8px 20px;background:#fff;border-bottom:1px solid #e8e8e8;font-size:12px;color:#333">
    &#x1F3B2; All-in EV差 <strong style="color:{ev_color_txt}">{sign}{diff:.2f}bb</strong>
    <span style="color:#555">（{_esc(ev_verdict)}）</span>
    <span style="color:#888;font-size:11px">{_esc(ev_detail)}（{ev_count}手）</span>
  </div>"""

    # ─── 青線/赤線 ハンド一覧 ──────────────────────────────────────────────
    _SUIT_COLORS = {'♠': '#000000', '♥': '#e53935', '♦': '#1e88e5', '♣': '#43a047'}
    def _card_html(s):
        if not s: return ""
        def _r(m):
            c = _SUIT_COLORS.get(m.group(2), '#000')
            return f'{_esc(m.group(1))}<span style="color:{c}">{m.group(2)}</span>'
        return _re.sub(r'([23456789TJQKA]{1,2})([\u2660\u2665\u2666\u2663])', _r, str(s))

    def _fmt_bb(val):
        try:
            n = float(val)
            if n > 0: return f"+{n:.2f}"
            if n < 0: return f"{n:.2f}"
            return "0"
        except Exception: return "—"

    def _fmt_amt(v):
        try:
            n = float(v)
            if n <= 0: return ""
            return str(int(n)) if n == int(n) else f"{n:.2f}".rstrip('0').rstrip('.')
        except Exception:
            return ""

    def _fmt_actions(actions):
        parts = []
        for a in actions:
            pos = a.get("position") or a.get("name", "?")
            act = a.get("action", "")
            amt = a.get("amount_bb")
            _a  = _fmt_amt(amt)
            amt_s = f"&nbsp;{_a}bb" if _a else ""
            if act == "Fold":
                parts.append(f'<span class="act-fold">{pos}&nbsp;F</span>')
            elif act == "Check":
                parts.append(f'<span class="act-check">{pos}&nbsp;X</span>')
            elif act == "Call":
                parts.append(f'<span class="act-call">{pos}&nbsp;Call{amt_s}</span>')
            elif act == "Raise":
                parts.append(f'<span class="act-raise">{pos}&nbsp;Raise{amt_s}</span>')
            elif act == "Bet":
                parts.append(f'<span class="act-bet">{pos}&nbsp;Bet{amt_s}</span>')
            elif act:
                parts.append(f'<span>{pos}&nbsp;{act}</span>')
        sep = ' <span class="act-sep">›</span> '
        return sep.join(parts)

    _ST_JP = {"preflop": "PF", "flop": "F", "turn": "T", "river": "R"}
    _BLUE_ORDER = ["value_success", "bluff_catch", "bluff_failed", "call_lost"]
    _RED_ORDER  = ["hero_aggression_won", "bad_fold", "nice_fold", "fold_unknown"]

    _CAT_CLASS = {
        "value_success": "blue",
        "bluff_catch":            "blue",
        "bluff_failed":           "red",
        "call_lost":              "red",
        "hero_aggression_won":    "red",
        "bad_fold":               "red",
        "nice_fold":              "",
        "fold_unknown":           "warn",
    }

    def _build_hand_card(h):
        clf = h.get("bluered_classification", {})
        hero_cards = "".join(h.get("hero_cards", []))
        hero_pos   = h.get("hero_position", "?")
        is_3bet    = h.get("is_3bet_pot", False)
        pl         = float(h.get("hero_result_bb", 0))
        pl_cls     = "pos" if pl > 0 else "neg" if pl < 0 else "zero"
        needs_api  = clf.get("needs_api", False)

        badge_3bet = '<span class="badge-3bet">3BET</span> ' if is_3bet else ""
        badge_ai   = '<span class="badge-ai">★</span> ' if needs_api else ""
        card_cls   = "hand-card needs-ai" if needs_api else "hand-card"

        opp_parts      = []
        opp_data_parts = []
        for p in h.get("players", []):
            if not p.get("is_hero"):
                cards = "".join(p.get("hole_cards", []))
                pos   = p.get("position", "?")
                if cards:
                    opp_parts.append(f'<span class="opp-pos">{pos}</span>&nbsp;{_card_html(cards)}')
                else:
                    opp_parts.append(f'<span class="opp-pos">{pos}</span>')
                opp_data_parts.append(f"{pos}:{cards}")
        opp_html     = "&ensp;".join(opp_parts) if opp_parts else "—"
        opp_data_str = ",".join(opp_data_parts)

        hero_c_html = _card_html(hero_cards) if hero_cards else "—"

        streets = h.get("streets", {})
        st_lines = []

        pf = streets.get("preflop", [])
        if pf:
            acts = _fmt_actions(pf)
            if acts:
                st_lines.append(
                    f'<div class="street-line">'
                    f'<span class="street-label">PF</span>'
                    f'<span>{acts}</span></div>'
                )

        for st_key, st_lbl in [("flop","F"), ("turn","T"), ("river","R")]:
            s = streets.get(st_key)
            if not s or not isinstance(s, dict): continue
            board_cards = [c for c in s.get("board", []) if c and c != "-"]
            pot         = s.get("pot_bb", 0)
            actions     = s.get("actions", [])
            board_part  = f'<span class="board-cards">{_card_html(" ".join(board_cards))}</span> ' if board_cards else ""
            pot_part    = f'<span class="pot-label">({pot}bb)</span>'
            acts        = _fmt_actions(actions)
            line = (
                f'<div class="street-line">'
                f'<span class="street-label">{st_lbl}</span>'
                f'{board_part}{pot_part}'
            )
            if acts:
                line += f' <span>{acts}</span>'
            line += '</div>'
            st_lines.append(line)

        streets_html = "".join(st_lines)

        hnum = h.get("hand_number", "")
        line = h.get("bluered_classification", {}).get("line", "")
        na_attr = ' data-needs-api="1"' if needs_api else ""
        _cards_str = "".join(h.get("hero_cards", []))
        _pl_str    = f"{_fmt_bb(pl)}bb"
        _board_cards = []
        for _st in ("flop", "turn", "river"):
            _s = h.get("streets", {}).get(_st)
            if _s and isinstance(_s, dict):
                _board_cards.extend([c for c in _s.get("board", []) if c and c != "-"])
        _board_str = " ".join(_board_cards)
        data_attrs = (
            f' data-pos="{_esc(hero_pos)}"'
            f' data-cards="{_esc(_cards_str)}"'
            f' data-pl="{_esc(_pl_str)}"'
            f' data-pl-num="{pl:.2f}"'
            f' data-board="{_esc(_board_str)}"'
            f' data-opp="{_esc(opp_data_str)}"'
            + (' data-3bet="1"' if is_3bet else '')
        )
        cart_btn = (
            f'<button class="cart-add-btn" onclick="toggleCart({hnum})" '
            f'data-hnum="{hnum}" title="カートに追加/削除">🛒</button>'
            if line != "preflop_only" else ""
        )

        return (
            f'<div class="{card_cls}" data-hnum="{hnum}" data-line="{line}"{na_attr}{data_attrs}>'
            f'<div class="hand-card-head">'
            f'{badge_ai}'
            f'<span class="hand-num">H{hnum}</span>'
            f'{badge_3bet}'
            f'<span class="hero-pos">{hero_pos}</span>'
            f'<span class="hero-label">(Hero)</span>'
            f'<span class="hero-cards">{hero_c_html}</span>'
            f'<span class="vs-label">vs</span>'
            f'<span class="opp-cards">{opp_html}</span>'
            f'<span class="hand-pl {pl_cls}">{_fmt_bb(pl)}bb</span>'
            f'{cart_btn}'
            f'</div>'
            f'<div class="hand-card-body">{streets_html}</div>'
            f'<div class="hand-ai-inline" id="hai-{hnum}"></div>'
            f'</div>'
        )

    def _build_hand_section(filtered_hands, cat_order):
        html = ""
        for cat in cat_order:
            cat_hands = [h for h in filtered_hands
                         if h.get("bluered_classification", {}).get("category") == cat]
            cat_hands.sort(key=lambda h: h.get("hand_number", 0))
            if not cat_hands: continue
            cat_label = cat_hands[0].get("bluered_classification", {}).get("category_label", cat)
            cat_pl    = sum(float(h.get("hero_result_bb", 0)) for h in cat_hands)
            pl_cls    = "pos" if cat_pl > 0 else "neg" if cat_pl < 0 else ""
            cc        = _CAT_CLASS.get(cat, "")
            needs_api_cnt = sum(1 for h in cat_hands if h.get("bluered_classification", {}).get("needs_api"))
            ai_badge  = f' <span class="ai-badge">★ 要AI {needs_api_cnt}手</span>' if needs_api_cnt else ""
            pl_sign   = "+" if cat_pl > 0 else ""

            sub_cls = f"cat-subheader {cc}" if cc else "cat-subheader"
            html += (
                f'<div class="hand-cat-group" style="padding:0 10px">'
                f'<div class="{sub_cls}">'
                f'{_esc(cat_label)} <span style="font-weight:400;color:#555">{len(cat_hands)}手</span>'
                f'<span class="cat-sub-pl {pl_cls}">{pl_sign}{cat_pl:.2f}bb</span>'
                f'{ai_badge}</div>\n'
            )
            for h in cat_hands:
                html += _build_hand_card(h)
            html += '</div>\n'
        return html

    # ─── ポジション別統計 ────────────────────────────────────────────
    _POS_ORDER = ["UTG", "UTG+1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
    pos_stats_html = ""
    if hands:
        pos_map = {}
        for h in hands:
            pos = h.get("hero_position", "?")
            if pos not in pos_map:
                pos_map[pos] = {"total": 0, "pl": 0.0, "blue": 0, "red": 0, "pf": 0, "won": 0, "vpip": 0, "pfr": 0, "tbet": 0}
            s = pos_map[pos]
            s["total"] += 1
            s["pl"] += float(h.get("hero_result_bb", 0))
            line = h.get("bluered_classification", {}).get("line", "preflop_only")
            if line == "blue":   s["blue"] += 1
            elif line == "red":  s["red"] += 1
            else:                s["pf"] += 1
            winners = {w["name"] for w in h.get("result", {}).get("winners", [])}
            hero_name2 = next((p.get("name","") for p in h.get("players",[]) if p.get("is_hero")), "")
            if hero_name2 and hero_name2 in winners:
                s["won"] += 1
            pf_acts = h.get("streets", {}).get("preflop", [])
            hero_acts = [a for a in pf_acts if a.get("name") == hero_name2]
            if any(a.get("action") in ("Call","Raise") for a in hero_acts):
                s["vpip"] += 1
            if any(a.get("action") == "Raise" for a in hero_acts):
                s["pfr"] += 1
            if h.get("is_3bet_pot") and any(a.get("action") == "Raise" for a in hero_acts):
                s["tbet"] += 1

        rows_pos = ""
        ordered_pos = [p for p in _POS_ORDER if p in pos_map] + [p for p in pos_map if p not in _POS_ORDER]
        for pos in ordered_pos:
            s = pos_map[pos]
            n = s["total"]
            pl = s["pl"]
            pl_c = "#2e7d32" if pl > 0 else "#c0392b" if pl < 0 else "#888"
            pl_s = ("+" if pl > 0 else "") + f"{pl:.2f}"
            avg = pl / n
            avg_c = "#2e7d32" if avg > 0 else "#c0392b" if avg < 0 else "#888"
            avg_s = ("+" if avg > 0 else "") + f"{avg:.2f}"
            rows_pos += f"""<tr>
  <td style="font-weight:700">{_esc(pos)}</td>
  <td style="text-align:center">{n}</td>
  <td style="text-align:center">{s['vpip']/n*100:.0f}%</td>
  <td style="text-align:center">{s['pfr']/n*100:.0f}%</td>
  <td style="text-align:center">{s['tbet']/n*100:.0f}%</td>
  <td style="text-align:center"><span style="color:#1a6abf">{s['blue']}</span> / <span style="color:#c0392b">{s['red']}</span> / <span style="color:#888">{s['pf']}</span></td>
  <td style="text-align:right;color:{pl_c};font-weight:700">{pl_s}bb</td>
  <td style="text-align:right;color:{avg_c}">{avg_s}bb</td>
</tr>"""
        pos_stats_html = f"""<div style="padding:16px 20px 24px">
<table style="width:100%;border-collapse:collapse;font-size:12px">
  <thead><tr style="background:#f0f0f0">
    <th style="padding:7px 8px;text-align:left;border-bottom:2px solid #ddd">ポジション</th>
    <th style="padding:7px 8px;text-align:center;border-bottom:2px solid #ddd">手数</th>
    <th style="padding:7px 8px;text-align:center;border-bottom:2px solid #ddd">VPIP</th>
    <th style="padding:7px 8px;text-align:center;border-bottom:2px solid #ddd">PFR</th>
    <th style="padding:7px 8px;text-align:center;border-bottom:2px solid #ddd">3BET%</th>
    <th style="padding:7px 8px;text-align:center;border-bottom:2px solid #ddd">青/赤/PF</th>
    <th style="padding:7px 8px;text-align:right;border-bottom:2px solid #ddd">合計損益</th>
    <th style="padding:7px 8px;text-align:right;border-bottom:2px solid #ddd">平均損益/手</th>
  </tr></thead>
  <tbody>{rows_pos}</tbody>
</table>
<p style="font-size:10px;color:#aaa;margin-top:8px">VPIP=自発的投資率 / PFR=プリフロップレイズ率 / 3BET%=3BETポット参加率</p>
</div>"""

    # ─── チップ推移データ（JS用JSON） ───────────────────────────────
    chip_data_json = "[]"
    if hands:
        chip_sorted = sorted(hands, key=lambda h: h.get("hand_number", 0))
        cumulative = 0.0
        points = []
        for h in chip_sorted:
            cumulative += float(h.get("hero_result_bb", 0))
            points.append({
                "x": h.get("hand_number", 0),
                "y": round(cumulative, 2),
                "line": h.get("bluered_classification", {}).get("line", "preflop_only"),
            })
        chip_data_json = _json.dumps(points)

    hands_html = ""
    if hands:
        blue_hands = [h for h in hands if h.get("bluered_classification", {}).get("line") == "blue"]
        red_hands  = [h for h in hands if h.get("bluered_classification", {}).get("line") == "red"]
        pf_hands   = [h for h in hands if h.get("bluered_classification", {}).get("line") == "preflop_only"]
        blue_pl    = sum(float(h.get("hero_result_bb", 0)) for h in blue_hands)
        red_pl     = sum(float(h.get("hero_result_bb", 0)) for h in red_hands)
        blue_pl_c  = "pos" if blue_pl > 0 else "neg" if blue_pl < 0 else ""
        red_pl_c   = "pos" if red_pl  > 0 else "neg" if red_pl  < 0 else ""
        blue_section = _build_hand_section(blue_hands, _BLUE_ORDER)
        red_section  = _build_hand_section(red_hands,  _RED_ORDER)

        all_sorted = sorted(hands, key=lambda h: h.get("hand_number", 0))
        _LINE_BADGE = {
            "blue":         '<span class="badge-line-blue">青</span>',
            "red":          '<span class="badge-line-red">赤</span>',
            "preflop_only": '<span class="badge-line-pf">PF</span>',
        }
        all_rows = ""
        for h in all_sorted:
            clf      = h.get("bluered_classification", {})
            line     = clf.get("line", "preflop_only")
            pl       = float(h.get("hero_result_bb", 0))
            pl_color = "#2e7d32" if pl > 0 else "#c0392b" if pl < 0 else "#999"
            hero_pos = h.get("hero_position", "?")
            hero_c   = "".join(h.get("hero_cards", []))
            badge    = _LINE_BADGE.get(line, "")
            badge3   = '<span class="badge-3bet" style="font-size:9px;padding:1px 4px">3B</span> ' if h.get("is_3bet_pot") else ""
            opp_parts2 = []
            for p in h.get("players", []):
                if not p.get("is_hero"):
                    cards2 = "".join(p.get("hole_cards", []))
                    pos2   = p.get("position", "?")
                    if cards2:
                        opp_parts2.append(f'<span class="opp-pos">{pos2}</span>&nbsp;{_card_html(cards2)}')
                    else:
                        opp_parts2.append(f'<span class="opp-pos">{pos2}</span>')
            opp2 = "&ensp;".join(opp_parts2) if opp_parts2 else "—"
            pf_acts = _fmt_actions(h.get("streets", {}).get("preflop", []))
            all_rows += (
                f'<tr>'
                f'<td style="white-space:nowrap">{badge} H{h.get("hand_number","")}</td>'
                f'<td><span style="font-weight:700">{_esc(hero_pos)} (H)</span> {badge3}'
                f'{_card_html(hero_c) if hero_c else "—"}'
                f' <span style="color:#bbb;font-size:10px">vs</span> {opp2}</td>'
                f'<td style="font-size:10px">{pf_acts}</td>'
                f'<td style="text-align:right;color:{pl_color};font-weight:700;white-space:nowrap">{_fmt_bb(pl)}bb</td>'
                f'</tr>'
            )

        blue_pl_str = ("+" if blue_pl > 0 else "") + f"{blue_pl:.2f}"
        red_pl_str  = ("+" if red_pl  > 0 else "") + f"{red_pl:.2f}"

        hands_html = f"""
<div class="section">
  <div class="section-header" onclick="toggleSection('hand-list-body')">
    &#x1F4CB; 青線 / 赤線 ハンド一覧
    <span class="toggle-btn">&#x25B2;</span>
  </div>
  <div class="accordion-body" id="hand-list-body">
    <div class="line-header">
      <span class="line-title blue">&#x1F535; 青線（ショーダウン）</span>
      <span class="line-count">{len(blue_hands)}手</span>
      <span class="line-pl {blue_pl_c}">{blue_pl_str}bb</span>
    </div>
    <div id="blue-hands-area">{blue_section or '<div style="padding:8px 14px;color:#aaa;font-size:12px">該当なし</div>'}</div>
    <div class="line-header" style="border-top:2px solid #eee;margin-top:8px">
      <span class="line-title red">&#x1F534; 赤線（ノーショーダウン）</span>
      <span class="line-count">{len(red_hands)}手</span>
      <span class="line-pl {red_pl_c}">{red_pl_str}bb</span>
    </div>
    <div id="red-hands-area">{red_section or '<div style="padding:8px 14px;color:#aaa;font-size:12px">該当なし</div>'}</div>
  </div>
</div>

<div class="section">
  <div class="section-header" onclick="toggleSection('all-hands-body')">
    &#x1F5C2; 全ハンド一覧（{len(all_sorted)}手）
    <span class="toggle-btn">&#x25BC;</span>
  </div>
  <div class="accordion-body collapsed" id="all-hands-body">
    <div class="section-body" style="overflow-x:auto">
      <table class="all-hands-table">
        <thead><tr>
          <th>分類 / H#</th>
          <th>ポジション / ホールカード</th>
          <th>PFアクション</th>
          <th style="text-align:right">損益(bb)</th>
        </tr></thead>
        <tbody>{all_rows}</tbody>
      </table>
    </div>
  </div>
</div>"""

    from jinja2 import Environment, FileSystemLoader as _FSL
    _env = Environment(loader=_FSL(_TEMPLATES_DIR), autoescape=False)
    return _env.get_template('classify_result.html').render(
        job_id=job_id,
        total_hands=total_hands,
        blue_count=blue_count,
        red_count=red_count,
        pf_count=pf_count,
        ev_html=ev_html,
        cat_rows=cat_rows,
        hands_html=hands_html,
        pos_stats_html=pos_stats_html,
        chip_data_json=chip_data_json,
        classified_path=classified_path,
        json_path=json_path,
    )
