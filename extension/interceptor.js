/**
 * interceptor.js - WebSocket傍受（Phase 7）
 *
 * world: "MAIN" で実行されるため window.WebSocket に直接アクセス可能。
 * CSPの制約を受けずにSocket.IOイベントを傍受できる。
 * ハンド終了を検知したら CustomEvent で content.js（ISOLATED world）に通知する。
 */

(function () {
  const _origWS = window.WebSocket;
  const _lastTableState = {};   // tableId → 直前のfastFoldTableState
  const _lastActionHistory = {}; // tableId → 保存済みactionHistory文字列（重複防止）

  function dispatchHand(state) {
    const tableId = state.tableId;
    const key = JSON.stringify(state.actionHistory);
    if (_lastActionHistory[tableId] === key) return; // 重複スキップ
    if (!state.actionHistory || state.actionHistory.length === 0) return; // 空スキップ
    _lastActionHistory[tableId] = key;
    window.dispatchEvent(new CustomEvent('t4_hand_complete', { detail: state }));
  }

  window.WebSocket = function (url, protocols) {
    const ws = protocols ? new _origWS(url, protocols) : new _origWS(url);

    ws.addEventListener('message', function (event) {
      if (typeof event.data !== 'string' || !event.data.startsWith('42')) return;
      try {
        const parsed = JSON.parse(event.data.slice(2));
        const eventName = parsed[0];
        const data = parsed[1];

        if (eventName === 'fastFoldTableState' && data && data.tableId) {
          _lastTableState[data.tableId] = data;
          if (data.isHandInProgress === false) {
            dispatchHand(data);
          }
        }

        // Fast Foldでフォールド離脱時：最後に記録したテーブル状態を保存
        if (eventName === 'fastFoldTableRemoved') {
          const tableId = data?.tableId || (typeof data === 'string' ? data : null);
          if (tableId && _lastTableState[tableId]) {
            dispatchHand(_lastTableState[tableId]);
            delete _lastTableState[tableId];
          }
        }
      } catch (e) {}
    });

    return ws;
  };

  Object.assign(window.WebSocket, _origWS);
})();
