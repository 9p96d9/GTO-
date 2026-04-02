/**
 * generate.js - GTO分析レポート PDF 生成
 * 使用法: node scripts/generate.js <output_dir> <data1.json> [data2.json ...]
 */
"use strict";

const fs        = require("fs");
const path      = require("path");
const puppeteer = require("puppeteer");
require("dotenv").config();

const { GoogleGenerativeAI } = require("@google/generative-ai");

const SUIT_COLORS  = { "♠": "#000000", "♥": "#E00000", "♦": "#0055CC", "♣": "#007700" };
const GEMINI_MODEL = "gemini-2.5-flash";

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
  return (hand.players || []).some(p => p.is_hero && p.name === name);
}

function isHeroAction(hand, action) {
  return (hand.players || []).some(p => p.is_hero && p.name === action.name);
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

function loadOpponentSummary(dataDir) {
  if (!dataDir) return [];
  const summaryPath = path.join(dataDir, "opponents_summary.json");
  if (!fs.existsSync(summaryPath)) return [];
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
    console.error("  [ERROR] opponents_summary.json:", e.message);
    return [];
  }
}

function makeActionSummary(hand, street) {
  const s = hand.streets?.[street];
  if (!s) return "";
  const acts = street === "preflop" ? s : (s.actions || []);
  return acts.map(a => {
    const pfx = isHeroAction(hand, a) ? "H" : "V";
    const amt = a.amount_bb != null ? ` ${a.amount_bb}bb` : "";
    return `${pfx}:${a.action}${amt}`;
  }).join(" ");
}

function getBoardCard(hand, street, idx) {
  return hand.streets?.[street]?.board?.[idx] || "";
}

