/**
 * background.js - PokerGTO Chrome拡張機能 Service Worker (Phase 9)
 */

import { initializeApp }          from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithCredential,
  signOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const SERVER_URL = "http://gto-alb-1734423629.ap-northeast-1.elb.amazonaws.com";

// ─── Firebase初期化 ─────────────────────────────────────────────────────────

let _app  = null;
let _auth = null;
let _user = null;
let _initPromise = null;

async function getFirebaseConfig() {
  const res = await fetch(SERVER_URL + "/api/firebase-config");
  return res.json();
}

async function initFirebase() {
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    const cfg = await getFirebaseConfig();
    _app  = initializeApp(cfg);
    _auth = getAuth(_app);
    await new Promise(resolve => {
      let resolved = false;
      onAuthStateChanged(_auth, user => {
        _user = user;
        if (!resolved) {
          resolved = true;
          resolve();
        }
        if (user) {
          chrome.storage.local.get(["sessionStartAt"], s => {
            if (!s.sessionStartAt) chrome.storage.local.set({ sessionStartAt: Date.now() });
          });
          _schedulePlaytimeNotify();
        } else {
          chrome.storage.local.remove(["sessionStartAt"]);
          _clearPlaytimeAlarm();
        }
      });
    });
  })();
  return _initPromise;
}

// ─── メッセージハンドラ ──────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessage(msg).then(sendResponse).catch(e => sendResponse({ error: e.message }));
  return true;
});

async function handleMessage(msg) {
  console.log("[GTO]", msg.type);
  await initFirebase();

  switch (msg.type) {

    case "GET_USER":
      if (_user) return { uid: _user.uid, email: _user.email, displayName: _user.displayName };
      return { uid: null };

    case "SIGN_IN": {
      try {
        const token = await getChromeIdentityToken();
        const credential = GoogleAuthProvider.credential(null, token);
        const result = await signInWithCredential(_auth, credential);
        _user = result.user;
        // ログイン時にプレイ開始時刻をリセット
        await chrome.storage.local.set({ sessionStartAt: Date.now(), handCounter: 0 });
        _schedulePlaytimeNotify();
        return { uid: _user.uid, email: _user.email, displayName: _user.displayName };
      } catch (e) {
        return { error: e.message };
      }
    }

    case "SIGN_OUT":
      if (_auth) await signOut(_auth);
      _user = null;
      _clearPlaytimeAlarm();
      await chrome.storage.local.remove(["sessionStartAt", "handCounter"]);
      return { ok: true };

    case "GET_ID_TOKEN": {
      if (!_user) return { error: "未ログイン" };
      try {
        const token = await _user.getIdToken(false);
        return { token };
      } catch (e) {
        return { error: e.message };
      }
    }

    case "GET_STORAGE": {
      const data = await chrome.storage.local.get(msg.keys || null);
      return data;
    }

    // 設定変更時にプレイ時間アラームを再設定
    case "SETTINGS_UPDATED": {
      _schedulePlaytimeNotify();
      return { ok: true };
    }

    // リアルタイムハンド取得（Phase 7）
    case "HAND_COMPLETE": {
      if (!_user) {
        console.log("[GTO] HAND_COMPLETE: _user null");
        return { ok: false, reason: "未ログイン" };
      }
      try {
        const token = await _user.getIdToken(false);
        const captured_at = new Date().toISOString();
        const res = await fetch(SERVER_URL + "/api/hands/realtime", {
          method: "POST",
          headers: {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ hand_json: msg.hand, captured_at })
        });
        const result = await res.json();
        console.log("[GTO] HAND_COMPLETE:", res.status, JSON.stringify(result).slice(0, 120));
        if (result.ok) {
          await _checkAutoAnalyze(token);
        }
        return result;
      } catch(e) {
        console.log("[GTO] HAND_COMPLETE error:", e.message);
        return { error: e.message };
      }
    }

    // ポップアップからの手動解析トリガー
    case "MANUAL_ANALYZE": {
      if (!_user) return { error: "未ログイン" };
      try {
        const token = await _user.getIdToken(false);
        const body = { limit: msg.limit ?? 500 };
        if (msg.since_iso) body.since_iso = msg.since_iso;
        const res = await fetch(SERVER_URL + "/api/hands/analyze", {
          method: "POST",
          headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.progress_url) {
          chrome.tabs.create({ url: SERVER_URL + data.progress_url });
          return { ok: true };
        }
        return { error: data.error || "解析開始失敗" };
      } catch(e) {
        return { error: e.message };
      }
    }

    default:
      return { error: "Unknown message type: " + msg.type };
  }
}

// ─── 自動解析（バックグラウンド） ───────────────────────────────────────────

