"use strict";

const SERVER_URL = "http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com";

function sendBg(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(msg, resp => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(resp);
    });
  });
}

function setStatus(msg, type = "") {
  const el = document.getElementById("main-status");
  el.textContent = msg;
  el.className = "status" + (type ? " " + type : "");
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16).replace(/-/g, "/");
}

function fmtPlaytime(startMs) {
  if (!startMs) return "—";
  const mins = Math.floor((Date.now() - startMs) / 60000);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

// ─── ログイン後UI初期化 ──────────────────────────────────────────────────────

async function initMain(email) {
  document.getElementById("view-login").style.display = "none";
  document.getElementById("view-main").style.display  = "block";
  document.getElementById("user-email").textContent   = email || "";

  chrome.action.setBadgeText({ text: "" });

  await Promise.all([loadHandCount(), loadHistory(), updatePlaytime(), loadSettings()]);
  setInterval(updatePlaytime, 10000);
}

async function loadHandCount() {
  const btn = document.getElementById("btn-analyze");
  try {
    const resp = await sendBg({ type: "GET_ID_TOKEN" });
    if (!resp?.token) return;
    const res = await fetch(SERVER_URL + "/api/hands/stats", {
      headers: { "Authorization": "Bearer " + resp.token }
    });
    if (!res.ok) return;
    const data = await res.json();
    const count = data.count || 0;
    document.getElementById("hand-count").textContent = count.toLocaleString();
    btn.disabled = count === 0;
  } catch (e) {
    document.getElementById("hand-count").textContent = "—";
  }
}

async function loadHistory() {
  const list = document.getElementById("history-list");
  try {
    const stored = await chrome.storage.local.get(["analysisHistory"]);
    const history = stored.analysisHistory || [];
    if (history.length === 0) {
      list.innerHTML = '<div class="history-empty">まだ解析履歴がありません</div>';
      return;
    }
    list.innerHTML = history.map(h => `
      <div class="history-item">
        <span class="history-date">${fmtDate(h.at)}</span>
        <span class="history-hands">${h.hands}手</span>
        <a class="history-link" href="${h.url}" target="_blank">結果 →</a>
      </div>
    `).join("");
  } catch (e) {
    list.innerHTML = '<div class="history-empty">—</div>';
  }
}

async function updatePlaytime() {
  const stored = await chrome.storage.local.get(["sessionStartAt"]);
  document.getElementById("playtime-display").textContent = fmtPlaytime(stored.sessionStartAt);
}

// ─── 設定 ────────────────────────────────────────────────────────────────────

async function loadSettings() {
  const s = await chrome.storage.local.get(["autoMode", "autoThreshold", "playtimeNotify"]);
  document.getElementById("sel-auto-mode").value = s.autoMode      ?? "background";
  document.getElementById("sel-threshold").value  = s.autoThreshold ?? 100;
  document.getElementById("sel-playtime").value   = s.playtimeNotify ?? 0;
}

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const settings = {
    autoMode:       document.getElementById("sel-auto-mode").value,
    autoThreshold:  parseInt(document.getElementById("sel-threshold").value),
    playtimeNotify: parseInt(document.getElementById("sel-playtime").value),
  };
  await chrome.storage.local.set(settings);
  sendBg({ type: "SETTINGS_UPDATED", settings });

  const flash = document.getElementById("saved-flash");
  flash.textContent = "✓ 保存しました";
  setTimeout(() => { flash.textContent = ""; }, 2000);
});

// ─── 設定パネル トグル ────────────────────────────────────────────────────────

document.getElementById("btn-options").addEventListener("click", () => {
  const panel = document.getElementById("view-settings");
  const btn   = document.getElementById("btn-options");
  const isOpen = panel.classList.toggle("open");
  btn.classList.toggle("active", isOpen);
  btn.textContent = isOpen ? "✕ 閉じる" : "⚙ 設定";
});

// ─── 解析ボタン ──────────────────────────────────────────────────────────────

document.getElementById("btn-analyze").addEventListener("click", async () => {
  const btn   = document.getElementById("btn-analyze");
  const range = document.getElementById("sel-analyze-range").value;
  btn.disabled = true;
  btn.textContent = "...";
  setStatus("");

  // range に応じて limit / since_iso を決定
  let limit = 500;
  let since_iso = "";

  if (range === "since_last") {
    // 前回解析の日時以降
    const stored = await chrome.storage.local.get(["analysisHistory"]);
    const last = (stored.analysisHistory || [])[0];
    if (last?.at) {
      since_iso = last.at;
      limit = 9999;
    } else {
      // 履歴なし → 今日分にフォールバック
      since_iso = new Date().toISOString().slice(0, 10) + "T00:00:00.000Z";
      limit = 9999;
    }
  } else if (range === "today") {
    since_iso = new Date().toISOString().slice(0, 10) + "T00:00:00.000Z";
    limit = 9999;
  } else {
    limit = parseInt(range);
  }

  try {
    const resp = await sendBg({ type: "MANUAL_ANALYZE", limit, since_iso });
    if (resp?.ok) {
      setStatus("解析を開始しました", "success");
    } else {
      setStatus(resp?.error || "エラーが発生しました", "error");
      btn.disabled = false;
      btn.textContent = "⚡ 解析";
    }
  } catch (e) {
    setStatus("エラー: " + e.message, "error");
    btn.disabled = false;
    btn.textContent = "⚡ 解析";
  }
});

// ─── フッターボタン ──────────────────────────────────────────────────────────

document.getElementById("btn-sessions").addEventListener("click", () => {
  chrome.tabs.create({ url: SERVER_URL + "/sessions" });
});

// ─── ログイン / ログアウト ───────────────────────────────────────────────────

document.getElementById("btn-login").addEventListener("click", async () => {
  const status = document.getElementById("login-status");
  status.textContent = "ログイン中...";
  status.className = "status";
  try {
    const resp = await sendBg({ type: "SIGN_IN" });
    if (resp?.uid) {
      await initMain(resp.email);
    } else {
      status.textContent = resp?.error || "ログイン失敗";
      status.className = "status error";
    }
  } catch (e) {
    status.textContent = "エラー: " + e.message;
    status.className = "status error";
  }
});

document.getElementById("btn-logout").addEventListener("click", async () => {
  await sendBg({ type: "SIGN_OUT" });
  document.getElementById("view-login").style.display = "block";
  document.getElementById("view-main").style.display  = "none";
});

// ─── 初期化 ─────────────────────────────────────────────────────────────────

(async () => {
  try {
    const resp = await sendBg({ type: "GET_USER" });
    if (resp?.uid) {
      await initMain(resp.email);
    } else {
      document.getElementById("view-login").style.display = "block";
    }
  } catch (e) {
    document.getElementById("view-login").style.display = "block";
  }
})();
