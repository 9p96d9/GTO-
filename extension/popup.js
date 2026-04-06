/**
 * popup.js - PokerGTO Chrome拡張機能 ポップアップ
 */
"use strict";

const SERVER_URL = "https://gto-production.up.railway.app";

const viewLogin    = document.getElementById("view-login");
const viewMain     = document.getElementById("view-main");
const loginStatus  = document.getElementById("login-status");
const mainStatus   = document.getElementById("main-status");

function showLogin() {
  viewLogin.style.display = "block";
  viewMain.style.display  = "none";
}

function showMain(email) {
  viewLogin.style.display = "none";
  viewMain.style.display  = "block";
  document.getElementById("user-email").textContent = email || "";
}

function setStatus(el, msg, type = "") {
  el.textContent = msg;
  el.className = "status" + (type ? " " + type : "");
}

function sendBg(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(msg, resp => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(resp);
    });
  });
}

// ─── 蓄積ハンド数を取得して表示 ────────────────────────────────────────────

async function loadHandCount() {
  const row = document.getElementById("hand-count-row");
  try {
    const resp = await sendBg({ type: "GET_ID_TOKEN" });
    if (!resp || !resp.token) { row.innerHTML = '<div style="font-size:12px;color:#556">ログイン中...</div>'; return; }
    const res = await fetch(SERVER_URL + "/api/hands/stats", {
      headers: { "Authorization": "Bearer " + resp.token }
    });
    if (!res.ok) throw new Error("取得失敗");
    const data = await res.json();
    const count = data.count || 0;
    row.innerHTML = `
      <div class="hand-count-num">${count.toLocaleString()}<span class="hand-count-unit">手</span></div>
      <div class="hand-count-label">蓄積済みハンド</div>
    `;
    // 自動解析バッジ（100手未確認の場合）
    const badge = document.getElementById("auto-badge");
    const stored = await chrome.storage.local.get(["autoAnalyzePending"]);
    if (stored.autoAnalyzePending) {
      badge.classList.add("visible");
      document.getElementById("auto-badge-text").textContent = "100手達成 → 自動解析を開始しました";
      setTimeout(() => chrome.storage.local.remove("autoAnalyzePending"), 3000);
    }
  } catch (e) {
    row.innerHTML = '<div style="font-size:12px;color:#556">—</div>';
  }
}

// ─── 初期化 ─────────────────────────────────────────────────────────────────

async function init() {
  try {
    const resp = await sendBg({ type: "GET_USER" });
    if (resp && resp.uid) {
      showMain(resp.email);
      loadHandCount();
    } else {
      showLogin();
    }
  } catch (e) {
    showLogin();
    setStatus(loginStatus, "初期化エラー: " + e.message, "error");
  }
}

// ─── ログイン ────────────────────────────────────────────────────────────────

document.getElementById("btn-login").addEventListener("click", async () => {
  setStatus(loginStatus, "ログイン中...");
  try {
    const resp = await sendBg({ type: "SIGN_IN" });
    if (resp && resp.uid) {
      showMain(resp.email);
      loadHandCount();
    } else {
      setStatus(loginStatus, resp?.error || "ログイン失敗", "error");
    }
  } catch (e) {
    setStatus(loginStatus, "エラー: " + e.message, "error");
  }
});

// ─── ログアウト ──────────────────────────────────────────────────────────────

document.getElementById("btn-logout").addEventListener("click", async () => {
  await sendBg({ type: "SIGN_OUT" });
  showLogin();
});

// ─── PokerGTOを開く ──────────────────────────────────────────────────────────

document.getElementById("btn-open-site").addEventListener("click", () => {
  chrome.tabs.create({ url: SERVER_URL + "/sessions" });
});

init();
