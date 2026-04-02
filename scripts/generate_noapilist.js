/**
 * generate_noapilist.js - 青線/赤線分類レポート PDF 生成（APIなしモード）
 * 使用法: node scripts/generate_noapilist.js <output_dir> <classified.json>
 */
"use strict";

const fs        = require("fs");
const path      = require("path");
const puppeteer = require("puppeteer");

const SUIT_COLORS = { "♠": "#000000", "♥": "#E00000", "♦": "#0055CC", "♣": "#007700" };

// ─── ユーティリティ ──────────────────────────────────────────────────────────

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function cardToHtml(cardStr) {
  if (!cardStr) return "";
  return String(cardStr).replace(
    /([23456789TJQKA]{1,2})([\u2660\u2665\u2666\u2663])/g,
    (_, rank, suit) =>
      `${esc(rank)}<span style="color:${SUIT_COLORS[suit] || "#000"}">${suit}</span>`
  );
}

function fmtBb(val) {
  const n = parseFloat(val);
  if (isNaN(n)) return "—";
  return n > 0 ? `+${n.toFixed(2)}bb` : n < 0 ? `${n.toFixed(2)}bb` : "0bb";
}

function fmtDateJP(isoStr) {
  const m = (isoStr || "").match(/(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}年${m[2]}月${m[3]}日` : (isoStr || "");
}

function plColor(val) {
  return val > 0 ? "#27ae60" : val < 0 ? "#e74c3c" : "#666";
}

// ─── ハンド情報ヘルパー ───────────────────────────────────────────────────────

function getHeroName(hand) {
  return (hand.players || []).find(p => p.is_hero)?.name || "";
}

function getOppCards(hand) {
  const others = (hand.players || []).filter(p => !p.is_hero);
  if (!others.length) return "";
  const winners = (hand.result?.winners || []).map(w => w.name);
  const opp = others.find(p => winners.includes(p.name)) || others[0];
  return (opp.hole_cards || []).join("");
}

/** allin_ev からheroのEVを返す。オールインなし or データなし → null */
function getHeroEV(hand) {
  const name = getHeroName(hand);
  if (!name) return null;
  const allin = hand.result?.allin_ev;
  if (!allin || !Object.keys(allin).length) return null;
  const ev = allin[name];
  return ev != null ? parseFloat(ev) : null;
}

function getBoardAtStreet(hand, upTo) {
  const order = ["flop", "turn", "river"];
  const idx   = order.indexOf(upTo);
  if (idx < 0) return "";
  const cards = [];
  for (let i = 0; i <= idx; i++) {
    const s = hand.streets?.[order[i]];
    if (s?.board) cards.push(...s.board.filter(c => c && c !== "-"));
  }
  return cards.join(" ");
}

// ─── 統計計算 ────────────────────────────────────────────────────────────────

function isHeroAction(hand, action) {
  return (hand.players || []).some(p => p.is_hero && p.name === action.name);
}
function isHeroName(hand, name) {
  return (hand.players || []).some(p => p.is_hero && p.name === name);
}

function calcEV(hands) {
  let total = 0, count = 0;
  for (const h of hands) {
    const ev = getHeroEV(h);
    if (ev != null) { total += ev; count++; }
  }
  return count > 0 ? total : null;
}

function calcPositionStats(hands) {
  const ORDER = ["UTG", "UTG+1", "LJ", "HJ", "CO", "BTN", "SB", "BB"];
  const stats = {};
  for (const pos of ORDER) {
    const ph = hands.filter(h => h.hero_position === pos);
    if (!ph.length) continue;
    let vpip = 0, pfr = 0, tb = 0, won = 0, pl = 0;
    for (const h of ph) {
      const acts = (h.streets?.preflop || []).filter(a => isHeroAction(h, a));
      if (acts.some(a => a.action === "Call" || a.action === "Raise")) vpip++;
      if (acts.some(a => a.action === "Raise")) pfr++;
      if (h.is_3bet_pot && acts.filter(a => a.action === "Raise").length >= 1) tb++;
      if ((h.result?.winners || []).some(w => isHeroName(h, w.name))) won++;
      pl += h.hero_result_bb || 0;
    }
    const n = ph.length;
    stats[pos] = {
      hands:     n,
      vpip:      ((vpip / n) * 100).toFixed(1),
      pfr:       ((pfr  / n) * 100).toFixed(1),
      three_bet: ((tb   / n) * 100).toFixed(1),
      win_rate:  ((won  / n) * 100).toFixed(1),
      total_pl:  pl.toFixed(2),
      avg_pl:    (pl / n).toFixed(2),
    };
  }
  return stats;
}

// ─── 定数 ────────────────────────────────────────────────────────────────────

const BLUE_CAT_ORDER = [
  "value_or_bluff_success",
  "bluff_catch",
  "bluff_failed",
  "call_lost",
];
const RED_CAT_ORDER = [
  "hero_aggression_won",
  "bad_fold",
  "nice_fold",
  "fold_unknown",
];
const STREET_ORDER = ["preflop", "flop", "turn", "river"];
const STREET_JP    = { preflop: "PF", flop: "F", turn: "T", river: "R" };

const CAT_CSS = {
  value_or_bluff_success: "cat-value",
  bluff_catch:            "cat-catch",
  bluff_failed:           "cat-bluff",
  call_lost:              "cat-call",
  hero_aggression_won:    "cat-agg",
  bad_fold:               "cat-bad",
  nice_fold:              "cat-nice",
  fold_unknown:           "cat-unknown",
};

// ─── CSS ─────────────────────────────────────────────────────────────────────

function buildCss() {
  return `
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Meiryo', 'MS Gothic', sans-serif;
  font-size: 8.5pt;
  color: #222;
  background: #fff;
  padding: 6mm 8mm 8mm 8mm;
}

/* ── コンパクトヘッダー（タイトル＋①を1バンドに） ── */
.header-band {
  border: 1px solid #2E4057;
  border-left: 5px solid #2E4057;
  border-radius: 0 3px 3px 0;
  padding: 2mm 3mm;
  margin-bottom: 4mm;
  background: #fafbfc;
}
.header-row1 {
  display: flex; align-items: baseline; gap: 6mm;
  border-bottom: 1px solid #dde; padding-bottom: 1.5mm; margin-bottom: 1.5mm;
}
.header-title { font-size: 10pt; font-weight: 700; color: #2E4057; white-space: nowrap; }
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
.hstat-lbl   { font-size: 6.5pt; color: #666; white-space: nowrap; }
.hstat-val   { font-size: 9pt; font-weight: 700; white-space: nowrap; }
.hstat-sub   { font-size: 6pt; color: #888; white-space: nowrap; }
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
.section-sub { font-size: 7.5pt; color: #555; margin-bottom: 2mm; }

/* ── 2カラムレイアウト ── */
.two-col { display: flex; gap: 4mm; margin-bottom: 4mm; align-items: flex-start; }
.col-half { flex: 1; min-width: 0; }

/* ── テーブル共通 ── */
.data-table {
  width: 100%; border-collapse: collapse;
  table-layout: fixed; margin-bottom: 4mm;
}
.data-table th {
  background: #2E4057; color: #fff;
  padding: 1.5pt 2pt; text-align: center;
  font-weight: 700; border: 1px solid #1e2e40;
  white-space: nowrap;
}
.data-table td {
  padding: 1.5pt 2pt; border: 1px solid #ccc;
  vertical-align: middle; overflow: hidden;
}
/* 2カラム用小テーブル */
.tbl-s { font-size: 6.5pt; }
.tbl-s th { font-size: 6pt; padding: 1.5pt 2pt; }
.tbl-s td { font-size: 6pt; padding: 1.5pt 2pt; }

/* カテゴリグループヘッダー行 */
.cat-header td {
  font-size: 6.5pt; font-weight: 700;
  background: #eef0f4; padding: 2pt 3pt;
  border: 1px solid #bbb;
}
.cat-badge {
  display: inline-block; font-size: 5.5pt; font-weight: bold;
  padding: 0px 3px; border-radius: 2px;
}
.cat-value  { background: #dff0d8; color: #2d6a4f; }
.cat-catch  { background: #cce5ff; color: #004085; }
.cat-bluff  { background: #f8d7da; color: #721c24; }
.cat-call   { background: #f8d7da; color: #721c24; }
.cat-agg    { background: #d4edda; color: #155724; }
.cat-bad    { background: #f8d7da; color: #721c24; }
.cat-nice   { background: #dff0d8; color: #2d6a4f; }
.cat-unknown{ background: #fff3cd; color: #856404; }

/* 損益色 */
.pl-pos { color: #27ae60; font-weight: bold; }
.pl-neg { color: #e74c3c; font-weight: bold; }

/* 行背景 */
.row-blue td { background: #f0f4ff; }
.row-red  td { background: #fff4f0; }
.row-api  td { background: #fffbea; }

/* API要フラグ */
.api-flag { color: #d97706; font-weight: bold; font-size: 6pt; }

/* セクション4 */
.pos-table { font-size: 7.5pt; }
.pos-table th { font-size: 7pt; }
.pos-table td { font-size: 7pt; padding: 2pt 3pt; }
`;
}

// ─── ヘッダーバンド（タイトル + サマリーを1ブロックに圧縮） ──────────────────

function buildSection1Html(hands, minDate, maxDate) {
  const minJP = fmtDateJP(minDate + "T00:00:00");
  const maxJP = fmtDateJP(maxDate + "T00:00:00");
  const dateLabel = minDate === maxDate ? minJP : `${minJP} 〜 ${maxJP}`;

  const totalPL   = hands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const totalEV   = calcEV(hands);
  const evDiff    = totalEV != null ? totalPL - totalEV : null;

  const blueHands = hands.filter(h => h.bluered_classification?.line === "blue");
  const redHands  = hands.filter(h => h.bluered_classification?.line === "red");
  const bluePL    = blueHands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const redPL     = redHands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const blueEV    = calcEV(blueHands);
  const redEV     = calcEV(redHands);
  const needsApi  = hands.filter(h => h.bluered_classification?.needs_api).length;

  // EV関連テキスト（あれば1行で添える）
  const evTotalStr = totalEV != null
    ? `　EV: <span style="color:${plColor(totalEV)}">${fmtBb(totalEV)}</span>　差: <span style="color:${plColor(evDiff)}">${fmtBb(evDiff)}</span>`
    : "";
  const blueEvStr = blueEV != null
    ? `<span class="hstat-sub">EV ${fmtBb(blueEV)} / 差 ${fmtBb(bluePL - blueEV)}</span>` : "";
  const redEvStr  = redEV != null
    ? `<span class="hstat-sub">EV ${fmtBb(redEV)} / 差 ${fmtBb(redPL - redEV)}</span>` : "";

  const apiNote = needsApi > 0
    ? `<span class="api-notice">⚠️ 要AI: ${needsApi}手</span>` : "";

  return `
<div class="header-band">
  <div class="header-row1">
    <span class="header-title">🃏 ポーカー 青線/赤線 分類レポート</span>
    <div class="header-meta">
      <span>📅 ${esc(dateLabel)}</span>
      <span>総ハンド数: <strong>${esc(hands.length)}</strong></span>
    </div>
  </div>
  <div class="header-row2">
    <div class="hstat total">
      <span class="hstat-lbl">実収支</span>
      <span class="hstat-val" style="color:${plColor(totalPL)}">${fmtBb(totalPL)}</span>
      ${evTotalStr ? `<span class="hstat-sub">${evTotalStr}</span>` : ""}
    </div>
    <div class="hstat blue">
      <span class="hstat-lbl">🔵 青線 ${blueHands.length}手</span>
      <span class="hstat-val" style="color:${plColor(bluePL)}">${fmtBb(bluePL)}</span>
      ${blueEvStr}
    </div>
    <div class="hstat red">
      <span class="hstat-lbl">🔴 赤線 ${redHands.length}手</span>
      <span class="hstat-val" style="color:${plColor(redPL)}">${fmtBb(redPL)}</span>
      ${redEvStr}
    </div>
    ${apiNote}
  </div>
</div>`;
}

// ─── セクション2/3: 青線/赤線 横並び ─────────────────────────────────────────

/**
 * カテゴリ別 → ストリート別 にグループ化してテーブル行HTMLを生成
 * @param {Array} filteredHands - 青線 or 赤線のハンド配列
 * @param {Array} catOrder      - カテゴリの表示順
 * @param {boolean} showApiFlag - 要AIフラグ列を表示するか
 */
function buildGroupedRows(filteredHands, catOrder, showApiFlag) {
  const colspan = showApiFlag ? 7 : 6;
  let html = "";

  for (const cat of catOrder) {
    const catHands = filteredHands
      .filter(h => h.bluered_classification?.category === cat)
      .sort((a, b) => {
        const ai = STREET_ORDER.indexOf(a.bluered_classification?.last_street || "preflop");
        const bi = STREET_ORDER.indexOf(b.bluered_classification?.last_street || "preflop");
        return ai !== bi ? ai - bi : (a.hand_number || 0) - (b.hand_number || 0);
      });

    if (!catHands.length) continue;

    const clf0      = catHands[0].bluered_classification;
    const catLabel  = clf0.category_label || cat;
    const catPL     = catHands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
    const catCss    = CAT_CSS[cat] || "";

    html += `<tr class="cat-header">
      <td colspan="${colspan}">
        <span class="cat-badge ${catCss}">${esc(catLabel)}</span>
        &nbsp; ${catHands.length}手
        <span style="float:right;color:${plColor(catPL)}">${fmtBb(catPL)}</span>
      </td>
    </tr>`;

    for (const h of catHands) {
      const clf    = h.bluered_classification;
      const lastSt = clf?.last_street || "river";
      const board  = getBoardAtStreet(h, lastSt);
      const opp    = getOppCards(h);
      const plNum  = h.hero_result_bb || 0;
      const plCls  = plNum > 0 ? "pl-pos" : plNum < 0 ? "pl-neg" : "";
      const rowCls = showApiFlag && clf?.needs_api ? "row-api" : "";

      html += `<tr class="${rowCls}">
        <td style="text-align:center;color:#888">H${esc(h.hand_number)}</td>
        <td style="text-align:center;font-weight:bold">${esc(STREET_JP[lastSt] || lastSt)}</td>
        <td>${cardToHtml((h.hero_cards || []).join(""))}</td>
        <td>${opp ? cardToHtml(opp) : '<span style="color:#bbb">—</span>'}</td>
        <td>${cardToHtml(board) || '<span style="color:#bbb">—</span>'}</td>
        <td style="text-align:right" class="${plCls}">${esc(fmtBb(plNum))}</td>
        ${showApiFlag ? `<td style="text-align:center">${clf?.needs_api ? '<span class="api-flag">★</span>' : ''}</td>` : ""}
      </tr>`;
    }
  }

  return html;
}

function buildSection2And3Html(hands) {
  const blueHands = hands.filter(h => h.bluered_classification?.line === "blue");
  const redHands  = hands.filter(h => h.bluered_classification?.line === "red");

  const bluePL = blueHands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const redPL  = redHands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const needsApiCnt = redHands.filter(h => h.bluered_classification?.needs_api).length;

  const blueRows = buildGroupedRows(blueHands, BLUE_CAT_ORDER, false);
  const redRows  = buildGroupedRows(redHands, RED_CAT_ORDER, true);

  // 列幅: # | St | Hero | 相手 | Board | 損益 [| AI]
  const colBlue = `<colgroup>
    <col style="width:8%"><col style="width:7%"><col style="width:16%">
    <col style="width:16%"><col style="width:32%"><col style="width:21%">
  </colgroup>`;
  const colRed = `<colgroup>
    <col style="width:7%"><col style="width:7%"><col style="width:15%">
    <col style="width:15%"><col style="width:29%"><col style="width:19%"><col style="width:8%">
  </colgroup>`;

  const hdrsBlue = ["#", "St", "Hero", "相手", "Board", "損益(bb)"];
  const hdrsRed  = ["#", "St", "Hero", "相手", "Board", "損益(bb)", "AI"];

  const noData = (n) =>
    `<tr><td colspan="${n}" style="text-align:center;color:#aaa;padding:4pt">該当なし</td></tr>`;

  return `
<h2 class="section-title">① 青線 / 赤線 ハンド一覧</h2>
<div class="two-col">
  <div class="col-half">
    <p class="section-sub">🔵 青線 ${blueHands.length}手 &nbsp;
      実収支: <strong style="color:${plColor(bluePL)}">${fmtBb(bluePL)}</strong>
    </p>
    <table class="data-table tbl-s">
      ${colBlue}
      <thead><tr>${hdrsBlue.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${blueRows || noData(6)}</tbody>
    </table>
  </div>
  <div class="col-half">
    <p class="section-sub">🔴 赤線 ${redHands.length}手 &nbsp;
      実収支: <strong style="color:${plColor(redPL)}">${fmtBb(redPL)}</strong>
      &nbsp; ★要AI: ${needsApiCnt}
    </p>
    <table class="data-table tbl-s">
      ${colRed}
      <thead><tr>${hdrsRed.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${redRows || noData(7)}</tbody>
    </table>
  </div>
</div>`;
}

// ─── セクション3: プリフロップ別成績 ──────────────────────────────────────────

function buildSection3Html(hands) {
  const posStats = calcPositionStats(hands);
  const hdrs = ["ポジション", "ハンド数", "VPIP", "PFR", "3BET%", "勝率", "合計損益(bb)", "平均損益(bb)"];
  const ws   = ["13%", "11%", "10%", "10%", "10%", "10%", "18%", "18%"];

  const rows = Object.entries(posStats).map(([pos, s], i) => {
    const plNum = parseFloat(s.total_pl);
    const cls   = plNum > 0 ? "row-blue" : plNum < -10 ? "row-red" : i % 2 === 0 ? "" : "";
    return `
    <tr class="${cls}">
      <td style="font-weight:700">${esc(pos)}</td>
      <td style="text-align:center">${esc(s.hands)}</td>
      <td style="text-align:center">${esc(s.vpip)}%</td>
      <td style="text-align:center">${esc(s.pfr)}%</td>
      <td style="text-align:center">${esc(s.three_bet)}%</td>
      <td style="text-align:center">${esc(s.win_rate)}%</td>
      <td style="text-align:right;color:${plColor(plNum)}">${esc(s.total_pl)}</td>
      <td style="text-align:right;color:${plColor(parseFloat(s.avg_pl))}">${esc(s.avg_pl)}</td>
    </tr>`;
  }).join("");

  return `
<h2 class="section-title">② プリフロップ別成績</h2>
<table class="data-table pos-table">
  <colgroup>${ws.map(w => `<col style="width:${w}">`).join("")}</colgroup>
  <thead><tr>${hdrs.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
  <tbody>${rows || '<tr><td colspan="8" style="text-align:center;color:#aaa">データなし</td></tr>'}</tbody>
</table>`;
}

// ─── HTML組み立て ─────────────────────────────────────────────────────────────

function buildFullHtml(sections) {
  return `<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <style>${buildCss()}</style>
</head>
<body>
${sections.join("\n")}
</body>
</html>`;
}

// ─── PDF生成 ──────────────────────────────────────────────────────────────────

async function generatePdf(html, outFile) {
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  try {
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: "networkidle0" });
    await page.pdf({
      path:            outFile,
      format:          "A4",
      printBackground: true,
      margin:          { top: "8mm", bottom: "8mm", left: "8mm", right: "8mm" },
    });
  } finally {
    await browser.close();
  }
}

// ─── メイン ──────────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.error("Usage: node scripts/generate_noapilist.js <output_dir> <classified.json>");
    process.exit(1);
  }

  const outputDir = args[0];
  const jsonPath  = args[1];

  if (!fs.existsSync(jsonPath)) {
    console.error(`File not found: ${jsonPath}`);
    process.exit(1);
  }

  const data  = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  const hands = (data.hands || []).slice().sort(
    (a, b) => (a.datetime || "").localeCompare(b.datetime || "")
  );

  if (!hands.length) {
    console.error("[ERROR] ハンドデータが空です");
    process.exit(1);
  }

  const today   = new Date().toISOString().slice(0, 10);
  const dates   = hands.map(h => h.datetime?.slice(0, 10)).filter(Boolean);
  const minDate = dates.length ? dates.reduce((a, b) => a < b ? a : b) : today;
  const maxDate = dates.length ? dates.reduce((a, b) => a > b ? a : b) : today;

  const html = buildFullHtml([
    buildSection1Html(hands, minDate, maxDate),
    buildSection2And3Html(hands),
    buildSection3Html(hands),
  ]);

  fs.mkdirSync(outputDir, { recursive: true });
  const dateStr = minDate === maxDate ? minDate : `${minDate}_${maxDate}`;
  const outFile = path.join(outputDir, `NoAPI_Report_${dateStr}.pdf`);

  console.log("  PDF生成中...");
  await generatePdf(html, outFile);
  console.log(`  Generated: ${outFile}`);
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
