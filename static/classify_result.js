// classify_result.js — classify_result.html の全 JS ロジック
// Jinja2 変数（_CHIP_DATA / JOB_ID）はインライン <script> で先行注入される

// ─── チャート ─────────────────────────────────────────────────────────────────

function buildChart() {
  const ctx = document.getElementById('chipChart');
  if (!ctx || !_CHIP_DATA.length) return;
  const labels = _CHIP_DATA.map(p => 'H' + p.x);
  const values = _CHIP_DATA.map(p => p.y);
  const ptColors = _CHIP_DATA.map(p =>
    p.line === 'blue' ? '#1a6abf' : p.line === 'red' ? '#c0392b' : '#bbb'
  );
  const ptRadius = _CHIP_DATA.map(p => p.line === 'preflop_only' ? 2 : 4);
  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: '累積損益 (bb)',
        data: values,
        borderColor: '#1a1a2e',
        borderWidth: 2,
        pointBackgroundColor: ptColors,
        pointRadius: ptRadius,
        pointHoverRadius: 6,
        tension: 0.1,
        fill: {
          target: 'origin',
          above: 'rgba(46,125,50,0.07)',
          below: 'rgba(192,57,43,0.07)',
        },
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const p = _CHIP_DATA[ctx.dataIndex];
              const sign = p.y >= 0 ? '+' : '';
              return `累積: ${sign}${p.y.toFixed(2)}bb  [${p.line}]`;
            }
          }
        }
      },
      scales: {
        x: { ticks: { maxTicksLimit: 20, font: { size: 10 } }, grid: { color: '#f0f0f0' } },
        y: {
          ticks: { font: { size: 10 }, callback: v => v + 'bb' },
          grid: { color: '#f0f0f0' },
          beginAtZero: false,
        }
      }
    }
  });
}

function toggleSection(id) {
  const el = document.getElementById(id);
  el.classList.toggle('collapsed');
  const btn = el.previousElementSibling.querySelector('.toggle-btn');
  if (btn) btn.textContent = el.classList.contains('collapsed') ? '▼' : '▲';
}

let _chartBuilt = false;
function switchTab(id, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  if (id === 'tab-chart' && !_chartBuilt) { buildChart(); _chartBuilt = true; }
}

// ─── カート基本動作（Firebase 非依存・常に動作） ──────────────────────────────

window._cartSet = new Set();

window.toggleCart = function(handNum) {
  handNum = parseInt(handNum);
  if (window._cartSet.has(handNum)) window._cartSet.delete(handNum);
  else window._cartSet.add(handNum);
  var inCart = window._cartSet.has(handNum);
  var card = document.querySelector('.hand-card[data-hnum="' + handNum + '"]');
  if (card) {
    card.classList.toggle('in-cart', inCart);
    var btn = card.querySelector('.cart-add-btn');
    if (btn) btn.textContent = inCart ? '✓カート' : '🛒';
  }
  var count = window._cartSet.size;
  var badge = document.getElementById('cart-badge');
  if (badge) { badge.textContent = count || ''; badge.classList.toggle('hidden', !count); }
  var fb = document.getElementById('footer-cart-badge');
  if (fb) fb.textContent = count ? ' (' + count + ')' : '';
  _renderCartDrawer();
  if (typeof window._fullRenderCart === 'function') window._fullRenderCart();
};

// ── トークン見積もり ──────────────────────────────────────────────────────────
var DETAIL_TOK_PER_HAND  = 370;
var EXPLAIN_TOK_PER_HAND = 1700;
var GROQ_FREE_TPM        = 14400;

function _updateTokenEstimate(count) {
  var el       = document.getElementById('token-estimate');
  var detailEl = document.getElementById('est-detail-tok');
  var statusEl = document.getElementById('est-groq-status');
  if (!el) return;
  if (count === 0) { el.style.display = 'none'; return; }
  el.style.display = '';
  var detailTok = count * DETAIL_TOK_PER_HAND;
  if (detailEl) detailEl.textContent = count + '手 × ' + DETAIL_TOK_PER_HAND + ' tok ≈ ' + detailTok.toLocaleString() + ' tok';
  if (statusEl) {
    if (detailTok <= GROQ_FREE_TPM) {
      statusEl.innerHTML = '⚡ Groq無料枠 ' + GROQ_FREE_TPM.toLocaleString() + ' tok/分 → <span style="color:#2e7d32;font-weight:700">余裕で収まります</span>';
    } else {
      var batches = Math.ceil(detailTok / GROQ_FREE_TPM);
      statusEl.innerHTML = '⚡ Groq無料枠 ' + GROQ_FREE_TPM.toLocaleString() + ' tok/分 → <span style="color:#c0392b;font-weight:700">複数回に分けて実行推奨</span>（約' + batches + '回）';
    }
  }
}

