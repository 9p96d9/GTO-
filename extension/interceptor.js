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
  const _heroInfo = {};          // tableId → { seatIndex, position, hand } ※フォールド離脱用退避

  function dispatchHand(state) {
    const tableId = state.tableId;
    const key = JSON.stringify(state.actionHistory);
    if (_lastActionHistory[tableId] === key) return; // 重複スキップ
    if (!state.actionHistory || state.actionHistory.length === 0) return; // 空スキップ
    _lastActionHistory[tableId] = key;
    window.dispatchEvent(new CustomEvent('t4_hand_complete', { detail: state }));
  }

  /**
   * buttonPosition + seats 配列から Hero のポジション名を算出する。
   * 6-max: offset 0=BTN, 1=SB, 2=BB, 3=UTG, 4=HJ, 5=CO
   */
  function calcHeroPosition(data) {
    const btn = data.buttonPosition;
    if (btn == null || data.mySeatIndex == null) return "";
    const active = (data.seats || [])
      .map((s, i) => (s && s.playerName) ? i : -1)
      .filter(i => i >= 0);
    const n = active.length;
    const btnPos  = active.indexOf(btn);
    const heroPos = active.indexOf(data.mySeatIndex);
    if (n === 0 || btnPos < 0 || heroPos < 0) return "";
    const offset = (heroPos - btnPos + n) % n;
    const NAMES = {
      2: ["BTN", "BB"],
      3: ["BTN", "SB", "BB"],
      4: ["BTN", "SB", "BB", "UTG"],
      5: ["BTN", "SB", "BB", "UTG", "CO"],
      6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
    };
    return (NAMES[n] || [])[offset] || "";
  }

  /** fastFoldTableState から Hero のホールカード・ポジションを退避する */
  function cacheHeroInfo(data) {
    if (data.mySeatIndex == null) return;

    const prevInfo = _heroInfo[data.tableId] || {};
    const position = calcHeroPosition(data) || prevInfo.position || "";

    // 方法1: handResults から（ハンド完了後）
    const heroResult = (data.handResults || []).find(r => r.seatIndex === data.mySeatIndex);
    if (heroResult && heroResult.hand && heroResult.hand.length > 0) {
      _heroInfo[data.tableId] = {
        seatIndex: data.mySeatIndex,
        position:  heroResult.position || position,
        hand:      heroResult.hand.slice(),
      };
      return;
    }

    // 方法2: seats[mySeatIndex].cards から（フォールド前に存在）
    const heroSeat = (data.seats || [])[data.mySeatIndex];
    const seatCards = heroSeat?.cards || heroSeat?.hand || heroSeat?.holeCards;
    if (Array.isArray(seatCards) && seatCards.length > 0) {
      _heroInfo[data.tableId] = {
        seatIndex: data.mySeatIndex,
        position:  position,
        hand:      seatCards.slice(),
      };
      return;
    }

    // カードなし状態でもポジションだけ更新（後続イベントで使う）
    if (position && !prevInfo.position) {
      _heroInfo[data.tableId] = { ...prevInfo, seatIndex: data.mySeatIndex, position };
    }
  }

  /**
   * fastFoldTableRemoved 発火時、_lastTableState に Hero 情報が欠落していれば
   * _heroInfo から補完して dispatch する。
   */
  function dispatchWithHeroFallback(tableId) {
    const state = _lastTableState[tableId];
    if (!state) return;

    const info = _heroInfo[tableId];
    if (info) {
      const results = state.handResults || [];
      const heroEntry = results.find(r => r.seatIndex === info.seatIndex);
      if (heroEntry) {
        // 欠落フィールドだけ補完（完了ハンドのデータを上書きしない）
        if (!heroEntry.position)                   heroEntry.position = info.position;
        if (!heroEntry.hand || heroEntry.hand.length === 0) heroEntry.hand = info.hand;
      } else {
        // Hero エントリ自体が存在しない場合は追加
        results.push({
          seatIndex:  info.seatIndex,
          position:   info.position,
          hand:       info.hand,
          profit:     0,
          playerName: "",
          isWinner:   false,
        });
        state.handResults = results;
      }
      delete _heroInfo[tableId];
    }

    dispatchHand(state);
    delete _lastTableState[tableId];
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
          // Hero 情報を毎回退避（fastFoldTableRemoved に備えて）
          cacheHeroInfo(data);
          _lastTableState[data.tableId] = data;
          if (data.isHandInProgress === false) {
            dispatchHand(data);
          }
        }

        // Fast Foldでフォールド離脱時：Hero情報を補完してから dispatch
        if (eventName === 'fastFoldTableRemoved') {
          const tableId = data?.tableId || (typeof data === 'string' ? data : null);
          if (tableId) dispatchWithHeroFallback(tableId);
        }
      } catch (e) {}
    });

    return ws;
  };

  Object.assign(window.WebSocket, _origWS);
})();
