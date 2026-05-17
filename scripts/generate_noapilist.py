"""
generate_noapilist.py - 青線/赤線分類レポート PDF 生成（APIなしモード）
使用法: python scripts/generate_noapilist.py <output_dir> <classified.json>
"""
import sys
import json
import re
from pathlib import Path
from datetime import date as date_cls

# ─── ユーティリティ ─────────────────────────────────────────────────────────────

SUIT_COLORS = {"♠": "#000000", "♥": "#E00000", "♦": "#0055CC", "♣": "#007700"}

def esc(s):
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def card_to_html(card_str):
    if not card_str:
        return ""
    def repl(m):
        rank, suit = m.group(1), m.group(2)
        color = SUIT_COLORS.get(suit, "#000")
        return f'{esc(rank)}<span style="color:{color}">{suit}</span>'
    return re.sub(r'([23456789TJQKA]{1,2})([♠♥♦♣])', repl, str(card_str))

def fmt_bb(val):
    try:
        n = float(val)
    except (TypeError, ValueError):
        return "—"
    if n > 0:
        return f"+{n:.2f}bb"
    if n < 0:
        return f"{n:.2f}bb"
    return "0bb"

def fmt_date_jp(iso_str):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', iso_str or "")
    return f"{m.group(1)}年{m.group(2)}月{m.group(3)}日" if m else (iso_str or "")

def pl_color(val):
    try:
        n = float(val)
    except (TypeError, ValueError):
        return "#666"
    return "#27ae60" if n > 0 else "#e74c3c" if n < 0 else "#666"

# ─── ハンド情報ヘルパー ──────────────────────────────────────────────────────────

def get_hero_name(hand):
    return next((p.get('name', '') for p in (hand.get('players') or []) if p.get('is_hero')), '')

def get_all_opp_cards(hand):
    return [
        {'pos': p.get('position', '?'), 'cards': ''.join(p.get('hole_cards') or [])}
        for p in (hand.get('players') or []) if not p.get('is_hero')
    ]

def fmt_actions_html(actions):
    if not actions:
        return ''
    parts = []
    for a in actions:
        pos = a.get('position') or a.get('name') or '?'
        act = a.get('action', '')
        amt = f" {a['amount_bb']}bb" if a.get('amount_bb') is not None else ''
        if act == 'Fold':
            parts.append(f'<span style="color:#777">{esc(pos)} F</span>')
        elif act == 'Check':
            parts.append(f'<span style="color:#555">{esc(pos)} X</span>')
        elif act == 'Call':
            parts.append(f'<span style="color:#0044AA">{esc(pos)} Call{esc(amt)}</span>')
        elif act in ('Bet', 'Raise'):
            parts.append(f'<span style="color:#884400;font-weight:bold">{esc(pos)} {esc(act)}{esc(amt)}</span>')
        elif act:
            parts.append(f'<span style="color:#444">{esc(pos)} {esc(act)}</span>')
    return ' <span style="color:#999">&rsaquo;</span> '.join(parts)

def build_action_flow_html(hand):
    streets = hand.get('streets') or {}
    lines = []
    pf = streets.get('preflop') or []
    pf_html = fmt_actions_html(pf)
    if pf_html:
        lines.append(f'<span style="color:#444;font-size:5.5pt;font-weight:bold">PF</span> {pf_html}')
    for key, lbl in [('flop', 'F'), ('turn', 'T'), ('river', 'R')]:
        s = streets.get(key)
        if not s or not isinstance(s, dict):
            continue
        board_cards = [c for c in (s.get('board') or []) if c and c != '-']
        board_html  = card_to_html(' '.join(board_cards)) if board_cards else ''
        pot_bb      = s.get('pot_bb')
        pot_html    = f'<span style="color:#666;font-size:5pt">({pot_bb}bb)</span>' if pot_bb else ''
        acts_html   = fmt_actions_html(s.get('actions') or [])
        line = f'<span style="color:#444;font-size:5.5pt;font-weight:bold">{lbl}</span>'
        if board_html: line += f' {board_html}'
        if pot_html:   line += f' {pot_html}'
        if acts_html:  line += f' {acts_html}'
        lines.append(line)
    return '<br>'.join(lines)

