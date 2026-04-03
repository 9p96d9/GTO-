/**
 * popup.js - PokerGTO Chrome拡張機能 ポップアップ
 * - Firebase Auth (Google) でログイン/ログアウト
 * - ログイン後: content.js にスクレイプ指示を送り、結果をサーバーへ送信
 */

"use strict";

const SERVER_URL = "https://gto-production.up.railway.app";

// ─── Firebase 初期化 ─────────────────────────────────────────────────────────
// Firebase JS SDKをESM importする場合 popup.htmlのscriptをtype="module"にする必要があるが、
// Chrome拡張のpopup.jsはmoduleをimportできないため、
// firebaseConfigはbackground.jsから取得して初期化する。
// popup.js はbackground.jsにメッセージを送ってAuth操作を委譲する。

// ─── UI要素 ─────────────────────────────────────────────────────────────────

const viewLogin   = document.getElementById("view-login");
const viewMain    = document.getElementById("view-main");
const loginStatus = document.getElementById("login-status");
const scrapeStatus = document.getElementById("scrape-status");
const userEmail   = document.getElementById("user-email");
const progressWrap = document.getElementById("progress-wrap");
const progressBar  = document.getElementById("progress-bar");

function showLogin() {
  viewLogin.style.display = "block";
  viewMain.style.display  = "none";
}

function showMain(email) {
  viewLogin.style.display = "none";
  viewMain.style.display  = "block";
  userEmail.textContent = email || "";
}

function setStatus(el, msg, type = "") {
  el.textContent = msg;
  el.className = "status" + (type ? " " + type : "");
}

function setProgress(pct) {
  progressWrap.style.display = "block";
  progressBar.style.width = pct + "%";
  if (pct >= 100) {
    setTimeout(() => { progressWrap.style.display = "none"; progressBar.style.width = "0%"; }, 1000);
  }
}

// ─── background.js への通信 ──────────────────────────────────────────────────

function sendBg(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(msg, resp => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(resp);
      }
    });
  });
}

// ─── 初期化: ログイン状態を確認 ─────────────────────────────────────────────

async function init() {
  try {
    const resp = await sendBg({ type: "GET_USER" });
    if (resp && resp.uid) {
      showMain(resp.email);
    } else {
      showLogin();
    }
  } catch (e) {
    showLogin();
    setStatus(loginStatus, "初期化エラー: " + e.message, "error");
  }
}

// ─── ログインボタン ──────────────────────────────────────────────────────────

document.getElementById("btn-login").addEventListener("click", async () => {
  setStatus(loginStatus, "ログイン中...");
  try {
    const resp = await sendBg({ type: "SIGN_IN" });
    if (resp && resp.uid) {
      showMain(resp.email);
    } else {
      setStatus(loginStatus, resp?.error || "ログイン失敗", "error");
    }
  } catch (e) {
    setStatus(loginStatus, "エラー: " + e.message, "error");
  }
});

// ─── ログアウトボタン ─────────────────────────────────────────────────────────

document.getElementById("btn-logout").addEventListener("click", async () => {
  await sendBg({ type: "SIGN_OUT" });
  showLogin();
});

// ─── PokerGTOサイトを開くボタン ───────────────────────────────────────────────

document.getElementById("btn-open-site").addEventListener("click", () => {
  chrome.tabs.create({ url: SERVER_URL + "/sessions" });
});

// ─── スクレイプ & 送信ボタン ─────────────────────────────────────────────────

document.getElementById("btn-scrape").addEventListener("click", async () => {
  const btn = document.getElementById("btn-scrape");
  btn.disabled = true;
  setStatus(scrapeStatus, "");
  setProgress(5);

  // idTokenを取得
  let idToken;
  try {
    const resp = await sendBg({ type: "GET_ID_TOKEN" });
    if (!resp || !resp.token) throw new Error(resp?.error || "未ログイン");
    idToken = resp.token;
  } catch (e) {
    btn.disabled = false;
    setStatus(scrapeStatus, "認証エラー: " + e.message, "error");
    return;
  }

  setProgress(15);

  // アクティブタブのcontent.jsにスクレイプを依頼
  let tabs;
  try {
    tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs.length) throw new Error("アクティブなタブが見つかりません");
  } catch (e) {
    btn.disabled = false;
    setStatus(scrapeStatus, e.message, "error");
    return;
  }

  const tabId = tabs[0].id;
  let scrapeResult;
  try {
    scrapeResult = await chrome.tabs.sendMessage(tabId, { type: "SCRAPE" });
  } catch (e) {
    btn.disabled = false;
    setStatus(scrapeStatus, "スクレイプ失敗。T4のブックマーク一覧ページを開いてください。", "error");
    return;
  }

  if (!scrapeResult || scrapeResult.error) {
    btn.disabled = false;
    setStatus(scrapeStatus, scrapeResult?.error || "スクレイプ失敗", "error");
    return;
  }

  setProgress(60);
  setStatus(scrapeStatus, `${scrapeResult.hand_count}件収集 → サーバーに送信中...`);

  // サーバーに送信
  try {
    const res = await fetch(SERVER_URL + "/api/upload-from-extension", {
      method: "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": "Bearer " + idToken,
      },
      body: JSON.stringify({
        raw_text:   scrapeResult.raw_text,
        filename:   scrapeResult.filename,
        hand_count: scrapeResult.hand_count,
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);

    setProgress(100);
    setStatus(scrapeStatus, `✓ 送信完了（${scrapeResult.hand_count}件）`, "success");

    // PokerGTOのセッション一覧を開く
    setTimeout(() => {
      chrome.tabs.create({ url: SERVER_URL + "/sessions" });
    }, 1200);

  } catch (e) {
    setStatus(scrapeStatus, "送信エラー: " + e.message, "error");
  }

  btn.disabled = false;
});

// ─── 起動 ───────────────────────────────────────────────────────────────────

init();