function _renderCartDrawer() {
  var count      = window._cartSet.size;
  var emptyEl    = document.getElementById('cart-empty');
  var itemsEl    = document.getElementById('cart-items');
  var analyzeBtn = document.getElementById('cart-analyze-btn');
  if (!itemsEl) return;
  if (count === 0) {
    if (emptyEl) emptyEl.style.display = '';
    itemsEl.innerHTML = '';
    if (analyzeBtn) analyzeBtn.disabled = true;
    _updateTokenEstimate(0);
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  if (analyzeBtn) analyzeBtn.disabled = false;
  _updateTokenEstimate(count);
  var sorted = Array.from(window._cartSet).sort(function(a,b){return a-b;});
  itemsEl.innerHTML = sorted.map(function(n) {
    var el    = document.querySelector('.hand-card[data-hnum="' + n + '"]');
    var pos   = el ? (el.dataset.pos   || '?') : '?';
    var cards = el ? (el.dataset.cards || '') : '';
    var pl    = el ? (el.dataset.pl    || '') : '';
    var plNum = el ? parseFloat(el.dataset.plNum || '0') : 0;
    var plCls = plNum > 0 ? 'pos' : plNum < 0 ? 'neg' : 'zero';
    return '<div class="cart-item">'
      + '<span class="cart-item-num">H' + n + '</span>'
      + '<span class="cart-item-info"><span class="cart-item-pos">' + pos + '</span> ' + cards + '</span>'
      + '<span class="cart-item-pl ' + plCls + '">' + pl + '</span>'
      + '<button class="cart-item-del" onclick="toggleCart(' + n + ')" title="削除">✕</button>'
      + '</div>';
  }).join('');
}

window.toggleCartPanel = function() {
  document.getElementById('cart-panel').classList.toggle('open');
  document.getElementById('cart-overlay').classList.toggle('open');
};
window.closeCartPanel = function() {
  document.getElementById('cart-panel').classList.remove('open');
  document.getElementById('cart-overlay').classList.remove('open');
};

window.setDesign = function(d) {
  document.body.className = document.body.className
    .replace(/\bdesign-[a-c]\b/g, '').trim() + ' design-' + d;
  ['a','b','c'].forEach(function(x) {
    var el = document.getElementById('ds-' + x);
    if (el) el.classList.toggle('active', x === d);
  });
  localStorage.setItem('cart_design', d);
};

// ページロード時にデザインを即時適用
setDesign(localStorage.getItem('cart_design') || 'a');

// ─── Firebase + Cart + AI JS ──────────────────────────────────────────────────

let _user         = null;
const cartSet     = window._cartSet;
let _syncTimer    = null;
let _cartLabels   = {};
let _userSettings = {};
let _geminiResults = {};

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 4色スート（♠黒・♥赤・♦青・♣緑）
var _SUIT_COLORS = {'♠':'#1a1a1a','♥':'#d32f2f','♦':'#1565c0','♣':'#2e7d32'};
function cardHtml(s) {
  if (!s) return '';
  return String(s).replace(/([23456789TJQKA]{1,2})([\u2660\u2665\u2666\u2663])/g, function(_, rank, suit) {
    var c = _SUIT_COLORS[suit] || '#000';
    return esc(rank) + '<span style="color:' + c + '">' + suit + '</span>';
  });
}

// ── Firebase 初期化 ──────────────────────────────────────────────────────────
(async () => {
  try {
    const { initializeApp, getApps, getApp } = await import('https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js');
    const { getAuth, onAuthStateChanged } = await import('https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js');
    let app;
    if (getApps().length > 0) {
      app = getApp();
    } else {
      const r = await fetch('/api/firebase-config');
      const cfg = await r.json();
      app = initializeApp(cfg);
    }
    const _auth = getAuth(app);
    onAuthStateChanged(_auth, async user => {
      _user = user;
      if (user) {
        buildHandLabels();
        await Promise.all([loadCart(), loadSettings()]);
      }
    });
  } catch(e) { console.warn('Firebase init failed:', e); }
})();

async function getToken() {
  if (!_user) return null;
  return _user.getIdToken();
}

function buildHandLabels() {
  document.querySelectorAll('.hand-card[data-hnum]').forEach(el => {
    const num = parseInt(el.dataset.hnum);
    const cat = el.querySelector('.badge-cat')?.textContent || el.dataset.line || '';
    _cartLabels[num] = cat;
  });
}

// ── カート読み込み ────────────────────────────────────────────────────────────
async function loadCart() {
  const token = await getToken();
  if (!token) return;
  try {
    const r = await fetch(`/api/cart/${JOB_ID}`, {
      headers: {'Authorization': `Bearer ${token}`}
    });
    const data = await r.json();
    cartSet.clear();
    (data.hand_numbers || []).map(Number).forEach(n => cartSet.add(n));
    if (data.gemini_results && Object.keys(data.gemini_results).length > 0) {
      _geminiResults = data.gemini_results;
      renderAiSection();
    }
    renderCart();
  } catch(e) { console.warn('cart load error', e); }
}

// ── ユーザー設定読み込み ──────────────────────────────────────────────────────
async function loadSettings() {
  const token = await getToken();
  if (!token) return;
  try {
    const r = await fetch('/api/user/settings', {
      headers: {'Authorization': `Bearer ${token}`}
    });
    _userSettings = await r.json();
    updateApiKeyUI();
    if (_userSettings.needs_api_auto_cart !== false) {
      autoAddNeedsApi();
    }
  } catch(e) {
    autoAddNeedsApi();
  }
}

function autoAddNeedsApi() {
  let changed = false;
  document.querySelectorAll('.hand-card[data-needs-api="1"]').forEach(el => {
    const num = parseInt(el.dataset.hnum);
    if (!cartSet.has(num)) { cartSet.add(num); changed = true; }
  });
  if (changed) { renderCart(); scheduleSync(); }
}

// ── カート追加/削除（Firebase拡張版） ────────────────────────────────────────
window.toggleCart = function(handNum) {
  handNum = parseInt(handNum);
  if (cartSet.has(handNum)) { cartSet.delete(handNum); }
  else { cartSet.add(handNum); }
  renderCart();
  scheduleSync();
};

function scheduleSync() {
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(syncCart, 600);
}

async function syncCart() {
  const token = await getToken();
  if (!token) return;
  try {
    await fetch(`/api/cart/${JOB_ID}/hands`, {
      method: 'POST',
      headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
      body: JSON.stringify({hand_numbers: [...cartSet]})
    });
  } catch(e) { console.warn('cart sync error', e); }
}

// ── レンダリング ──────────────────────────────────────────────────────────────
function renderCart() {
  const count = cartSet.size;
  const badge = document.getElementById('cart-badge');
  badge.textContent = count || '';
  badge.classList.toggle('hidden', count === 0);
  const footerBadge = document.getElementById('footer-cart-badge');
  if (footerBadge) footerBadge.textContent = count ? ` (${count})` : '';

  document.querySelectorAll('.hand-card[data-hnum]').forEach(el => {
    const num = parseInt(el.dataset.hnum);
    el.classList.toggle('in-cart', cartSet.has(num));
    const btn = el.querySelector('.cart-add-btn');
    if (btn) btn.textContent = cartSet.has(num) ? '✓カート' : '🛒';
  });

  const itemsEl = document.getElementById('cart-items');
  const emptyEl = document.getElementById('cart-empty');
  if (count === 0) {
    emptyEl.style.display = '';
    itemsEl.innerHTML = '';
    document.getElementById('cart-analyze-btn').disabled = true;
    _updateTokenEstimate(0);
    return;
  }
  emptyEl.style.display = 'none';
  document.getElementById('cart-analyze-btn').disabled = false;
  _updateTokenEstimate(count);
  const sorted = [...cartSet].sort((a,b) => a-b);
  itemsEl.innerHTML = sorted.map(n => {
    const el    = document.querySelector(`.hand-card[data-hnum="${n}"]`);
    const pos   = el?.dataset.pos   || '?';
    const cards = el?.dataset.cards || '';
    const pl    = el?.dataset.pl    || '';
    const plNum = parseFloat(el?.dataset.plNum || '0');
    const plCls = plNum > 0 ? 'pos' : plNum < 0 ? 'neg' : 'zero';
    const hasAi = _geminiResults[String(n)] ? ' 🤖' : '';
    return `<div class="cart-item">
      <span class="cart-item-num">H${n}${hasAi}</span>
      <span class="cart-item-info"><span class="cart-item-pos">${pos}</span> ${cards}</span>
      <span class="cart-item-pl ${plCls}">${pl}</span>
      <button class="cart-item-del" onclick="toggleCart(${n})" title="削除">✕</button>
    </div>`;
  }).join('');
}

window._fullRenderCart = renderCart;

// ── テキストをフィールドマップに分解 ─────────────────────────────────────────
function parseAiText(text) {
  const fields = {};
  const lines = (text || '').split('\n');
  for (const line of lines) {
    const m = line.match(/^([^:]+):\s*(.*)/);
    if (m) fields[m[1].trim()] = m[2].trim();
  }
  return fields;
}

function ratingClass(rating) {
  if (!rating) return '';
  if (rating.startsWith('✅')) return 'good';
  if (rating.startsWith('⚠️')) return 'warn';
  if (rating.startsWith('❌')) return 'error';
  if (rating.startsWith('🎲')) return 'cooler';
  return '';
}

// ── AI解析結果セクション描画 ──────────────────────────────────────────────────
function _oppDataToHtml(oppStr) {
  // "BTN:AhKh,CO:" → 対戦相手表示HTML（4色スート）
  if (!oppStr) return '';
  return oppStr.split(',').filter(Boolean).map(part => {
    const idx   = part.indexOf(':');
    const pos   = idx >= 0 ? part.slice(0, idx) : part;
    const cards = idx >= 0 ? part.slice(idx + 1) : '';
    return `<span class="ai-pos" style="font-size:10px">${esc(pos)}</span>${cards ? ` <span style="font-size:12px">${cardHtml(cards)}</span>` : ''}`;
  }).join(' <span style="color:#ccc">|</span> ');
}

function renderAiSection() {
  const keys = Object.keys(_geminiResults);
  if (!keys.length) return;
  const section = document.getElementById('ai-section');
  section.style.display = '';
  document.getElementById('ai-count').textContent = `(${keys.length}手)`;
  const list = document.getElementById('ai-results-list');
  list.innerHTML = keys.sort((a,b) => parseInt(a)-parseInt(b)).map(k => {
    const r    = _geminiResults[k];
    const text = r.text || '評価なし';
    const cat  = r.category || '';
    const f    = parseAiText(text);

    const el    = document.querySelector(`.hand-card[data-hnum="${k}"]`);
    const pos   = el?.dataset.pos   || '';
    const cards = el?.dataset.cards || '';
    const plStr = el?.dataset.pl    || '';
    const plNum = parseFloat(el?.dataset.plNum || '0');
    const board = el?.dataset.board || '';
    const opp   = el?.dataset.opp   || '';
    const is3bt = el?.dataset['3bet'] === '1';
    const plCls = plNum > 0 ? 'pos' : plNum < 0 ? 'neg' : 'zero';

    const rating   = f['GTO評価'] || '';
    const ichi     = f['一言'] || '';
    const detail   = f['詳細'] || '';
    const kaizen   = f['代替ライン'] || '';
    const evLoss   = f['EV損失推定'] || '';
    const handRead = f['ハンドリーディング'] || '';
    const oppGto   = f['相手GTOずれ'] || '';
    const rCls     = ratingClass(rating);
    const uid      = `ai-${k}`;
    const oppHtml  = _oppDataToHtml(opp);

    return `<div class="ai-result-item">
      <div class="ai-result-head">
        <span class="ai-hand-num">H${k}</span>
        ${is3bt ? '<span class="badge-3bet">3BET</span>' : ''}
        ${rating ? `<span class="ai-rating-badge ${rCls}">${esc(rating)}</span>` : ''}
        ${cat ? `<span class="ai-category">${esc(cat)}</span>` : ''}
      </div>
      <div class="ai-hand-info">
        ${pos   ? `<span class="ai-pos">${esc(pos)}</span>` : ''}
        ${cards ? `<span class="ai-cards">${cardHtml(cards)}</span>` : ''}
        ${oppHtml ? `<span class="vs-label">vs</span> ${oppHtml}` : ''}
        ${board ? `<span class="ai-board">/ ${cardHtml(board)}</span>` : ''}
        ${plStr ? `<span class="ai-pl ${plCls}">${esc(plStr)}</span>` : ''}
      </div>
      ${ichi   ? `<div class="ai-ichi">${esc(ichi)}</div>` : ''}
      ${detail ? `<div class="ai-detail">${esc(detail)}</div>` : ''}
      ${(kaizen || evLoss) ? `<div class="ai-kaizen">${kaizen ? esc(kaizen) : ''}${evLoss ? ` <b>(${esc(evLoss)})</b>` : ''}</div>` : ''}
      ${handRead ? `<button class="ai-collapse-toggle" onclick="toggleAiCollapse('${uid}-hr')">▶ ハンドリーディング</button>
        <div class="ai-collapse-body" id="${uid}-hr">${esc(handRead)}</div>` : ''}
      ${oppGto ? `<button class="ai-collapse-toggle" onclick="toggleAiCollapse('${uid}-og')">▶ 相手GTOずれ</button>
        <div class="ai-collapse-body" id="${uid}-og">${esc(oppGto)}</div>` : ''}
      ${f['Heroレンジ'] ? `<button class="ai-collapse-toggle" onclick="toggleAiCollapse('${uid}-rep')">▶ Hero表現レンジ</button>
        <div class="ai-collapse-body" id="${uid}-rep">${esc(f['Heroレンジ'])}</div>` : ''}
      <div style="margin-top:8px">
        <button class="ai-explain-btn" id="${uid}-exbtn" onclick="fetchExplain(${k}, '${uid}')">📖 詳細解説</button>
      </div>
      <div class="ai-explain-panel" id="${uid}-expanel">${r.explain ? esc(r.explain) : ''}</div>
    </div>`;
  }).join('');

  // インラインパネルも更新・既存 explain を復元表示
  keys.forEach(k => {
    const r = _geminiResults[k];
    if (!r) return;
    renderAiInHandCard(parseInt(k), r);
    if (r.explain) {
      const uid   = `ai-${k}`;
      const panel = document.getElementById(`${uid}-expanel`);
      const btn   = document.getElementById(`${uid}-exbtn`);
      if (panel) panel.classList.add('open');
      if (btn)   btn.textContent = '📖 詳細解説を隠す';
    }
  });
}

// ── AI インラインパネル描画（hand-card 内・デフォルト折りたたみ） ───────────────
function renderAiInHandCard(hnum, result) {
  const container = document.getElementById('hai-' + hnum);
  if (!container) return;
  const text    = result.text || '';
  const f       = parseAiText(text);
  const rating  = f['GTO評価'] || '';
  const ichi    = f['一言']    || '';
  const detail  = f['詳細']   || '';
  const kaizen  = f['代替ライン'] || '';
  const rCls    = ratingClass(rating);
  const bodyId  = 'hai-body-' + hnum;
  // ヘッダー部（常に表示）: バッジ + 一言 + 展開トグル
  // ボディ部（デフォルト折りたたみ）: 詳細 + 代替ライン
  container.innerHTML =
    '<div class="hai-head" onclick="toggleHaiBody(\'' + bodyId + '\', this)">'
    + (rating ? '<span class="ai-rating-badge ' + rCls + '" style="font-size:10px">' + esc(rating) + '</span>' : '')
    + (ichi   ? '<span class="hai-ichi">' + esc(ichi) + '</span>' : '')
    + '<span class="hai-toggle">▶</span>'
    + '</div>'
    + '<div class="hai-body" id="' + bodyId + '">'
    + (detail ? '<div class="hai-detail">' + esc(detail) + '</div>' : '')
    + (kaizen ? '<div class="hai-kaizen">' + esc(kaizen) + '</div>' : '')
    + '</div>';
}

window.toggleHaiBody = function(bodyId, headEl) {
  const body = document.getElementById(bodyId);
  if (!body) return;
  body.classList.toggle('open');
  const btn = headEl ? headEl.querySelector('.hai-toggle') : null;
  if (btn) btn.textContent = body.classList.contains('open') ? '▲' : '▶';
};

window.toggleAiCollapse = function(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
  const btn = el.previousElementSibling;
  if (btn) btn.textContent = btn.textContent.replace(/^[▶▼]/, el.classList.contains('open') ? '▼' : '▶');
};

// ── 詳細解説（explainモード）取得 ─────────────────────────────────────────────
window.fetchExplain = async function(handNum, uid) {
  const panel = document.getElementById(`${uid}-expanel`);
  const btn   = document.getElementById(`${uid}-exbtn`);
  if (!panel || !btn) return;

  // テキスト既ロード済みならフェッチせずトグル
  if (panel.textContent.trim()) {
    if (panel.classList.contains('open')) {
      panel.classList.remove('open');
      btn.textContent = '📖 詳細解説';
    } else {
      panel.classList.add('open');
      btn.textContent = '📖 詳細解説を隠す';
    }
    return;
  }

  const token = await getToken();
  if (!token) { alert('ログインしてください'); return; }

  btn.disabled = true;
  btn.textContent = '⏳ 解説生成中...';

  try {
    const resp = await fetch(`/api/cart/${JOB_ID}/explain`, {
      method: 'POST',
      headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
      body: JSON.stringify({hand_number: handNum})
    });
    const d = await resp.json();
    if (!resp.ok || !d.ok) {
      alert('エラー: ' + (d.error || resp.statusText));
      btn.disabled = false;
      btn.textContent = '📖 詳細解説';
      return;
    }
    panel.textContent = d.explain || '';
    panel.classList.add('open');
    const key = String(handNum);
    if (_geminiResults[key]) _geminiResults[key].explain = d.explain;
    btn.textContent = '📖 詳細解説を隠す';
  } catch(e) {
    alert('エラー: ' + e.message);
    btn.textContent = '📖 詳細解説';
  } finally {
    btn.disabled = false;
  }
};

// ── APIキー UI 更新 ───────────────────────────────────────────────────────────
function updateApiKeyUI() {
  const section  = document.getElementById('api-key-section');
  const statusEl = document.getElementById('api-key-status');
  if (!section) return;
  section.style.display = '';
  if (_userSettings.has_key) {
    statusEl.textContent = `設定済み: ${_userSettings.key_masked || '****'}`;
    statusEl.style.color = '#2e7d32';
  } else {
    statusEl.textContent = 'APIキーを設定してください';
    statusEl.style.color = '#c0392b';
  }
}

// ── APIキー保存 ───────────────────────────────────────────────────────────────
window.saveApiKey = async function() {
  const key = document.getElementById('api-key-input')?.value.trim();
  if (!key) return;
  const token = await getToken();
  if (!token) { alert('ログインしてください'); return; }
  const statusEl = document.getElementById('api-key-status');
  statusEl.textContent = '保存中...';
  statusEl.style.color = '#888';
  try {
    const r = await fetch('/api/user/settings', {
      method: 'PUT',
      headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: key})
    });
    if (!r.ok) {
      const d = await r.json().catch(()=>({}));
      statusEl.textContent = 'HTTPエラー ' + r.status + ': ' + (d.error || r.statusText);
      statusEl.style.color = '#c0392b';
      return;
    }
    const d = await r.json();
    if (!d.ok) {
      statusEl.textContent = 'エラー: ' + (d.error || '保存失敗');
      statusEl.style.color = '#c0392b';
      return;
    }
    const vr = await fetch('/api/user/settings', {headers: {'Authorization': `Bearer ${token}`}});
    const vd = await vr.json();
    const saved_hint = vd.key_masked || ('****' + key.slice(-4));
    _userSettings = vd;
    document.getElementById('api-key-input').value = '';
    statusEl.textContent = `保存完了: ${saved_hint}`;
    statusEl.style.color = '#2e7d32';
  } catch(e) {
    statusEl.textContent = 'エラー: ' + e.message;
    statusEl.style.color = '#c0392b';
  }
};