def get_hero_ev(hand):
    name  = get_hero_name(hand)
    allin = (hand.get('result') or {}).get('allin_ev')
    if not name or not allin:
        return None
    ev = allin.get(name)
    try:
        return float(ev) if ev is not None else None
    except (TypeError, ValueError):
        return None

# ─── 統計計算 ──────────────────────────────────────────────────────────────────

def is_hero_action(hand, action):
    return any(p.get('is_hero') and p.get('name') == action.get('name')
               for p in (hand.get('players') or []))

def is_hero_name(hand, name):
    return any(p.get('is_hero') and p.get('name') == name
               for p in (hand.get('players') or []))

def calc_ev(hands):
    total, count = 0.0, 0
    for h in hands:
        ev = get_hero_ev(h)
        if ev is not None:
            total += ev
            count += 1
    return total if count > 0 else None

def calc_position_stats(hands):
    ORDER = ['UTG', 'UTG+1', 'LJ', 'HJ', 'CO', 'BTN', 'SB', 'BB']
    stats = {}
    for pos in ORDER:
        ph = [h for h in hands if h.get('hero_position') == pos]
        if not ph:
            continue
        vpip = pfr = tb = won = 0
        pl = 0.0
        for h in ph:
            acts = [a for a in (h.get('streets') or {}).get('preflop', []) if is_hero_action(h, a)]
            if any(a.get('action') in ('Call', 'Raise') for a in acts): vpip += 1
            if any(a.get('action') == 'Raise' for a in acts): pfr += 1
            if h.get('is_3bet_pot') and sum(1 for a in acts if a.get('action') == 'Raise') >= 1: tb += 1
            if any(is_hero_name(h, w.get('name')) for w in (h.get('result') or {}).get('winners', [])): won += 1
            pl += h.get('hero_result_bb') or 0
        n = len(ph)
        stats[pos] = {
            'hands': n,
            'vpip':      f"{vpip/n*100:.1f}",
            'pfr':       f"{pfr/n*100:.1f}",
            'three_bet': f"{tb/n*100:.1f}",
            'win_rate':  f"{won/n*100:.1f}",
            'total_pl':  f"{pl:.2f}",
            'avg_pl':    f"{pl/n:.2f}",
        }
    return stats

# ─── 定数 ──────────────────────────────────────────────────────────────────────

BLUE_CAT_ORDER = ['value_success', 'bluff_catch', 'bluff_failed', 'call_lost']
RED_CAT_ORDER  = ['hero_aggression_won', 'bad_fold', 'nice_fold', 'fold_unknown']
STREET_ORDER   = ['preflop', 'flop', 'turn', 'river']

CAT_CSS = {
    'value_success':       'cat-value',
    'bluff_catch':         'cat-catch',
    'bluff_failed':        'cat-bluff',
    'call_lost':           'cat-call',
    'hero_aggression_won': 'cat-agg',
    'bad_fold':            'cat-bad',
    'nice_fold':           'cat-nice',
    'fold_unknown':        'cat-unknown',
}

# ─── CSS ───────────────────────────────────────────────────────────────────────

