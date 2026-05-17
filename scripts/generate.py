#!/usr/bin/env python3
"""generate.py - GTO分析レポート PDF 生成
使用法: python scripts/generate.py <output_dir> <data1.json> [data2.json ...]
"""
import sys
import json
import re
import html as html_module
from pathlib import Path
from datetime import date

SUIT_COLORS = {"♠": "#000000", "♥": "#E00000", "♦": "#0055CC", "♣": "#007700"}

BLUE_CAT_ORDER = ["value_success", "bluff_catch", "bluff_failed", "call_lost"]
RED_CAT_ORDER  = ["hero_aggression_won", "bad_fold", "nice_fold", "fold_unknown"]
ALL_CAT_ORDER  = BLUE_CAT_ORDER + RED_CAT_ORDER
STREET_ORDER   = ["preflop", "flop", "turn", "river"]
CAT_LABELS = {
    "value_success":       "[青] バリュー成功",
    "bluff_catch":         "[青] ブラフキャッチ",
    "bluff_failed":        "[青] ブラフ失敗",
    "call_lost":           "[青] コール負け",
    "hero_aggression_won": "[赤] アグレッション勝利",
    "bad_fold":            "[赤] バッドフォールド",
    "nice_fold":           "[赤] ナイスフォールド",
    "fold_unknown":        "[赤] フォールド(要確認)",
}
CAT_BG = {
    "value_success": "#E0FFE0", "bluff_catch": "#E0F0FF",
    "bluff_failed": "#FFE0E0", "call_lost": "#FFE0E0",
    "hero_aggression_won": "#E0FFE0", "bad_fold": "#FFE0E0",
    "nice_fold": "#EEFFEE", "fold_unknown": "#FFF8E0",
}


def sort_by_category(hands):
    def key(h):
        cat    = (h.get("bluered_classification") or {}).get("category", "")
        ia     = ALL_CAT_ORDER.index(cat) if cat in ALL_CAT_ORDER else 999
        street = (h.get("bluered_classification") or {}).get("last_street", "preflop")
        sa     = STREET_ORDER.index(street) if street in STREET_ORDER else 0
        return (ia, sa, h.get("hand_number", 0))
    return sorted(hands, key=key)


def esc(s):
    return html_module.escape(str(s) if s is not None else "")


def card_to_html(card_str):
    if not card_str:
        return ""
    def repl(m):
        rank, suit = m.group(1), m.group(2)
        return f'{esc(rank)}<span style="color:{SUIT_COLORS.get(suit, "#000")}">{suit}</span>'
    return re.sub(r'([23456789TJQKA]{1,2})([♠♥♦♣])', repl, str(card_str))


def fmt_bb(val):
    try:
        n = float(val)
    except (TypeError, ValueError):
        return "0bb"
    if n > 0:
        return f"+{n:.2f}bb"
    if n < 0:
        return f"{n:.2f}bb"
    return "0bb"


def fmt_time(iso_str):
    m = re.search(r'T(\d{2}:\d{2})', iso_str or "")
    return m.group(1) if m else ""


def fmt_date_jp(iso_str):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', iso_str or "")
    return f"{m.group(1)}年{m.group(2)}月{m.group(3)}日" if m else (iso_str or "")


def is_hero_action(hand, action):
    return any(
        p.get("is_hero") and p.get("name") == action.get("name")
        for p in (hand.get("players") or [])
    )


def make_action_summary(hand, street):
    s = (hand.get("streets") or {}).get(street)
    if not s:
        return ""
    acts = s if street == "preflop" else (s.get("actions") or [])
    parts = []
    for a in acts:
        pfx = "H" if is_hero_action(hand, a) else "V"
        amt = f" {a['amount_bb']}bb" if a.get("amount_bb") is not None else ""
        parts.append(f"{pfx}:{a.get('action', '')}{amt}")
    return " ".join(parts)


def get_board_card(hand, street, idx):
    board = ((hand.get("streets") or {}).get(street) or {}).get("board") or []
    return board[idx] if idx < len(board) else ""


def get_opponent_cards(hand):
    others = [p for p in (hand.get("players") or []) if not p.get("is_hero")]
    if not others:
        return ""
    winners = [(w.get("name") or "") for w in ((hand.get("result") or {}).get("winners") or [])]
    opp = next((p for p in others if p.get("name") in winners), others[0])
    return "".join(opp.get("hole_cards") or [])


def get_gto_rating(ev):
    if not ev:
        return ""
    for line in ev.split("\n"):
        l = line.strip()
        if l.startswith("GTO評価:"):
            return l[len("GTO評価:"):].strip()
    return ev


def gto_full_display(ev):
    if not ev:
        return ""
    parts = []
    for line in ev.split("\n"):
        l = line.strip()
        if not l:
            continue
        l = re.sub(r'^GTO評価:\s*', '', l)
        l = re.sub(r'^一言:\s*', '', l)
        l = re.sub(r'^詳細:\s*', '', l)
        l = re.sub(r'^改善:\s*', '改善: ', l)
        l = re.sub(r'^EV損失推定:\s*', 'EV: ', l)
        parts.append(l)
    return " / ".join(parts)