// ── ドロワー開閉 ──────────────────────────────────────────────────────────────
window.toggleCartPanel = () => {
  const panel   = document.getElementById('cart-panel');
  const overlay = document.getElementById('cart-overlay');
  panel.classList.toggle('open');
  overlay.classList.toggle('open');
};
window.closeCartPanel = () => {
  document.getElementById('cart-panel').classList.remove('open');
  document.getElementById('cart-overlay').classList.remove('open');
};

// ── AI解析実行（SSEストリーミング） ──────────────────────────────────────────
window.startAnalyze = async function() {
  const token = await getToken();
  if (!token) { alert('ログインしてください'); return; }

  const btn = document.getElementById('cart-analyze-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 解析中...';

  try {
    const resp = await fetch(`/api/cart/${JOB_ID}/analyze`, {
      method: 'POST',
      headers: {'Authorization': `Bearer ${token}`}
    });

    if (!resp.ok) {
      let errMsg = resp.statusText;
      try { errMsg = (await resp.json()).error || errMsg; } catch(_) {}
      if (errMsg.includes('APIキー')) {
        updateApiKeyUI();
        document.getElementById('cart-panel')?.classList.add('open');
        document.getElementById('cart-overlay')?.classList.add('open');
      } else {
        alert('エラー: ' + errMsg);
      }
      btn.disabled = false;
      btn.textContent = '⚡ 解析を実行';
      return;
    }

    const reader    = resp.body.getReader();
    const decoder   = new TextDecoder();
    let buffer      = '';
    let doneCount   = 0;
    let totalCount  = 0;
    const startTime = Date.now();

    const aiSec       = document.getElementById('ai-section');
    if (aiSec) aiSec.style.display = '';
    const progressArea = document.getElementById('ai-progress-area');
    const progressFill = document.getElementById('ai-progress-fill');
    const progressText = document.getElementById('ai-progress-text');
    if (progressArea) progressArea.style.display = '';

    function updateProgress() {
      const pct     = totalCount > 0 ? Math.round(doneCount / totalCount * 100) : 0;
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      if (progressFill) progressFill.style.width = pct + '%';
      if (progressText) progressText.textContent = `${doneCount}/${totalCount || '?'} 手完了 (${elapsed}秒経過)`;
      btn.textContent = `⏳ ${doneCount}/${totalCount || '?'} 解析中...`;
    }

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch(_) { continue; }
        if (ev.type === 'batch') {
          totalCount = ev.total || totalCount;
          for (const r of (ev.results || [])) {
            const result = {text: r.text, category: r.category};
            _geminiResults[String(r.hand_number)] = result;
            renderAiInHandCard(r.hand_number, result);
            doneCount++;
          }
          updateProgress();
          renderAiSection();
          renderCart();
        } else if (ev.type === 'done') {
          doneCount  = ev.total || doneCount;
          totalCount = ev.total || totalCount;
          updateProgress();
          if (progressArea) progressArea.style.display = 'none';
          btn.textContent = '✅ 解析完了';
          btn.disabled = false;
          renderAiSection();
          renderCart();
          const aiPdfArea = document.getElementById('ai-pdf-area');
          if (aiPdfArea) aiPdfArea.style.display = '';
          window.closeCartPanel();
          if (aiSec) aiSec.scrollIntoView({behavior: 'smooth', block: 'start'});
        } else if (ev.type === 'error') {
          throw new Error(ev.message || '解析エラー');
        }
      }
    }
  } catch(e) {
    const progressArea = document.getElementById('ai-progress-area');
    if (progressArea) progressArea.style.display = 'none';
    alert('解析エラー: ' + e.message);
    btn.disabled = false;
    btn.textContent = '⚡ 解析を実行';
  }
};