async function _checkAutoAnalyze(token) {
  try {
    const s = await chrome.storage.local.get(["handCounter", "autoMode", "autoThreshold"]);
    const mode      = s.autoMode      ?? "background";
    const threshold = s.autoThreshold ?? 100;

    if (mode === "off") return;

    let counter = (s.handCounter || 0) + 1;
    await chrome.storage.local.set({ handCounter: counter });
    if (counter < threshold) return;

    // 閾値到達 → カウンターリセット
    await chrome.storage.local.set({ handCounter: 0, lastAutoAt: new Date().toISOString() });

    const res = await fetch(SERVER_URL + "/api/hands/analyze", {
      method: "POST",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify({ limit: threshold }),
    });
    const data = await res.json();

    if (data.job_id) {
      // 解析結果URLを履歴に追加（最新3件を保持）
      const resultUrl = SERVER_URL + "/classify_result/" + data.job_id;
      const stored = await chrome.storage.local.get(["analysisHistory"]);
      const history = stored.analysisHistory || [];
      history.unshift({ url: resultUrl, job_id: data.job_id, at: new Date().toISOString(), hands: threshold });
      await chrome.storage.local.set({ analysisHistory: history.slice(0, 3) });

      // バッジ通知（緑の✓）
      chrome.action.setBadgeText({ text: "✓" });
      chrome.action.setBadgeBackgroundColor({ color: "#2e7d32" });

      // ブラウザ通知
      chrome.notifications.create("analyze_done_" + data.job_id, {
        type:    "basic",
        iconUrl: "icons/icon48.png",
        title:   "PokerGTO — 解析完了",
        message: `${threshold}手の解析が完了しました。クリックして結果を見る`,
        buttons: [{ title: "結果を見る" }],
      });

      // 通知クリック → 小窓で結果ページを開く
      chrome.notifications.onButtonClicked.addListener(function onBtn(notifId, btnIdx) {
        if (notifId.startsWith("analyze_done_")) {
          const jid = notifId.replace("analyze_done_", "");
          chrome.windows.create({
            url:    SERVER_URL + "/classify_result/" + jid,
            type:   "popup",
            width:  1200,
            height: 800,
            focused: false,
          });
          chrome.notifications.onButtonClicked.removeListener(onBtn);
        }
      });
    }
  } catch(e) {
    console.warn("[PokerGTO] 自動解析エラー:", e.message);
  }
}

// ─── プレイ時間通知 ──────────────────────────────────────────────────────────

const PLAYTIME_ALARM = "gto_playtime";

async function _schedulePlaytimeNotify() {
  _clearPlaytimeAlarm();
  const s = await chrome.storage.local.get(["playtimeNotify"]);
  const minutes = parseInt(s.playtimeNotify ?? 0);
  if (!minutes) return;
  chrome.alarms.create(PLAYTIME_ALARM, { delayInMinutes: minutes });
}

function _clearPlaytimeAlarm() {
  chrome.alarms.clear(PLAYTIME_ALARM);
}

chrome.alarms.onAlarm.addListener(async alarm => {
  if (alarm.name !== PLAYTIME_ALARM) return;
  const s = await chrome.storage.local.get(["sessionStartAt", "playtimeNotify"]);
  const minutes = parseInt(s.playtimeNotify ?? 0);
  if (!minutes || !s.sessionStartAt) return;

  const elapsed = Math.round((Date.now() - s.sessionStartAt) / 60000);
  chrome.notifications.create("playtime_" + Date.now(), {
    type:    "basic",
    iconUrl: "icons/icon48.png",
    title:   "PokerGTO — プレイ時間通知",
    message: `${elapsed}分プレイ中です。休憩を取りましょう。`,
  });

  // 繰り返し通知（同じ間隔で再スケジュール）
  chrome.alarms.create(PLAYTIME_ALARM, { delayInMinutes: minutes });
});

// ─── 通知クリックで小窓表示 ─────────────────────────────────────────────────

chrome.notifications.onClicked.addListener(notifId => {
  if (notifId.startsWith("analyze_done_")) {
    const jid = notifId.replace("analyze_done_", "");
    chrome.windows.create({
      url:    SERVER_URL + "/classify_result/" + jid,
      type:   "popup",
      width:  1200,
      height: 800,
      focused: false,
    });
    chrome.notifications.clear(notifId);
  }
});

// ─── chrome.identity でGoogleトークン取得 ────────────────────────────────────

function getChromeIdentityToken() {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive: true }, token => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else if (!token) {
        reject(new Error("トークン取得失敗"));
      } else {
        resolve(token);
      }
    });
  });
}

// ─── 起動時に初期化 ─────────────────────────────────────────────────────────
initFirebase();