def build_css():
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'IPAGothic', 'Noto Sans JP', 'Meiryo', sans-serif;
  font-size: 8.5pt; color: #222; background: #fff;
  padding: 6mm 8mm 8mm 8mm;
}
.header-band {
  border: 1px solid #2E4057; border-left: 5px solid #2E4057;
  border-radius: 0 3px 3px 0; padding: 2mm 3mm;
  margin-bottom: 4mm; background: #fafbfc;
}
.header-row1 {
  display: flex; align-items: baseline; gap: 6mm;
  border-bottom: 1px solid #dde; padding-bottom: 1.5mm; margin-bottom: 1.5mm;
}
.header-title { font-size: 10pt; font-weight: 700; color: #2E4057; }
.header-meta  { font-size: 7pt; color: #666; display: flex; gap: 8mm; flex-wrap: wrap; }
.header-meta span { white-space: nowrap; }
.header-row2  { display: flex; gap: 3mm; }
.hstat {
  flex: 1; display: flex; align-items: center; gap: 3mm;
  padding: 1mm 2mm; border-radius: 2px;
}
.hstat.total { background: #f0f0f0; }
.hstat.blue  { background: #eef2ff; border-left: 3px solid #0055CC; }
.hstat.red   { background: #fff0f0; border-left: 3px solid #E00000; }
.hstat-lbl { font-size: 6.5pt; color: #666; white-space: nowrap; }
.hstat-val { font-size: 9pt; font-weight: 700; white-space: nowrap; }
.hstat-sub { font-size: 6pt; color: #888; white-space: nowrap; }
.api-notice {
  font-size: 6.5pt; color: #7a5f00; margin-left: auto;
  background: #fffbea; padding: 0.5mm 2mm; border-radius: 2px;
  align-self: center; white-space: nowrap;
}
.section-title {
  font-size: 10pt; font-weight: 700;
  background: #2E4057; color: #fff;
  padding: 1.5mm 4mm; margin-top: 5mm; margin-bottom: 2mm;
}
.flow-2col { column-count: 2; column-gap: 4mm; margin-bottom: 4mm; }
.flow-section-label {
  column-span: none; break-before: avoid;
  font-size: 7.5pt; color: #555;
  margin: 2mm 0 1mm 0;
  border-left: 3px solid #0055CC; padding-left: 2mm;
}
.flow-section-label.red-label { border-color: #E00000; }
.hand-card {
  break-inside: avoid; margin-bottom: 1.5mm;
  border-radius: 2px; overflow: hidden;
}
.hand-card.card-blue { background: #f0f4ff; border-left: 2px solid #0055CC; }
.hand-card.card-red  { background: #fff4f0; border-left: 2px solid #E00000; }
.hand-card.card-api  { background: #fffbea; border-left: 2px solid #d97706; }
.hand-top {
  display: flex; align-items: baseline; gap: 2mm;
  padding: 1.5pt 3pt 0.5pt 3pt; font-size: 6pt;
}
.hand-num  { color: #888; white-space: nowrap; flex-shrink: 0; }
.hand-info { flex: 1; min-width: 0; overflow: hidden; }
.hand-pl   { white-space: nowrap; flex-shrink: 0; font-weight: bold; }
.hand-flow {
  font-size: 5.5pt; color: #333; line-height: 1.7;
  padding: 1pt 4pt 2pt 4pt; background: rgba(0,0,0,0.03);
}
.cat-card {
  break-inside: avoid; margin: 2mm 0 0.5mm 0;
  font-size: 6.5pt; font-weight: 700;
  background: #eef0f4; padding: 2pt 3pt; border: 1px solid #bbb;
}
.data-table {
  width: 100%; border-collapse: collapse;
  table-layout: fixed; margin-bottom: 4mm;
}
.data-table th {
  background: #2E4057; color: #fff;
  padding: 1.5pt 2pt; text-align: center;
  font-weight: 700; border: 1px solid #1e2e40; white-space: nowrap;
}
.data-table td { padding: 1.5pt 2pt; border: 1px solid #ccc; vertical-align: middle; }
.cat-badge {
  display: inline-block; font-size: 5.5pt; font-weight: bold;
  padding: 0px 3px; border-radius: 2px;
}
.cat-value   { background: #dff0d8; color: #2d6a4f; }
.cat-catch   { background: #cce5ff; color: #004085; }
.cat-bluff   { background: #f8d7da; color: #721c24; }
.cat-call    { background: #f8d7da; color: #721c24; }
.cat-agg     { background: #d4edda; color: #155724; }
.cat-bad     { background: #f8d7da; color: #721c24; }
.cat-nice    { background: #dff0d8; color: #2d6a4f; }
.cat-unknown { background: #fff3cd; color: #856404; }
.pl-pos { color: #27ae60; font-weight: bold; }
.pl-neg { color: #e74c3c; font-weight: bold; }
.api-flag { color: #d97706; font-weight: bold; font-size: 6pt; }
.pos-table { font-size: 7.5pt; }
.pos-table th { font-size: 7pt; }
.pos-table td { font-size: 7pt; padding: 2pt 3pt; }
.all-tbl { font-size: 6pt; margin-bottom: 4mm; }
.all-tbl th {
  background: #2E4057; color: #fff;
  padding: 1.5pt 3pt; font-weight: 700; border: 1px solid #1e2e40;
  white-space: nowrap; text-align: left;
}
.all-tbl td { padding: 1.5pt 3pt; border: 1px solid #ccc; vertical-align: middle; }
.all-tbl tr:nth-child(even) td { background: #f5f6f8; }
.badge-blue { background:#ddeeff; color:#003399; font-weight:bold; padding:0 3px; border-radius:2px; }
.badge-red  { background:#ffdddd; color:#990000; font-weight:bold; padding:0 3px; border-radius:2px; }
.badge-pf   { background:#f0f0f0; color:#666; padding:0 3px; border-radius:2px; }
.badge-3b   { background:#ede0ff; color:#5b00d6; font-weight:bold; padding:0 3px; border-radius:2px; font-size:4.5pt; }
.all-pos     { color:#333; font-weight:bold; }
.all-opp-pos { color:#555; font-weight:bold; }
"""

# ─── セクション1: ヘッダーバンド ────────────────────────────────────────────────

def build_section1_html(hands, min_date, max_date):
    min_jp = fmt_date_jp(min_date + "T00:00:00")
    max_jp = fmt_date_jp(max_date + "T00:00:00")
    date_label = min_jp if min_date == max_date else f"{min_jp} 〜 {max_jp}"

    total_pl = sum(h.get('hero_result_bb') or 0 for h in hands)
    total_ev = calc_ev(hands)
    ev_diff  = total_pl - total_ev if total_ev is not None else None

    blue_hands = [h for h in hands if (h.get('bluered_classification') or {}).get('line') == 'blue']
    red_hands  = [h for h in hands if (h.get('bluered_classification') or {}).get('line') == 'red']
    blue_pl    = sum(h.get('hero_result_bb') or 0 for h in blue_hands)
    red_pl     = sum(h.get('hero_result_bb') or 0 for h in red_hands)
    blue_ev    = calc_ev(blue_hands)
    red_ev     = calc_ev(red_hands)
    needs_api  = sum(1 for h in hands if (h.get('bluered_classification') or {}).get('needs_api'))

    ev_total_str = ''
    if total_ev is not None:
        ev_total_str = (f'EV: <span style="color:{pl_color(total_ev)}">{fmt_bb(total_ev)}</span>'
                        f' / 差: <span style="color:{pl_color(ev_diff)}">{fmt_bb(ev_diff)}</span>')
    blue_ev_str = (f'<span class="hstat-sub">EV {fmt_bb(blue_ev)} / 差 {fmt_bb(blue_pl - blue_ev)}</span>'
                   if blue_ev is not None else '')
    red_ev_str  = (f'<span class="hstat-sub">EV {fmt_bb(red_ev)} / 差 {fmt_bb(red_pl - red_ev)}</span>'
                   if red_ev is not None else '')
    api_note    = f'<span class="api-notice">★要AI: {needs_api}手</span>' if needs_api > 0 else ''
    ev_sub_html = f'<span class="hstat-sub">{ev_total_str}</span>' if ev_total_str else ''

    return f"""<div class="header-band">
  <div class="header-row1">
    <span class="header-title">ポーカー 青線/赤線 分類レポート</span>
    <div class="header-meta">
      <span>{esc(date_label)}</span>
      <span>総ハンド数: <strong>{len(hands)}</strong></span>
    </div>
  </div>
  <div class="header-row2">
    <div class="hstat total">
      <span class="hstat-lbl">実収支</span>
      <span class="hstat-val" style="color:{pl_color(total_pl)}">{fmt_bb(total_pl)}</span>
      {ev_sub_html}
    </div>
    <div class="hstat blue">
      <span class="hstat-lbl">青線 {len(blue_hands)}手</span>
      <span class="hstat-val" style="color:{pl_color(blue_pl)}">{fmt_bb(blue_pl)}</span>
      {blue_ev_str}
    </div>
    <div class="hstat red">
      <span class="hstat-lbl">赤線 {len(red_hands)}手</span>
      <span class="hstat-val" style="color:{pl_color(red_pl)}">{fmt_bb(red_pl)}</span>
      {red_ev_str}
    </div>
    {api_note}
  </div>
</div>"""

# ─── セクション2/3: 青線/赤線カード ────────────────────────────────────────────

def build_grouped_cards(filtered_hands, cat_order, line_key):
    card_line_cls = 'card-blue' if line_key == 'blue' else 'card-red'
    parts = []
    for cat in cat_order:
        cat_hands = sorted(
            [h for h in filtered_hands
             if (h.get('bluered_classification') or {}).get('category') == cat],
            key=lambda h: (
                0 if h.get('is_3bet_pot') else 1,
                STREET_ORDER.index((h.get('bluered_classification') or {}).get('last_street', 'preflop'))
                if (h.get('bluered_classification') or {}).get('last_street', 'preflop') in STREET_ORDER else 0,
                h.get('hand_number') or 0,
            )
        )
        if not cat_hands:
            continue
        clf0      = cat_hands[0].get('bluered_classification') or {}
        cat_label = clf0.get('category_label') or cat
        cat_pl    = sum(h.get('hero_result_bb') or 0 for h in cat_hands)
        cat_css   = CAT_CSS.get(cat, '')

        parts.append(f"""<div class="cat-card">
  <span class="cat-badge {cat_css}">{esc(cat_label)}</span>
  &nbsp;{len(cat_hands)}手
  <span style="float:right;color:{pl_color(cat_pl)}">{fmt_bb(cat_pl)}</span>
</div>""")

        for h in cat_hands:
            clf      = h.get('bluered_classification') or {}
            pl_num   = h.get('hero_result_bb') or 0
            pl_cls   = 'pl-pos' if pl_num > 0 else 'pl-neg' if pl_num < 0 else ''
            card_cls = 'card-api' if clf.get('needs_api') else card_line_cls
            badge3   = '<span class="cat-badge" style="background:#ede0ff;color:#5b00d6;font-size:5pt">3B</span> ' if h.get('is_3bet_pot') else ''
            api_mark = '<span class="api-flag">★</span>' if clf.get('needs_api') else ''

            opp_html_parts = []
            for oc in get_all_opp_cards(h):
                opp_html_parts.append(
                    f'<span class="opp-pos" style="font-size:5pt;color:#555;font-weight:bold">{esc(oc["pos"])}</span>'
                    + (card_to_html(oc['cards']) if oc['cards'] else '<span style="color:#bbb">—</span>')
                )
            opp_html       = '&ensp;'.join(opp_html_parts) or '<span style="color:#bbb">—</span>'
            hero_cards_html = card_to_html(''.join(h.get('hero_cards') or [])) or '<span style="color:#bbb">—</span>'
            flow_html       = build_action_flow_html(h)

            parts.append(f"""<div class="hand-card {card_cls}">
  <div class="hand-top">
    <span class="hand-num">{api_mark}H{esc(h.get('hand_number'))}</span>
    <span class="hand-info">
      {badge3}<strong>{esc(h.get('hero_position') or '?')}</strong><span style="color:#888;font-size:5pt">(H)</span>
      {hero_cards_html}
      <span style="color:#bbb;font-size:5pt">vs</span>
      {opp_html}
    </span>
    <span class="hand-pl {pl_cls}">{esc(fmt_bb(pl_num))}</span>
  </div>
  <div class="hand-flow">{flow_html}</div>
</div>""")
    return ''.join(parts)

def build_section2_and3_html(hands):
    blue_hands    = [h for h in hands if (h.get('bluered_classification') or {}).get('line') == 'blue']
    red_hands     = [h for h in hands if (h.get('bluered_classification') or {}).get('line') == 'red']
    blue_pl       = sum(h.get('hero_result_bb') or 0 for h in blue_hands)
    red_pl        = sum(h.get('hero_result_bb') or 0 for h in red_hands)
    needs_api_cnt = sum(1 for h in red_hands if (h.get('bluered_classification') or {}).get('needs_api'))
    blue_cards    = build_grouped_cards(blue_hands, BLUE_CAT_ORDER, 'blue')
    red_cards     = build_grouped_cards(red_hands,  RED_CAT_ORDER,  'red')
    no_data       = '<div style="text-align:center;color:#aaa;font-size:6pt;padding:4pt">該当なし</div>'

    return f"""<h2 class="section-title">&#9312; 青線 / 赤線 ハンド詳細</h2>
<div class="flow-2col">
  <div class="flow-section-label">
    青線 {len(blue_hands)}手 &nbsp;
    実収支: <strong style="color:{pl_color(blue_pl)}">{fmt_bb(blue_pl)}</strong>
  </div>
  {blue_cards or no_data}
  <div class="flow-section-label red-label">
    赤線 {len(red_hands)}手 &nbsp;
    実収支: <strong style="color:{pl_color(red_pl)}">{fmt_bb(red_pl)}</strong>
    &nbsp; ★要AI: {needs_api_cnt}
  </div>
  {red_cards or no_data}
</div>"""

# ─── 全ハンド一覧 ───────────────────────────────────────────────────────────────

def build_all_hands_section(hands):
    LINE_BADGE = {
        'blue':         '<span class="badge-blue">青</span>',
        'red':          '<span class="badge-red">赤</span>',
        'preflop_only': '<span class="badge-pf">PF</span>',
    }
    rows = []
    for h in hands:
        clf    = h.get('bluered_classification') or {}
        line   = clf.get('line') or 'preflop_only'
        pl_num = h.get('hero_result_bb') or 0
        pl_cls = 'pl-pos' if pl_num > 0 else 'pl-neg' if pl_num < 0 else ''
        badge  = LINE_BADGE.get(line) or f'<span class="badge-pf">{esc(line)}</span>'
        badge3 = ' <span class="badge-3b">3BET</span>' if h.get('is_3bet_pot') else ''

        hero_cards_html = card_to_html(''.join(h.get('hero_cards') or [])) or '<span style="color:#aaa">—</span>'
        hero_pos = h.get('hero_position') or '?'

        opp_parts = []
        for p in (h.get('players') or []):
            if p.get('is_hero'):
                continue
            cards = ''.join(p.get('hole_cards') or [])
            pos   = p.get('position') or '?'
            opp_parts.append(
                f'<span class="all-opp-pos">{esc(pos)}</span>&nbsp;'
                + (card_to_html(cards) if cards else '<span style="color:#aaa">—</span>')
            )
        opp_html = '&ensp;'.join(opp_parts) or '<span style="color:#aaa">—</span>'
        pf_acts  = fmt_actions_html((h.get('streets') or {}).get('preflop') or [])

        rows.append(f"""<tr>
  <td style="text-align:center;white-space:nowrap">{badge}{badge3} H{esc(h.get('hand_number'))}</td>
  <td style="white-space:nowrap">
    <span class="all-pos">{esc(hero_pos)}</span>&nbsp;<span style="color:#666;font-size:5pt">(H)</span>&nbsp;{hero_cards_html}
    &ensp;<span style="color:#aaa">vs</span>&ensp;{opp_html}
  </td>
  <td>{pf_acts or '<span style="color:#aaa">—</span>'}</td>
  <td style="text-align:right;white-space:nowrap" class="{pl_cls}">{fmt_bb(pl_num)}</td>
</tr>""")

    no_data   = '<tr><td colspan="4" style="text-align:center;color:#aaa;padding:4pt">データなし</td></tr>'
    rows_html = ''.join(rows) or no_data

    return f"""<h2 class="section-title">&#9313; 全ハンド一覧（{len(hands)}手）</h2>
<table class="all-tbl data-table">
  <colgroup>
    <col style="width:9%"><col style="width:52%"><col style="width:27%"><col style="width:12%">
  </colgroup>
  <thead><tr>
    <th>分類 / H#</th><th>ポジション / ホールカード</th>
    <th>PFアクション</th><th style="text-align:right">損益(bb)</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""

# ─── ポジション別成績 ────────────────────────────────────────────────────────────

def build_section3_html(hands):
    pos_stats = calc_position_stats(hands)
    hdrs = ['ポジション', 'ハンド数', 'VPIP', 'PFR', '3BET%', '勝率', '合計損益(bb)', '平均損益(bb)']
    ws   = ['13%', '11%', '10%', '10%', '10%', '10%', '18%', '18%']

    rows = []
    for pos, s in pos_stats.items():
        pl_num  = float(s['total_pl'])
        avg_num = float(s['avg_pl'])
        rows.append(f"""<tr>
  <td style="font-weight:700">{esc(pos)}</td>
  <td style="text-align:center">{s['hands']}</td>
  <td style="text-align:center">{s['vpip']}%</td>
  <td style="text-align:center">{s['pfr']}%</td>
  <td style="text-align:center">{s['three_bet']}%</td>
  <td style="text-align:center">{s['win_rate']}%</td>
  <td style="text-align:right;color:{pl_color(pl_num)}">{s['total_pl']}</td>
  <td style="text-align:right;color:{pl_color(avg_num)}">{s['avg_pl']}</td>
</tr>""")

    col_html  = ''.join(f'<col style="width:{w}">' for w in ws)
    th_html   = ''.join(f'<th>{esc(h)}</th>' for h in hdrs)
    rows_html = ''.join(rows) or '<tr><td colspan="8" style="text-align:center;color:#aaa">データなし</td></tr>'

    return f"""<h2 class="section-title">&#9314; プリフロップ別成績</h2>
<table class="data-table pos-table">
  <colgroup>{col_html}</colgroup>
  <thead><tr>{th_html}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""

# ─── HTML組み立て・PDF生成 ──────────────────────────────────────────────────────

def build_full_html(sections):
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <style>{build_css()}</style>
</head>
<body>
{''.join(sections)}
</body>
</html>"""

def generate_pdf(html, out_file):
    from weasyprint import HTML
    HTML(string=html).write_pdf(out_file)

# ─── メイン ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/generate_noapilist.py <output_dir> <classified.json>",
              file=sys.stderr)
        sys.exit(1)

    output_dir = Path(sys.argv[1])
    json_path  = Path(sys.argv[2])

    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    hands = sorted(data.get('hands') or [], key=lambda h: h.get('datetime') or '')
    if not hands:
        print("[ERROR] ハンドデータが空です", file=sys.stderr)
        sys.exit(1)

    today    = date_cls.today().isoformat()
    dates    = [h['datetime'][:10] for h in hands if h.get('datetime') and len(h['datetime']) >= 10]
    min_date = min(dates) if dates else today
    max_date = max(dates) if dates else today
    all_sorted = sorted(hands, key=lambda h: h.get('hand_number') or 0)

    html = build_full_html([
        build_section1_html(hands, min_date, max_date),
        build_section2_and3_html(hands),
        build_all_hands_section(all_sorted),
        build_section3_html(hands),
    ])

    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = min_date if min_date == max_date else f"{min_date}_{max_date}"
    out_file = output_dir / f"NoAPI_Report_{date_str}.pdf"

    print("  PDF生成中...")
    generate_pdf(html, str(out_file))
    print(f"  Generated: {out_file}")

if __name__ == '__main__':
    main()
