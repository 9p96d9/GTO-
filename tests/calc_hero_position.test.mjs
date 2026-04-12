/**
 * calcHeroPosition のユニットテスト
 * 実行: node --test tests/calc_hero_position.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";

// interceptor.js は IIFE + ブラウザ前提なので関数だけ抜き出して再定義
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

// ─── ヘルパー ────────────────────────────────────────────────────────────────

/** seats配列を生成（seatIndex 0〜5 に playerName を埋める） */
function makeSeats(indices) {
  const seats = Array(6).fill(null);
  for (const i of indices) seats[i] = { playerName: `P${i}` };
  return seats;
}

// ─── 6-max テスト ────────────────────────────────────────────────────────────

test("6-max: Hero=BTN", () => {
  // 席: 0,1,2,3,4,5 全員いる。BTN=2、Hero=2
  const data = { buttonPosition: 2, mySeatIndex: 2, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "BTN");
});

test("6-max: Hero=SB (BTNの次)", () => {
  const data = { buttonPosition: 2, mySeatIndex: 3, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "SB");
});

test("6-max: Hero=BB", () => {
  const data = { buttonPosition: 2, mySeatIndex: 4, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "BB");
});

test("6-max: Hero=UTG", () => {
  const data = { buttonPosition: 2, mySeatIndex: 5, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "UTG");
});

test("6-max: Hero=HJ", () => {
  const data = { buttonPosition: 2, mySeatIndex: 0, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "HJ");
});

test("6-max: Hero=CO", () => {
  const data = { buttonPosition: 2, mySeatIndex: 1, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "CO");
});

// ─── BTNが配列の末尾席のケース（折り返し） ───────────────────────────────────

test("6-max: BTN=5、Hero=0 → SB", () => {
  const data = { buttonPosition: 5, mySeatIndex: 0, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "SB");
});

test("6-max: BTN=5、Hero=5 → BTN", () => {
  const data = { buttonPosition: 5, mySeatIndex: 5, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "BTN");
});

// ─── 空席あり（Fast Fold で実際に起きるケース） ──────────────────────────────

test("5人（席0が空）: BTN=1、Hero=3 → BB", () => {
  // active: [1,2,3,4,5]、BTN=index1、Hero=index3 → offset=2 → BB
  const data = { buttonPosition: 1, mySeatIndex: 3, seats: makeSeats([1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "BB");
});

test("4人（席0,1が空）: BTN=2、Hero=5 → UTG", () => {
  // active: [2,3,4,5]、BTN=index2 → offset3=UTG
  const data = { buttonPosition: 2, mySeatIndex: 5, seats: makeSeats([2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "UTG");
});

// ─── エッジケース ────────────────────────────────────────────────────────────

test("buttonPosition が null → 空文字", () => {
  const data = { buttonPosition: null, mySeatIndex: 2, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "");
});

test("mySeatIndex が null → 空文字", () => {
  const data = { buttonPosition: 2, mySeatIndex: null, seats: makeSeats([0,1,2,3,4,5]) };
  assert.equal(calcHeroPosition(data), "");
});

test("seats が空 → 空文字", () => {
  const data = { buttonPosition: 2, mySeatIndex: 2, seats: [] };
  assert.equal(calcHeroPosition(data), "");
});

test("Hero の席に playerName がない（離席済み）→ 空文字", () => {
  // Hero(mySeatIndex=2) の席が null
  const seats = makeSeats([0,1,3,4,5]); // 2が空
  const data = { buttonPosition: 0, mySeatIndex: 2, seats };
  assert.equal(calcHeroPosition(data), "");
});