// ─── フィルター/ソート ────────────────────────────────────────────────────────

var _currentFilter  = 'all';
var _currentSort    = null;       // null = Pythonデフォルト順
var _cardOrigParent = {};         // hnum -> original parentElement

var _POS_ORDER_JS = ['UTG','UTG+1','LJ','HJ','CO','BTN','SB','BB'];

function _saveOriginalParents() {
  if (Object.keys(_cardOrigParent).length) return;
  document.querySelectorAll('#hand-list-body .hand-card').forEach(function(c) {
    _cardOrigParent[c.dataset.hnum] = c.parentNode;
  });
}

function _getOrCreateFlatView(line) {
  var id = line + '-flat-view';
  var el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.style.cssText = 'padding:0 10px';
    var area = document.getElementById(line + '-hands-area');
    if (area) area.appendChild(el);
  }
  return el;
}

function _applyFilter() {
  document.querySelectorAll('#hand-list-body .hand-card').forEach(function(c) {
    var show = true;
    if      (_currentFilter === 'blue') show = c.dataset.line === 'blue';
    else if (_currentFilter === 'red')  show = c.dataset.line === 'red';
    else if (_currentFilter === '3bet') show = c.dataset['3bet'] === '1';
    else if (_currentFilter === 'ai')   show = !!(_geminiResults && _geminiResults[c.dataset.hnum]);
    c.style.display = show ? '' : 'none';
  });
  // 空の cat-group を非表示
  document.querySelectorAll('#hand-list-body .hand-cat-group').forEach(function(grp) {
    var any = Array.from(grp.querySelectorAll('.hand-card')).some(function(c) {
      return c.style.display !== 'none';
    });
    grp.style.display = any ? '' : 'none';
  });
}

