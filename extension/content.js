/**
 * content.js - PokerGTO Chrome拡張機能 コンテンツスクリプト
 *
 * T4のブックマーク一覧ページで動作する。
 * popup.js から { type: "SCRAPE" } メッセージを受け取ると
 * ハンドカードをクリック → オーバーレイのテキストを収集 → { raw_text, hand_count, filename } を返す。
 *
 * 元の bookmarklet.js のロジックをそのまま移植。
 */

"use strict";

const sleep = ms => new Promise(r => setTimeout(r, ms));

/**
 * ブックマーク一覧からハンドを収集する
 * @returns {{ raw_text: string, hand_count: number, filename: string }}
 */
async function scrapeHands() {
  // ハンドカード要素を収集（bookmarklet.jsと同じフィルタ）
  const cards = [...document.querySelectorAll("*")].filter(e =>
    /2026|2025|2024/.test(e.textContent) &&
    /bb/.test(e.textContent) &&
    e.children.length > 0 &&
    e.children.length < 8 &&
    e.offsetHeight > 30 &&
    e.offsetHeight < 150
  );

  if (!cards.length) {
    throw new Error("ハンドが見つかりません。T4のブックマーク一覧ページを開いてください。");
  }

  const results = [];

  for (let i = 0; i < cards.length; i++) {
    cards[i].scrollIntoView({ block: "center" });
    await sleep(200);
    cards[i].click();
    await sleep(700);

    // オーバーレイを探す
    const overlay = [...document.querySelectorAll("*")].find(e =>
      window.getComputedStyle(e).position === "fixed" &&
      e.innerText &&
      e.innerText.includes("ハンドヒストリー詳細")
    );

    if (overlay) {
      results.push(
        "=".repeat(60) + "\n" +
        `ハンド ${i + 1} / ${cards.length}\n` +
        "=".repeat(60) + "\n" +
        overlay.innerText.trim() + "\n"
      );
      // ブックマーク解除（一覧から消えるのでセッションをクリアするため）
      const btn = overlay.querySelector('button[title="ブックマーク解除"]');
      if (btn) { btn.click(); await sleep(300); }
    } else {
      results.push(`=== ハンド ${i + 1} (取得失敗) ===\n${cards[i].textContent.trim()}\n`);
    }

    // オーバーレイを閉じる
    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Escape", keyCode: 27, bubbles: true,
    }));
    await sleep(400);
  }

  if (!results.length) {
    throw new Error("収集できたハンドが0件でした。");
  }

  // ファイル名を今日の日付で作成
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const filename = `t4_hands_${today}.txt`;

  return {
    raw_text:   results.join("\n"),
    hand_count: results.length,
    filename,
  };
}

// ─── リアルタイムWebSocket傍受（Phase 7） ──────────────────────────────────────

(function injectWebSocketInterceptor() {
  const script = document.createElement('script');
  script.textContent = `
    (function() {
      const _origWS = window.WebSocket;
      window.WebSocket = function(url, protocols) {
        const ws = protocols ? new _origWS(url, protocols) : new _origWS(url);
        ws.addEventListener('message', function(event) {
          if (typeof event.data !== 'string' || !event.data.startsWith('42')) return;
          try {
            const parsed = JSON.parse(event.data.slice(2));
            if (parsed[0] === 'fastFoldTableState' && parsed[1] && parsed[1].isHandInProgress === false) {
              window.dispatchEvent(new CustomEvent('t4_hand_complete', { detail: parsed[1] }));
            }
          } catch(e) {}
        });
        return ws;
      };
      Object.assign(window.WebSocket, _origWS);
    })();
  `;
  document.documentElement.appendChild(script);
  script.remove();

  window.addEventListener('t4_hand_complete', function(e) {
    chrome.runtime.sendMessage({ type: 'HAND_COMPLETE', hand: e.detail });
  });
})();

// ─── メッセージリスナー ──────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type !== "SCRAPE") return;

  scrapeHands()
    .then(result => sendResponse(result))
    .catch(e   => sendResponse({ error: e.message }));

  return true;  // 非同期レスポンスを有効化
});