function getOpponentCards(hand) {
  const others = (hand.players || []).filter(p => !p.is_hero);
  if (!others.length) return "";
  const winners = (hand.result?.winners || []).map(w => w.name);
  const opp = others.find(p => winners.includes(p.name)) || others[0];
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

function gtoFullDisplay(ev) {
  if (!ev) return "";
  // 全行をそのまま結合（テーブル内で折り返し表示）
  return ev.split("\n")
    .map(l => l.trim())
    .filter(Boolean)
    .map(l => l.replace(/^GTO評価:\s*/, "").replace(/^一言:\s*/, "").replace(/^詳細:\s*/, "").replace(/^改善:\s*/, "改善: ").replace(/^EV損失推定:\s*/, "EV: "))
    .join(" / ");
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

/* テーブル共通 */
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

/* セクション3 全ハンド一覧 */
.table-s3    { font-size: 6pt; }
.table-s3 th { font-size: 6pt; padding: 1.5pt 2pt; }
.table-s3 td { font-size: 6pt; padding: 1.5pt 2pt; }

/* セクション4 エラーハンド */
.error-hand    { margin-bottom: 5mm; padding: 3mm; border-left: 4px solid #e74c3c; background: #fff5f5; }
.error-hand h3 { font-size: 10pt; font-weight: 700; margin-bottom: 2mm; color: #c0392b; }
.error-hand p  { font-size: 9pt; line-height: 1.6; margin-bottom: 1mm; }

/* セクション5 改善点 */
.improve-block { margin-bottom: 4mm; }
.improve-block p { font-size: 9pt; line-height: 1.7; margin-bottom: 1mm; }
.improve-category {
  border-left: 5px solid #e74c3c;
  background: #fff5f5;
  padding: 3mm 4mm;
  margin-bottom: 3mm;
  border-radius: 0 4px 4px 0;
}
.improve-category.warn {
  border-left-color: #f39c12;
  background: #fffbf0;
}
.improve-category h4 { font-size: 9pt; font-weight: 700; margin-bottom: 2mm; }
.improve-category p  { font-size: 8.5pt; line-height: 1.6; margin-bottom: 1mm; }
.improve-table td { padding: 3pt 5pt; font-size: 8.5pt; }

/* セクション6 強み */
.strength-block { font-size: 9pt; line-height: 1.7; margin-bottom: 4mm; }
.strength-block p { margin-bottom: 1mm; }
.good-plays    { margin-top: 4mm; }
.good-plays h3 { font-size: 10pt; font-weight: 700; border-bottom: 2px solid #27ae60; padding-bottom: 2mm; margin-bottom: 3mm; color: #27ae60; }
.good-plays ul { padding-left: 5mm; }
.good-plays li { font-size: 8.5pt; margin-bottom: 1mm; line-height: 1.5; }

/* セクション7 対戦相手 */
.opp-profile {
  border: 1px solid #bbb;
  border-radius: 4px;
  margin-bottom: 4mm;
  overflow: hidden;
}
.opp-profile-header {
  background: #34495e; color: #fff;
  padding: 2mm 4mm;
  font-weight: 700; font-size: 9pt;
}
.opp-profile-body { padding: 3mm 4mm; font-size: 8.5pt; line-height: 1.6; }
.opp-profile-body .stats { display: flex; gap: 10mm; margin-bottom: 2mm; }
.opp-profile-body .stat  { text-align: center; }
.opp-profile-body .stat .val { font-size: 11pt; font-weight: 700; color: #2E4057; }
.opp-profile-body .stat .lbl { font-size: 7pt; color: #888; }
.opp-exploit { background: #eaf4fb; border-left: 4px solid #2980b9; padding: 2mm 3mm; margin-bottom: 2mm; font-size: 8.5pt; }
.opp-reads   { font-size: 8pt; color: #444; white-space: pre-line; }
`;
}

// ─── HTML セクションビルダー ──────────────────────────────────────────────────

function colgroup(widths) {
  return `<colgroup>${widths.map(w => `<col style="width:${w}">`).join("")}</colgroup>`;
}
function thead(headers) {
  return `<thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>`;
}

// ── タイトル + サマリー ──
function buildTitleHtml(minDate, maxDate, totalHands, totalPL) {
  const minJP     = fmtDateJP(minDate + "T00:00:00");
  const maxJP     = fmtDateJP(maxDate + "T00:00:00");
  const dateLabel = minDate === maxDate ? minJP : `${minJP} 〜 ${maxJP}`;
  const plColor   = totalPL >= 0 ? "#27ae60" : "#e74c3c";
  return `
<h1 class="report-title">ポーカー GTO分析レポート</h1>
<div class="report-meta">
  <span>📅 ${esc(dateLabel)}</span>
  <span>🃏 総ハンド数: <strong>${esc(totalHands)}</strong>ハンド</span>
  <span>💰 総損益: <strong style="color:${plColor}">${esc(fmtBb(totalPL))}</strong></span>
</div>`;
}

// ── セクション1: ポジション別成績 ──
function buildSection1Html(posStats) {
  const hdrs = ["ポジション","ハンド数","VPIP","PFR","3BET%","勝率","合計損益(bb)","平均損益(bb)"];
  const ws   = ["13%","11%","10%","10%","10%","10%","18%","18%"];
  const rows = Object.entries(posStats).map(([pos, s], i) => {
    const plNum = parseFloat(s.total_pl);
    const cls   = plNum > 0 ? "row-good" : plNum < -10 ? "row-error" : i % 2 === 0 ? "row-even" : "";
    return `
    <tr class="${cls}">
      <td style="font-weight:700">${esc(pos)}</td>
      <td style="text-align:center">${esc(s.hands)}</td>
      <td style="text-align:center">${esc(s.vpip)}%</td>
      <td style="text-align:center">${esc(s.pfr)}%</td>
      <td style="text-align:center">${esc(s.three_bet)}%</td>
      <td style="text-align:center">${esc(s.win_rate)}%</td>
      <td style="text-align:right;color:${plNum>=0?"#27ae60":"#e74c3c"}">${esc(s.total_pl)}</td>
      <td style="text-align:right;color:${parseFloat(s.avg_pl)>=0?"#27ae60":"#e74c3c"}">${esc(s.avg_pl)}</td>
    </tr>`;
  }).join("");
  return `
<h2 class="section-title">① ポジション別成績分析</h2>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション2: 3BETポット ──
function buildSection2Html(hands) {
  const threeBetHands = hands.filter(h => h.is_3bet_pot);
  const hdrs = ["#","時刻","ポジ","Hero手札","ボード","アクション概要","結果(bb)","GTO評価"];
  const ws   = ["4%","6%","6%","8%","12%","22%","8%","34%"];
  const rows = threeBetHands.map((h, i) => {
    const board = [
      ...(h.streets?.flop?.board  || []),
      ...(h.streets?.turn?.board  || []),
      ...(h.streets?.river?.board || []),
    ].join(" ");
    const ev     = h.gto_evaluation || "";
    const rating = getGtoRating(ev);
    const cls    = rating.startsWith("❌") ? "row-error" : rating.startsWith("⚠️") ? "row-warn"
                 : rating.startsWith("✅") ? "row-good"  : i % 2 === 0 ? "row-even" : "";
    return `
    <tr class="${cls}">
      <td>H${esc(h.hand_number)}</td>
      <td>${esc(fmtTime(h.datetime))}</td>
      <td style="font-weight:700">${esc(h.hero_position || "")}</td>
      <td>${cardToHtml((h.hero_cards || []).join(""))}</td>
      <td>${cardToHtml(board)}</td>
      <td>${esc(makeActionSummary(h, "preflop"))}</td>
      <td style="text-align:right">${esc(fmtBb(h.hero_result_bb))}</td>
      <td style="white-space:pre-line">${esc(gtoFullDisplay(ev))}</td>
    </tr>`;
  }).join("");
  return `
<h2 class="section-title">② 3BETポット専用分析</h2>
<p class="section-sub">対象ハンド数: ${threeBetHands.length}ハンド</p>
<table class="data-table">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション3: 全ハンドアクション一覧（縦向き12列）──
function buildSection3Html(hands) {
  const hdrs = ["#","ポジ","Hero手札","相手手札","ボード(F)","フロップ","T","ターン","R","リバー","結果","GTO評価・改善点"];
  const ws   = ["3%","4%","6%","6%","7%","9%","3%","8%","3%","8%","6%","37%"];
  const rows = hands.map((h, idx) => {
    const ev      = h.gto_evaluation || "";
    const rating  = getGtoRating(ev);
    const cls     = rating.startsWith("❌") ? "row-error"
                  : rating.startsWith("⚠️") ? "row-warn"
                  : rating.startsWith("🎲") ? "row-cooler"
                  : rating.startsWith("✅") ? "row-good"
                  : idx % 2 === 0           ? "row-even" : "";
    const heroCards = (h.hero_cards || []).join("");
    const oppCards  = getOpponentCards(h);
    const flop      = (h.streets?.flop?.board || []).join(" ");
    const turnCard  = getBoardCard(h, "turn",  0);
    const riverCard = getBoardCard(h, "river", 0);
    const plNum     = h.hero_result_bb || 0;
    const plColor   = plNum > 0 ? "#27ae60" : plNum < 0 ? "#e74c3c" : "#666";
    return `
    <tr class="${cls}">
      <td>H${esc(h.hand_number)}</td>
      <td style="font-weight:700;text-align:center">${esc(h.hero_position || "")}</td>
      <td>${cardToHtml(heroCards)}</td>
      <td>${cardToHtml(oppCards)}</td>
      <td>${cardToHtml(flop)}</td>
      <td>${esc(makeActionSummary(h, "flop"))}</td>
      <td>${cardToHtml(turnCard)}</td>
      <td>${esc(makeActionSummary(h, "turn"))}</td>
      <td>${cardToHtml(riverCard)}</td>
      <td>${esc(makeActionSummary(h, "river"))}</td>
      <td style="text-align:right;color:${plColor};font-weight:700">${esc(fmtBb(plNum))}</td>
      <td style="white-space:pre-line">${esc(gtoFullDisplay(ev))}</td>
    </tr>`;
  }).join("");
  return `
<h2 class="section-title">③ 全ハンドアクション一覧</h2>
<p class="section-sub">スートカラー: ♠黒 ♥赤 ♦青 ♣緑 ｜ H=Hero V=Villain ｜ ✅良好 ⚠️改善 ❌エラー 🎲クーラー</p>
<table class="data-table table-s3">${colgroup(ws)}${thead(hdrs)}<tbody>${rows}</tbody></table>`;
}

// ── セクション4: GTOエラー分析 ──
function buildSection4Html(hands) {
  const errorHands = hands.filter(h => h.has_gto_error);
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
        <h3>❌ ハンド #${esc(h.hand_number)} — ${esc(h.hero_position)} / ${cardToHtml((h.hero_cards || []).join(""))} → ${esc(fmtBb(h.hero_result_bb))}</h3>
        <p>ボード: ${board ? cardToHtml(board) : "(プリフロップのみ)"}</p>
        <p style="white-space:pre-line">${esc(h.gto_evaluation || "")}</p>
      </div>`;
    }
  }
  return `
<h2 class="section-title">④ GTOエラー分析</h2>
${content}`;
}

// ── セクション5: 改善点（Gemini生成）──
function buildSection5Html(improvementText) {
  // テキストをパースしてカテゴリブロックに変換
  const lines  = improvementText.split("\n").filter(Boolean);
  let   html   = "";
  let   buf    = [];
  let   inCat  = false;
  let   catNum = 0;

  const flushBuf = () => {
    if (!buf.length) return;
    if (inCat) {
      const cls = catNum === 1 ? "" : "warn";
      html += `<div class="improve-category ${cls}"><p>${buf.map(l => esc(l)).join("</p><p>")}</p></div>`;
    } else {
      html += `<div class="improve-block"><p>${buf.map(l => esc(l)).join("</p><p>")}</p></div>`;
    }
    buf = [];
  };

  for (const line of lines) {
    if (line.startsWith("【") || line.startsWith("■") || line.startsWith("①") || line.startsWith("②") || line.startsWith("③")) {
      flushBuf();
      inCat = true;
      catNum++;
      buf.push(line);
    } else {
      buf.push(line);
    }
  }
  flushBuf();

  return `
<h2 class="section-title">⑤ 改善すべき点</h2>
${html}`;
}

// ── セクション6: 強み（Gemini生成）──
function buildSection6Html(strengthText, goodHands) {
  const lines = strengthText.split("\n").filter(Boolean).map(l => `<p>${esc(l)}</p>`).join("\n");
  let goodList = "";
  if (goodHands.length > 0) {
    const items = goodHands.slice(0, 20).map(h =>
      `<li>H${esc(h.hand_number)}: ${esc(h.hero_position)} / ${cardToHtml((h.hero_cards || []).join(""))} → ${esc(gtoFullDisplay(h.gto_evaluation || ""))}</li>`
    ).join("\n");
    goodList = `<div class="good-plays"><h3>✅ ナイスプレイ一覧</h3><ul>${items}</ul></div>`;
  }
  return `
<h2 class="section-title">⑥ 強み・ナイスプレイ</h2>
<div class="strength-block">${lines}</div>
${goodList}`;
}

// ── セクション7: 対戦相手分析（Geminiでプロファイル生成）──
function buildSection7Html(opponentStats) {
  if (!opponentStats.length) {
    return `<h2 class="section-title">⑦ 対戦相手分析</h2><p class="section-sub">データが不足しています。</p>`;
  }
  const profiles = opponentStats.map(s => `
  <div class="opp-profile">
    <div class="opp-profile-header">■ ${esc(s.name)} — ${esc(s.estimated_type)} （${esc(s.hands)}回対戦）</div>
    <div class="opp-profile-body">
      <div class="stats">
        <div class="stat"><div class="val">${esc(s.vpip)}%</div><div class="lbl">VPIP</div></div>
        <div class="stat"><div class="val">${esc(s.pfr)}%</div><div class="lbl">PFR</div></div>
        <div class="stat"><div class="val">${esc(s.three_bet)}%</div><div class="lbl">3BET</div></div>
        <div class="stat"><div class="val">${esc(s.hero_win_rate)}%</div><div class="lbl">対Hero勝率</div></div>
      </div>
      ${s.exploit_strategy ? `<div class="opp-exploit">🎯 エクスプロイト戦略: ${esc(s.exploit_strategy)}</div>` : ""}
      ${s.reads ? `<div class="opp-reads">${esc(s.reads)}</div>` : ""}
    </div>
  </div>`).join("");
  return `
<h2 class="section-title">⑦ 対戦相手分析</h2>
<p class="section-sub">※過去セッション含む累積データ</p>
${profiles}`;
}

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

// ─── Gemini API ───────────────────────────────────────────────────────────────

async function geminiGenerate(genAI, prompt) {
  const model  = genAI.getGenerativeModel({ model: GEMINI_MODEL });
  const result = await model.generateContent(prompt);
  return result.response.text().trim();
}

async function fetchImprovement(genAI, hands) {
  const errorHands = hands.filter(h => h.has_gto_error || getGtoRating(h.gto_evaluation || "").startsWith("⚠️"));
  const evals = hands
    .filter(h => h.gto_evaluation)
    .map(h => `H${h.hand_number} (${h.hero_position}/${(h.hero_cards || []).join("")}):\n${h.gto_evaluation}`)
    .join("\n\n");
  if (!evals) return "データ不足のため分析できません。";
  try {
    return await geminiGenerate(genAI,
`あなたはポーカーのGTOコーチです。
以下の${hands.length}ハンドの評価データをもとに総合分析してください。

${evals}

以下の形式で日本語で回答してください：
【エラーパターン分析】
カテゴリ1: （最頻出パターン名）
・該当ハンド: H○○, H○○...
・内容: （具体的なミスのパターン）
・対策: （具体的な改善ルール）
カテゴリ2: ...（2〜3カテゴリ）

【今すぐ実行すべき改善3点】
① 改善項目: / 具体的ルール: / 期待効果:
② ...
③ ...`);
  } catch (e) {
    console.error("fetchImprovement error:", e.message);
    return `改善点分析エラー: ${e.message}`;
  }
}

async function fetchStrength(genAI, goodHands) {
  if (!goodHands.length) return "良好なプレイが記録されていません。";
  const summaries = goodHands
    .map(h => `H${h.hand_number} ${(h.hero_cards || []).join("")} (${h.hero_position}):\n${h.gto_evaluation}`)
    .join("\n\n");
  try {
    return await geminiGenerate(genAI,
`以下の✅良好・🎲クーラー判定のハンドから、このプレイヤーの強みと再現すべきプレイを分析してください。

${summaries}

以下の形式で：
【強み・再現すべきプレイ】
H○○ {手札}: {シチュエーション} / {評価コメント}
（3〜8件）

【総合コメント】
（このプレイヤーの強みを100字以内で）`);
  } catch (e) {
    console.error("fetchStrength error:", e.message);
    return `強み分析エラー: ${e.message}`;
  }
}

async function fetchOpponentStrategies(genAI, opponentStats, allHands) {
  console.log(`  対戦相手分析: ${opponentStats.length}名を分析中...`);
  const results = await Promise.allSettled(opponentStats.map(async (opp) => {
    const oppHands = allHands
      .filter(h => (h.players || []).some(p => p.name === opp.name && !p.is_hero))
      .slice(0, 15);
    const history = oppHands.map(h => {
      const oppPlayer = (h.players || []).find(p => p.name === opp.name);
      const oppCards  = oppPlayer ? (oppPlayer.hole_cards || []).join("") : "?";
      const board     = [
        ...(h.streets?.flop?.board  || []),
        ...(h.streets?.turn?.board  || []),
        ...(h.streets?.river?.board || []),
      ].join(" ");
      return `H${h.hand_number} Hero:${h.hero_position}/${(h.hero_cards||[]).join("")} vs ${opp.name}/${oppCards} [${board||"PF"}] ${makeActionSummary(h,"preflop")}`;
    }).join("\n");

    const result = await geminiGenerate(genAI,
`対戦相手: ${opp.name}
統計: VPIP ${opp.vpip}% / PFR ${opp.pfr}% / 3BET ${opp.three_bet}% / 対Hero勝率 ${opp.hero_win_rate}%
対戦回数: ${opp.hands}回
ハンド履歴:
${history || "(データなし)"}

以下の形式で分析してください：
タイプ: （例: タイト・バリューベット過剰コール）
エクスプロイト戦略: （具体的な戦略を50字以内で）
読み:
（観察した傾向1）
（観察した傾向2）
（観察した傾向3）`);

    const lines     = result.split("\n").map(l => l.trim()).filter(Boolean);
    const typeLine  = lines.find(l => l.startsWith("タイプ:"));
    const stratLine = lines.find(l => l.startsWith("エクスプロイト戦略:"));
    const readsIdx  = lines.findIndex(l => l.startsWith("読み"));
    if (typeLine)   opp.estimated_type   = typeLine.replace("タイプ:", "").trim();
    if (stratLine)  opp.exploit_strategy = stratLine.replace("エクスプロイト戦略:", "").trim();
    if (readsIdx >= 0) {
      opp.reads = lines.slice(readsIdx + 1)
        .filter(l => l.startsWith("（") || l.match(/^[・\-①②③]/))
        .join("\n");
    }
    if (!opp.exploit_strategy) opp.exploit_strategy = result.slice(0, 100);
    console.log(`    [OK] ${opp.name}`);
  }));

  const failed = results.filter(r => r.status === "rejected");
  if (failed.length) {
    console.error(`  [WARN] ${failed.length}名の分析でエラー`);
    failed.forEach(r => console.error("   ", r.reason?.message));
  }
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
      path:              outFile,
      format:            "A4",
      printBackground:   true,
      margin:            { top: "10mm", bottom: "10mm", left: "10mm", right: "10mm" },
    });
  } finally {
    await browser.close();
  }
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

  let hands   = [];
  let dataDir = null;
  for (const jsonPath of jsonPaths) {
    if (!fs.existsSync(jsonPath)) { console.error(`File not found: ${jsonPath}`); continue; }
    const d = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
    if (d.hands) hands = hands.concat(d.hands);
    if (!dataDir) dataDir = path.dirname(jsonPath);
  }

  hands.sort((a, b) => (a.datetime || "").localeCompare(b.datetime || ""));

  const today   = new Date().toISOString().slice(0, 10);
  const dates   = hands.map(h => h.datetime?.slice(0, 10)).filter(Boolean);
  const minDate = dates.length ? dates.reduce((a, b) => a < b ? a : b) : today;
  const maxDate = dates.length ? dates.reduce((a, b) => a > b ? a : b) : today;

  const totalPL       = hands.reduce((s, h) => s + (h.hero_result_bb || 0), 0);
  const posStats      = calcPositionStats(hands);

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    console.error("[ERROR] GEMINI_API_KEY が .env に設定されていません");
    process.exit(1);
  }
  const genAI = new GoogleGenerativeAI(apiKey);

  console.log("  セクション5をGemini APIで生成中...");
  const improvementText = await fetchImprovement(genAI, hands);

  const html = buildFullHtml([
    buildTitleHtml(minDate, maxDate, hands.length, totalPL),
    buildSection1Html(posStats),
    buildSection2Html(hands),
    buildSection3Html(hands),
    buildSection4Html(hands),
    buildSection5Html(improvementText),
  ]);

  const dateStr = minDate === maxDate ? minDate : `${minDate}_${maxDate}`;
  fs.mkdirSync(outputDir, { recursive: true });
  const outFile = path.join(outputDir, `GTO_Report_${dateStr}.pdf`);

  console.log("  PDF生成中...");
  await generatePdf(html, outFile);
  console.log(`  Generated: ${outFile}`);
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