def build_css():
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'IPAGothic', 'Noto Sans JP', 'Meiryo', sans-serif;
  font-size: 9pt;
  color: #222;
  background: #fff;
  padding: 15mm 15mm 15mm 15mm;
}
h1.report-title {
  font-size: 20pt; font-weight: 700;
  text-align: center;
  border-bottom: 3px solid #2E4057;
  padding-bottom: 4mm;
  margin-bottom: 6mm;
}
.report-meta {
  text-align: center;
  font-size: 10pt;
  color: #555;
  margin-bottom: 8mm;
}
.report-meta span { margin: 0 8px; }
.section-title {
  font-size: 13pt; font-weight: 700;
  background: #2E4057; color: #fff;
  padding: 3mm 5mm;
  margin-top: 10mm;
  margin-bottom: 4mm;
}
.section-sub { font-size: 8pt; color: #555; margin-bottom: 3mm; }
.data-table {
  width: 100%; border-collapse: collapse;
  table-layout: fixed; font-size: 8pt;
  margin-bottom: 6mm;
}
.data-table th {
  background: #2E4057; color: #fff;
  padding: 2pt 3pt; text-align: center;
  font-weight: 700; border: 1px solid #1e2e40;
  white-space: nowrap; font-size: 7.5pt;
}
.data-table td {
  padding: 2pt 3pt; border: 1px solid #ccc;
  vertical-align: top; overflow: hidden;
  font-size: 7.5pt;
}
.row-error  td { background: #FFE0E0; }
.row-good   td { background: #E0FFE0; }
.row-cooler td { background: #E0F0FF; }
.row-warn   td { background: #FFF8E0; }
.row-even   td { background: #F8F8F8; }
.table-s3    { font-size: 6pt; }
.table-s3 th { font-size: 6pt; padding: 1.5pt 2pt; }
.table-s3 td { font-size: 6pt; padding: 1.5pt 2pt; }
"""


def colgroup(widths):
    return "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"


def thead(headers):
    return "<thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr></thead>"


def build_title_html(min_date, max_date, total_hands, total_pl):
    min_jp     = fmt_date_jp(min_date + "T00:00:00")
    max_jp     = fmt_date_jp(max_date + "T00:00:00")
    date_label = min_jp if min_date == max_date else f"{min_jp} 〜 {max_jp}"
    pl_color   = "#27ae60" if total_pl >= 0 else "#e74c3c"
    return f"""<h1 class="report-title">ポーカー GTO分析レポート</h1>
<div class="report-meta">
  <span>[日付] {esc(date_label)}</span>
  <span>[手] 総ハンド数: <strong>{esc(str(total_hands))}</strong>ハンド</span>
  <span>[損益] 総損益: <strong style="color:{pl_color}">{esc(fmt_bb(total_pl))}</strong></span>
</div>"""


def _rating_class(rating):
    if rating.startswith("❌") or "[×]" in rating:
        return "row-error"
    if rating.startswith("⚠") or "[△]" in rating:
        return "row-warn"
    if rating.startswith("✅") or "[○]" in rating:
        return "row-good"
    if rating.startswith("🎲") or "[?]" in rating:
        return "row-cooler"
    return "row-even"


def build_section2_html(hands):
    sorted_hands = sort_by_category([h for h in hands if h.get("is_3bet_pot")])
    hdrs = ["#", "時刻", "ポジ", "Hero手札", "ボード", "アクション概要", "結果(bb)", "GTO評価"]
    ws   = ["4%", "6%", "6%", "8%", "12%", "22%", "8%", "34%"]

    rows = []
    prev_cat = None
    for h in sorted_hands:
        cat = (h.get("bluered_classification") or {}).get("category", "")
        if cat != prev_cat:
            prev_cat  = cat
            cat_label = CAT_LABELS.get(cat, cat)
            cat_bg    = CAT_BG.get(cat, "#f0f0f0")
            rows.append(
                f'<tr><td colspan="8" style="background:{cat_bg};font-weight:700;'
                f'font-size:7.5pt;padding:2pt 4pt;border:1px solid #bbb">{esc(cat_label)}</td></tr>'
            )
        streets = h.get("streets") or {}
        board_cards = (
            ((streets.get("flop")  or {}).get("board") or []) +
            ((streets.get("turn")  or {}).get("board") or []) +
            ((streets.get("river") or {}).get("board") or [])
        )
        board      = " ".join(board_cards)
        ev         = h.get("gto_evaluation", "")
        cls        = _rating_class(get_gto_rating(ev))
        hero_cards = "".join(h.get("hero_cards") or [])
        rows.append(f"""    <tr class="{cls}">
      <td>H{esc(str(h.get('hand_number', '')))}</td>
      <td>{esc(fmt_time(h.get('datetime', '')))}</td>
      <td style="font-weight:700">{esc(h.get('hero_position', ''))}</td>
      <td>{card_to_html(hero_cards)}</td>
      <td>{card_to_html(board)}</td>
      <td>{esc(make_action_summary(h, 'preflop'))}</td>
      <td style="text-align:right">{esc(fmt_bb(h.get('hero_result_bb')))}</td>
      <td style="white-space:pre-line">{esc(gto_full_display(ev))}</td>
    </tr>""")

    rows_html = "\n".join(rows)
    return f"""
<h2 class="section-title">&#9312; 3BETポット専用分析</h2>
<p class="section-sub">対象ハンド数: {len(sorted_hands)}ハンド　｜　青線/赤線カテゴリ順で表示</p>
<table class="data-table">{colgroup(ws)}{thead(hdrs)}<tbody>{rows_html}</tbody></table>"""


def build_section3_html(hands):
    sorted_hands = sort_by_category(hands)
    hdrs = ["#", "ポジ", "Hero手札", "相手手札", "ボード(F)", "フロップ", "T", "ターン", "R", "リバー", "結果", "GTO評価・改善点"]
    ws   = ["3%", "4%", "6%", "6%", "7%", "9%", "3%", "8%", "3%", "8%", "6%", "37%"]

    rows = []
    prev_cat = None
    for h in sorted_hands:
        cat = (h.get("bluered_classification") or {}).get("category", "")
        if cat != prev_cat:
            prev_cat  = cat
            cat_label = CAT_LABELS.get(cat, cat)
            cat_bg    = CAT_BG.get(cat, "#f0f0f0")
            rows.append(
                f'<tr><td colspan="12" style="background:{cat_bg};font-weight:700;'
                f'font-size:6pt;padding:1.5pt 3pt;border:1px solid #bbb">{esc(cat_label)}</td></tr>'
            )
        ev         = h.get("gto_evaluation", "")
        cls        = _rating_class(get_gto_rating(ev))
        hero_cards = "".join(h.get("hero_cards") or [])
        opp_cards  = get_opponent_cards(h)
        streets    = h.get("streets") or {}
        flop       = " ".join((streets.get("flop") or {}).get("board") or [])
        turn_card  = get_board_card(h, "turn", 0)
        river_card = get_board_card(h, "river", 0)
        pl_num     = h.get("hero_result_bb") or 0
        pl_color   = "#27ae60" if pl_num > 0 else "#e74c3c" if pl_num < 0 else "#666"
        rows.append(f"""    <tr class="{cls}">
      <td>H{esc(str(h.get('hand_number', '')))}</td>
      <td style="font-weight:700;text-align:center">{esc(h.get('hero_position', ''))}</td>
      <td>{card_to_html(hero_cards)}</td>
      <td>{card_to_html(opp_cards)}</td>
      <td>{card_to_html(flop)}</td>
      <td>{esc(make_action_summary(h, 'flop'))}</td>
      <td>{card_to_html(turn_card)}</td>
      <td>{esc(make_action_summary(h, 'turn'))}</td>
      <td>{card_to_html(river_card)}</td>
      <td>{esc(make_action_summary(h, 'river'))}</td>
      <td style="text-align:right;color:{pl_color};font-weight:700">{esc(fmt_bb(pl_num))}</td>
      <td style="white-space:pre-line">{esc(gto_full_display(ev))}</td>
    </tr>""")

    rows_html = "\n".join(rows)
    return f"""
<h2 class="section-title">&#9313; 全ハンドアクション一覧</h2>
<p class="section-sub">スートカラー: ♠黒 ♥赤 ♦青 ♣緑 | H=Hero V=Villain | [○]良好 [△]改善 [×]エラー [?]クーラー | 青線/赤線カテゴリ順で表示</p>
<table class="data-table table-s3">{colgroup(ws)}{thead(hdrs)}<tbody>{rows_html}</tbody></table>"""


def build_full_html(sections):
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <style>{build_css()}</style>
</head>
<body>
{"".join(sections)}
</body>
</html>"""


def generate_pdf(html_content, out_file):
    from weasyprint import HTML
    HTML(string=html_content).write_pdf(out_file)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python scripts/generate.py <output_dir> <data1.json> [data2.json ...]",
              file=sys.stderr)
        sys.exit(1)

    output_dir = args[0]
    json_paths = args[1:]

    hands = []
    for json_path in json_paths:
        p = Path(json_path)
        if not p.exists():
            print(f"File not found: {json_path}", file=sys.stderr)
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("hands"):
            hands.extend(d["hands"])

    hands.sort(key=lambda h: h.get("datetime") or "")

    today    = date.today().isoformat()
    dates    = [h["datetime"][:10] for h in hands if h.get("datetime")]
    min_date = min(dates) if dates else today
    max_date = max(dates) if dates else today
    total_pl = sum(h.get("hero_result_bb") or 0 for h in hands)

    html_content = build_full_html([
        build_title_html(min_date, max_date, len(hands), total_pl),
        build_section2_html(hands),
        build_section3_html(hands),
    ])

    date_str = min_date if min_date == max_date else f"{min_date}_{max_date}"
    out_dir  = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = str(out_dir / f"GTO_Report_{date_str}.pdf")

    print("  PDF生成中...")
    generate_pdf(html_content, out_file)
    print(f"  Generated: {out_file}")


if __name__ == "__main__":
    main()
