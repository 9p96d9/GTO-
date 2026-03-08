/**
 * generate.js - GTO分析レポート HTML 生成（ローカル版・API不要）
 * 使用法: node scripts/generate.js <output_dir> <data1.json> [data2.json ...]
 *
 * // --- API版（無効化中）---
 * // const { GoogleGenerativeAI } = require("@google/generative-ai");
 * // const puppeteer = require("puppeteer");
 * // const GEMINI_MODEL = "gemini-2.5-flash";
 * // async function geminiGenerate(genAI, prompt) { ... }
 * // async function fetchImprovement(genAI, hands) { ... }
 * // async function fetchStrength(genAI, goodHands) { ... }
 * // async function fetchOpponentStrategies(genAI, opponentStats, allHands) { ... }
 * // async function generatePdf(html, outFile) { ... }
 */
"use strict";

const fs   = require("fs");
const path = require("path");
// require("dotenv").config();

// ─── 定数 ────────────────────────────────────────────────────────────────────

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
  if (isNaN(n)) return "0bb";
  return n > 0 ? `+${n.toFixed(2)}bb` : n < 0 ? `${n.toFixed(2)}bb` : "0bb";
}

function fmtTime(isoStr) {
  const m = (isoStr || "").match(/T(\d{2}:\d{2})/);
  return m ? m[1] : "";
}