function _applySort() {
  ['blue', 'red'].forEach(function(line) {
    var area = document.getElementById(line + '-hands-area');
    if (!area) return;
    if (!_currentSort) {
      // デフォルト順に戻す: カードを元の親へ移動
      var flat = document.getElementById(line + '-flat-view');
      if (flat) {
        Array.from(flat.querySelectorAll('.hand-card')).forEach(function(c) {
          var orig = _cardOrigParent[c.dataset.hnum];
          if (orig) orig.appendChild(c);
        });
        flat.style.display = 'none';
      }
      area.querySelectorAll('.hand-cat-group').forEach(function(g) { g.style.display = ''; });
    } else {
      _saveOriginalParents();
      // area内の全 hand-card を収集（cat-group + flat-view 両方から）
      var cards = Array.from(area.querySelectorAll('.hand-card'));
      cards.sort(function(a, b) {
        if (_currentSort === 'pl-asc')  return parseFloat(a.dataset.plNum || 0) - parseFloat(b.dataset.plNum || 0);
        if (_currentSort === 'pl-desc') return parseFloat(b.dataset.plNum || 0) - parseFloat(a.dataset.plNum || 0);
        if (_currentSort === 'pos') {
          var ai = _POS_ORDER_JS.indexOf(a.dataset.pos);
          var bi = _POS_ORDER_JS.indexOf(b.dataset.pos);
          return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
        }
        return 0;
      });
      var flat = _getOrCreateFlatView(line);
      flat.style.display = '';
      flat.innerHTML = '';
      cards.forEach(function(c) { flat.appendChild(c); });
      area.querySelectorAll('.hand-cat-group').forEach(function(g) { g.style.display = 'none'; });
    }
  });
}

window.applyFilter = function(type, btn) {
  _currentFilter = type;
  document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  _applyFilter();
};

window.applySort = function(type, btn) {
  // 同じボタンを再クリック → デフォルト順に戻す
  if (_currentSort === type) {
    _currentSort = null;
    document.querySelectorAll('.sort-btn').forEach(function(b) { b.classList.remove('active'); });
  } else {
    _currentSort = type;
    document.querySelectorAll('.sort-btn').forEach(function(b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
  }
  _applySort();
  _applyFilter();
};

// ── デザイン切替 ──────────────────────────────────────────────────────────────
window.setDesign = (d) => {
  document.body.className = document.body.className
    .replace(/\bdesign-[a-c]\b/g, '').trim() + ` design-${d}`;
  ['a','b','c'].forEach(x => {
    document.getElementById(`ds-${x}`).classList.toggle('active', x === d);
  });
  localStorage.setItem('cart_design', d);
};
