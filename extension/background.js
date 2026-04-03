/**
 * background.js - PokerGTO Chrome拡張機能 Service Worker
 *
 * Firebase Auth の状態管理を担当する。
 * popup.js / content.js からのメッセージを受け取り、Auth操作を実行して結果を返す。
 *
 * Firebase JS SDK は background service worker でも動作するが、
 * IndexedDB ベースの persistence を使うため、
 * initializeApp 時に indexedDB persistence を明示的に設定する。
 */

import { initializeApp }          from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithCredential,
  signOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const SERVER_URL = "https://gto-production.up.railway.app";

// ─── Firebase初期化 ─────────────────────────────────────────────────────────

let _app  = null;
let _auth = null;
let _user = null;  // 現在ログイン中のユーザー

async function getFirebaseConfig() {
  const res = await fetch(SERVER_URL + "/api/firebase-config");
  return res.json();
}

async function initFirebase() {
  if (_app) return;
  const cfg = await getFirebaseConfig();
  _app  = initializeApp(cfg);
  _auth = getAuth(_app);
  onAuthStateChanged(_auth, user => {
    _user = user;
  });
}

// ─── メッセージハンドラ ──────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessage(msg).then(sendResponse).catch(e => sendResponse({ error: e.message }));
  return true;  // 非同期レスポンスを有効化
});

async function handleMessage(msg) {
  await initFirebase();

  switch (msg.type) {

    // 現在のログイン状態を返す
    case "GET_USER":
      if (_user) {
        return { uid: _user.uid, email: _user.email, displayName: _user.displayName };
      }
      return { uid: null };

    // Google ログイン（chrome.identity を使って Popup なしでトークン取得）
    case "SIGN_IN": {
      try {
        const token = await getChromeIdentityToken();
        const credential = GoogleAuthProvider.credential(null, token);
        const result = await signInWithCredential(_auth, credential);
        _user = result.user;
        return { uid: _user.uid, email: _user.email, displayName: _user.displayName };
      } catch (e) {
        return { error: e.message };
      }
    }

    // ログアウト
    case "SIGN_OUT":
      if (_auth) await signOut(_auth);
      _user = null;
      return { ok: true };

    // 最新の idToken を返す（サーバー送信時に使用）
    case "GET_ID_TOKEN": {
      if (!_user) return { error: "未ログイン" };
      try {
        const token = await _user.getIdToken(/* forceRefresh */ false);
        return { token };
      } catch (e) {
        return { error: e.message };
      }
    }

    default:
      return { error: "Unknown message type: " + msg.type };
  }
}

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