function fmtDateJP(isoStr) {
  const m = (isoStr || "").match(/(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}年${m[2]}月${m[3]}日` : (isoStr || "");
}

// ─── 統計計算 ────────────────────────────────────────────────────────────────

function isHeroName(hand, name) {
  return (hand.players || []).some((p) => p.is_hero && p.name === name);
}

function isHeroAction(hand, action) {
  return (hand.players || []).some((p) => p.is_hero && p.name === action.name);
}

function calcPositionStats(hands) {
  const ORDER = ["UTG", "UTG+1", "LJ", "HJ", "CO", "BTN", "SB", "BB"];
  const stats = {};
  for (const pos of ORDER) {
    const ph = hands.filter((h) => h.hero_position === pos);
    if (!ph.length) continue;
    let vpip = 0, pfr = 0, tb = 0, won = 0, pl = 0;
    for (const h of ph) {
      const acts = (h.streets?.preflop || []).filter((a) => isHeroAction(h, a));
      if (acts.some((a) => a.action === "Call" || a.action === "Raise")) vpip++;
      if (acts.some((a) => a.action === "Raise")) pfr++;
      if (h.is_3bet_pot && acts.filter((a) => a.action === "Raise").length >= 1) tb++;
      if ((h.result?.winners || []).some((w) => isHeroName(h, w.name))) won++;
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

/** opponents_summary.json を読み込んでセクション7用の配列を返す */
function loadOpponentSummary(dataDir) {
  if (!dataDir) return [];
  const summaryPath = path.join(dataDir, "opponents_summary.json");
  if (!fs.existsSync(summaryPath)) {
    console.warn("  [WARN] opponents_summary.json が見つかりません:", summaryPath);
    return [];
  }
  try {
    const data = JSON.parse(fs.readFileSync(summaryPath, "utf-8"));
    return Object.entries(data.opponents || {}).map(([name, opp]) => ({
      name,
      hands:            opp.total_hands,
      vpip:             (opp.vpip     * 100).toFixed(1),
      pfr:              (opp.pfr      * 100).toFixed(1),
      three_bet:        (opp.threebet * 100).toFixed(1),
      hero_win_rate:    (opp.hero_winrate * 100).toFixed(1),
      estimated_type:   opp.player_type || "バランス",
      exploit_strategy: "",
      reads:            "",
    })).sort((a, b) => b.hands - a.hands);
  } catch (e) {
    console.error("  [ERROR] opponents_summary.json 読み込みエラー:", e.message);
    return [];
  }
}

function makeActionSummary(hand, street) {
  const s = hand.streets?.[street];
  if (!s) return "";
  const acts = street === "preflop" ? s : (s.actions || []);
  return acts.map((a) => {
    const pfx = isHeroAction(hand, a) ? "H" : "V";
    const amt = a.amount_bb != null ? ` ${a.amount_bb}bb` : "";
    return `${pfx}:${a.action}${amt}`;
  }).join(" ");
}

function getBoardCard(hand, street, idx) {
  return hand.streets?.[street]?.board?.[idx] || "";
}

function getOpponentCards(hand) {
  const others = (hand.players || []).filter((p) => !p.is_hero);
  if (!others.length) return "";
  const winners = (hand.result?.winners || []).map((w) => w.name);
  const opp = others.find((p) => winners.includes(p.name)) || others[0];
  return (opp.hole_cards || []).join("");
}

// ─── GTO評価ヘルパー ──────────────────────────────────────────────────────────

function getGtoRating(ev) {
  if (!ev) return "";
  for (const line of ev.split("\n")) {
    const l = line.trim();
    if (l.startsWith("GTO評価:")) return l.slice("GTO評価:".length).trim();
  }
  return ev;
}

function gtoTableDisplay(ev) {
  if (!ev) return "";
  const lines = ev.split("\n").map(l => l.trim()).filter(Boolean);
  const rating = lines.find(l => l.startsWith("GTO評価:"))?.slice("GTO評価:".length).trim() || lines[0] || "";
  const ichi   = lines.find(l => l.startsWith("一言:"))?.slice("一言:".length).trim() || "";
  return ichi ? `${rating}\n${ichi}` : rating;
}

// ─── CSS ─────────────────────────────────────────────────────────────────────

function buildCss() {
  return `
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Meiryo', 'MS Gothic', sans-serif;
  font-size: 9pt;
  color: #222;
  background: #fff;
  padding: 20px;
}

h1 { font-size: 22pt; font-weight: 700; margin-bottom: 6px; }
h2.section-title {
  font-size: 14pt;
  font-weight: 700;
  border-bottom: 2px solid #333;
  padding-bottom: 4px;
  margin: 30px 0 8px;
}
.section-sub { font-size: 9pt; color: #555; margin-bottom: 8px; }
.cover { text-align: center; padding: 40px 0 20px; border-bottom: 2px solid #333; margin-bottom: 10px; }
.cover-date  { font-size: 13pt; margin: 6px 0; }
.cover-stat  { font-size: 11pt; margin: 3px 0; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 8pt;
  margin-bottom: 12px;
}
.data-table th {
  background: #2E4057;
  color: #fff;
  padding: 3px 4px;
  text-align: center;
  border: 1px solid #1e2e40;
  white-space: nowrap;
}
.data-table td {
  padding: 2px 3px;
  border: 1px solid #ccc;
  vertical-align: middle;
}
.row-error  td { background: #FFE0E0; }
.row-good   td { background: #E0FFE0; }
.row-cooler td { background: #E0F0FF; }
.row-even   td { background: #F5F5F5; }

.error-hand { margin-bottom: 16px; padding: 10px; border: 1px solid #f99; background: #fff5f5; }
.error-hand h3 { font-size: 11pt; margin-bottom: 4px; }
.error-hand p  { font-size: 10pt; line-height: 1.6; }

.text-block { font-size: 10pt; line-height: 1.8; }
.text-block p { margin-bottom: 4px; }

.good-plays h3 { font-size: 12pt; font-weight: 700; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin: 10px 0 6px; }
.good-plays ul { padding-left: 20px; }
.good-plays li { font-size: 10pt; margin-bottom: 3px; line-height: 1.5; }

.summary-box { background: #f0f4ff; border: 1px solid #aac; padding: 12px; margin-bottom: 12px; font-size: 10pt; line-height: 1.8; }
`;
}

// ─── HTML セクションビルダー ──────────────────────────────────────────────────

function colgroup(widths) {
  return `<colgroup>${widths.map(w => `<col style="width:${w}">`).join("")}</colgroup>`;
}

function thead(headers) {
  return `<thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>`;
}

// ── カバー ──
function buildCoverHtml(minDate, maxDate, totalHands, totalPL) {
  const minJP     = fmtDateJP(minDate + "T00:00:00");
  const maxJP     = fmtDateJP(maxDate + "T00:00:00");
  const dateLabel = minDate === maxDate ? minJP : `${minJP} 〜 ${maxJP}`;
  return `
<div class="cover">
  <h1>ポーカー GTO分析レポート</h1>
  <p class="cover-date">${esc(dateLabel)}</p>
  <p class="cover-stat">総ハンド数: ${esc(totalHands)}ハンド</p>
  <p class="cover-stat">総損益: ${esc(fmtBb(totalPL))}</p>
  <p class="cover-stat" style="color:#888;font-size:9pt;margin-top:8px;">※ローカル評価版（AI分析なし）</p>
</div>`;
}

// ── セクション1: ポジション別成績 ──
function buildSection1Html(posStats) {
  const hdrs = ["ポジション","ハンド数","VPIP","PFR","3BET%","勝率","合計損益(bb)","平均損益(bb)"];
  const ws   = ["15%","12%","11%","11%","11%","11%","14.5%","14.5%"];
  const rows = Object.entries(posStats).map(([pos, s], i) => `
    <tr class="${i % 2 === 0 ? "row-even" : ""}">
      <td>${esc(pos)}</td><td>${esc(s.hands)}</td>
      <td>${esc(s.vpip)}%</td><td>${esc(s.pfr)}%</td><td>${esc(s.three_bet)}%</td>
      <td>${esc(s.win_rate)}%</td><td>${esc(s.total_pl)}</td><td>${esc(s.avg_pl)}</td>
    </tr>`).join("");
  return `
<h2 class="section-title">セクション1: ポジション別成績分析</h2>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション2: 3BETポット ──
function buildSection2Html(hands) {
  const threeBetHands = hands.filter((h) => h.is_3bet_pot);
  const hdrs = ["#","日時","ポジ","手札","ボード","アクション概要","結果(bb)","GTO評価"];
  const ws   = ["4%","6%","7%","9%","12%","21.8%","7%","33.2%"];
  const rows = threeBetHands.map((h, i) => {
    const board = [
      ...(h.streets?.flop?.board  || []),
      ...(h.streets?.turn?.board  || []),
      ...(h.streets?.river?.board || []),
    ].join(" ");
    return `
    <tr class="${i % 2 === 0 ? "row-even" : ""}">
      <td>${esc(h.hand_number)}</td>
      <td>${esc(fmtTime(h.datetime))}</td>
      <td>${esc(h.hero_position || "")}</td>
      <td>${cardToHtml((h.hero_cards || []).join(""))}</td>
      <td>${cardToHtml(board)}</td>
      <td>${esc(makeActionSummary(h, "preflop"))}</td>
      <td>${esc(fmtBb(h.hero_result_bb))}</td>
      <td style="white-space:pre-line">${esc(gtoTableDisplay(h.gto_evaluation || ""))}</td>
    </tr>`;
  }).join("");
  return `
<h2 class="section-title">セクション2: 3BETポット分析</h2>
<p class="section-sub">対象ハンド数: ${threeBetHands.length}</p>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション3: 全ハンド一覧 ──
function buildSection3Html(hands) {
  const hdrs = ["#","時刻","ポジ","Hero手札","相手手札","フロップ","ターン","リバー","結果(bb)","GTO評価"];
  const ws = ["4%","5%","5%","8%","8%","15%","10%","10%","8%","27%"];
  const rows = hands.map((h, idx) => {
    const ev      = h.gto_evaluation || "";
    const rating  = getGtoRating(ev);
    const cls     = rating.startsWith("❌") ? "row-error"
                  : rating.startsWith("🎲") ? "row-cooler"
                  : rating.startsWith("✅") ? "row-good"
                  : idx % 2 === 0           ? "row-even" : "";
    const heroCards = (h.hero_cards || []).join("");
    const oppCards  = getOpponentCards(h);
    const flop = (h.streets?.flop?.board || []).join(" ");
    const turn = getBoardCard(h, "turn", 0);
    const river = getBoardCard(h, "river", 0);
    return `
    <tr class="${cls}">
      <td>${esc(h.hand_number)}</td>
      <td>${esc(fmtTime(h.datetime))}</td>
      <td>${esc(h.hero_position || "")}</td>
      <td>${cardToHtml(heroCards)}</td>
      <td>${cardToHtml(oppCards)}</td>
      <td>${cardToHtml(flop)}</td>
      <td>${cardToHtml(turn)}</td>
      <td>${cardToHtml(river)}</td>
      <td>${esc(fmtBb(h.hero_result_bb))}</td>
      <td style="white-space:pre-line">${esc(gtoTableDisplay(ev))}</td>
    </tr>`;
  }).join("");
  return `
<h2 class="section-title">セクション3: 全ハンドアクション一覧</h2>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション4: GTOエラー分析 ──
function buildSection4Html(hands) {
  const errorHands = hands.filter((h) => h.has_gto_error);
  let content = "";
  if (!errorHands.length) {
    content = `<p class="section-sub">GTOエラーのハンドはありません。</p>`;
  } else {
    for (const h of errorHands) {
      const board = [
        ...(h.streets?.flop?.board  || []),
        ...(h.streets?.turn?.board  || []),
        ...(h.streets?.river?.board || []),
      ].join(" ");
      content += `
      <div class="error-hand">
        <h3>ハンド #${esc(h.hand_number)} — ${esc(h.hero_position)} / ${cardToHtml((h.hero_cards || []).join(""))}</h3>
        <p>ボード: ${board ? cardToHtml(board) : "(プリフロップのみ)"}</p>
        <p style="white-space:pre-line">${esc(h.gto_evaluation || "")}</p>
      </div>`;
    }
  }
  return `
<h2 class="section-title">セクション4: 要改善ハンド一覧</h2>
${content}`;
}

// ── セクション5: 改善点（ローカル集計版）──
function buildSection5Html(hands) {
  const errorHands  = hands.filter(h => h.has_gto_error);
  const warnHands   = hands.filter(h => {
    const r = getGtoRating(h.gto_evaluation || "");
    return r.startsWith("⚠️");
  });
  const total = hands.length;

  const lines = [];
  lines.push(`【セッション統計サマリー】`);
  lines.push(`総ハンド数: ${total}`);
  lines.push(`要改善(⚠️): ${warnHands.length}ハンド (${total ? ((warnHands.length/total)*100).toFixed(1) : 0}%)`);
  lines.push(`エラー(❌): ${errorHands.length}ハンド (${total ? ((errorHands.length/total)*100).toFixed(1) : 0}%)`);
  lines.push(``);

  // ポジション別の改善が必要なケース
  const warnByPos = {};
  for (const h of warnHands) {
    const pos = h.hero_position || "不明";
    warnByPos[pos] = (warnByPos[pos] || 0) + 1;
  }
  if (Object.keys(warnByPos).length > 0) {
    lines.push(`【ポジション別要改善】`);
    for (const [pos, cnt] of Object.entries(warnByPos).sort((a,b) => b[1]-a[1])) {
      lines.push(`  ${pos}: ${cnt}件`);
    }
  }

  lines.push(``);
  lines.push(`※ AI分析は無効化中です。詳細な改善提案はAPI版をご利用ください。`);

  const paras = lines.map(l => `<p>${esc(l)}</p>`).join("\n");
  return `
<h2 class="section-title">セクション5: 改善すべき点</h2>
<div class="text-block">${paras}</div>`;
}

// ── セクション6: 強み ──
function buildSection6Html(hands) {
  const goodHands = hands.filter(h => h.is_good_play);
  const total = hands.length;

  const summaryLines = [];
  summaryLines.push(`良好プレイ(✅): ${goodHands.length}ハンド (${total ? ((goodHands.length/total)*100).toFixed(1) : 0}%)`);
  summaryLines.push(`クーラー(🎲): ${hands.filter(h => getGtoRating(h.gto_evaluation||"").startsWith("🎲")).length}ハンド`);

  const paras = summaryLines.map(l => `<p>${esc(l)}</p>`).join("\n");

  let goodList = "";
  if (goodHands.length > 0) {
    const items = goodHands.slice(0, 20).map(h =>
      `<li>ハンド #${esc(h.hand_number)}: ${esc(h.hero_position)} / ${cardToHtml((h.hero_cards || []).join(""))} — ${esc(gtoTableDisplay(h.gto_evaluation || ""))}</li>`
    ).join("\n");
    goodList = `<div class="good-plays"><h3>ナイスプレイ一覧</h3><ul>${items}</ul></div>`;
  }

  return `
<h2 class="section-title">セクション6: 強み・ナイスプレイ</h2>
<div class="text-block">${paras}</div>
${goodList}`;
}

// ── セクション7: 対戦相手分析 ──
function buildSection7Html(opponentStats) {
  const hdrs = ["対戦相手名","総対戦数","VPIP","PFR","3BET%","ヒーロー勝率","推定タイプ"];
  const ws   = ["20%","10%","10%","10%","10%","12%","28%"];
  const rows = opponentStats.map((s, i) => `
    <tr class="${i % 2 === 0 ? "row-even" : ""}">
      <td>${esc(s.name)}</td>
      <td>${esc(s.hands)}</td>
      <td>${esc(s.vpip)}%</td>
      <td>${esc(s.pfr)}%</td>
      <td>${esc(s.three_bet)}%</td>
      <td>${esc(s.hero_win_rate)}%</td>
      <td>${esc(s.estimated_type)}</td>
    </tr>`).join("");
  return `
<h2 class="section-title">セクション7: 対戦相手分析</h2>
<p class="section-sub">※過去セッション含む累積データ</p>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ─── メイン ──────────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    console.error("Usage: node scripts/generate.js <output_dir> <data1.json> [data2.json ...]");
    process.exit(1);
  }

  const outputDir = args[0];
  const jsonPaths = args.slice(1);

  // 全 JSON を読み込んでハンドをマージ
  let hands = [];
  let dataDir = null;
  for (const jsonPath of jsonPaths) {
    if (!fs.existsSync(jsonPath)) {
      console.error(`File not found: ${jsonPath}`);
      continue;
    }
    const d = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
    if (d.hands) hands = hands.concat(d.hands);
    if (!dataDir) dataDir = path.dirname(jsonPath);
  }

  // 時系列順にソート
  hands.sort((a, b) => (a.datetime || "").localeCompare(b.datetime || ""));

  const today   = new Date().toISOString().slice(0, 10);
  const dates   = hands.map((h) => h.datetime?.slice(0, 10)).filter(Boolean);
  const minDate = dates.length ? dates.reduce((a, b) => (a < b ? a : b)) : today;
  const maxDate = dates.length ? dates.reduce((a, b) => (a > b ? a : b)) : today;

  const opponentStats = loadOpponentSummary(dataDir);
  const totalPL       = hands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const posStats      = calcPositionStats(hands);
  const topOpponents  = opponentStats.slice(0, 20);

  // HTML組み立て
  const sections = [
    buildCoverHtml(minDate, maxDate, hands.length, totalPL),
    buildSection1Html(posStats),
    buildSection2Html(hands),
    buildSection3Html(hands),
    buildSection4Html(hands),
    buildSection5Html(hands),
    buildSection6Html(hands),
    buildSection7Html(topOpponents),
  ].join("\n");

  const html = `<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>ポーカー GTO分析レポート</title>
  <style>${buildCss()}</style>
</head>
<body>
${sections}
</body>
</html>`;

  // ファイル名: .html
  const dateStr = minDate === maxDate ? minDate : `${minDate}_${maxDate}`;
  fs.mkdirSync(outputDir, { recursive: true });
  const outFile = path.join(outputDir, `GTO_Report_${dateStr}.html`);
  fs.writeFileSync(outFile, html, "utf-8");
  console.log(`  Generated: ${outFile}`);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
