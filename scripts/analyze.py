"""
analyze.py - ハンドのGTO評価をGemini APIで生成し、JSONに追記する
使用法: python scripts/analyze.py data/file.json
"""

import json
import re
import sys
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from google import genai

load_dotenv()

MODEL      = "gemini-2.5-flash"
BATCH_SIZE = 10          # 1リクエストあたりのハンド数（50ハンド→5リクエスト）
RETRY_WAIT = 5.0         # 429エラー時の待機秒数
MAX_RETRY  = 3


# ─── ハンドサマリ（SSE進捗用）────────────────────────────────────────────────

def get_hand_summary(hand: dict) -> str:
    pos      = hand.get("hero_position", "?")
    is_3bet  = hand.get("is_three_bet_pot", False)
    pot_type = "3betポット" if is_3bet else "シングルレイズ"
    flop     = hand.get("streets", {}).get("flop")
    if flop and flop.get("board"):
        street_str = "Flop: " + " ".join(flop["board"])
    else:
        street_str = "プリフロップ"
    opponents = [p for p in hand.get("players", []) if not p.get("is_hero")]
    opp_pos   = opponents[0].get("position", "?") if opponents else "?"
    return f"{pos} vs {opp_pos}, {pot_type}, {street_str}"


# ─── プロンプト生成 ───────────────────────────────────────────────────────────

def format_action_summary(hand: dict) -> str:
    lines = []
    streets = hand.get("streets") or {}
    preflop = streets.get("preflop") or []
    if preflop:
        lines.append("【プリフロップ】")
        for a in preflop:
            amt = f" {a.get('amount_bb')}bb" if a.get("amount_bb") is not None else ""
            pos = a.get("position") or a.get("name", "?")
            lines.append(f"  {pos}: {a.get('action', '?')}{amt}")

    for street in ("flop", "turn", "river"):
        s = streets.get(street)
        if s and isinstance(s, dict):
            board = " ".join(s.get("board") or [])
            lines.append(f"【{street.capitalize()}】{board} (ポット: {s.get('pot_bb', 0)}bb)")
            for a in (s.get("actions") or []):
                amt = f" {a.get('amount_bb')}bb" if a.get("amount_bb") is not None else ""
                pos = a.get("position") or a.get("name", "?")
                lines.append(f"  {pos}: {a.get('action', '?')}{amt}")

    return "\n".join(lines)


def get_board_summary(hand: dict) -> str:
    boards = []
    for street in ("flop", "turn", "river"):
        s = hand["streets"].get(street)
        if s and s.get("board"):
            boards.extend(s["board"])
    return " ".join(boards) if boards else "(プリフロップのみ)"


def build_hand_block(idx: int, hand: dict) -> str:
    pos    = hand.get("hero_position", "不明")
    cards  = " ".join(hand.get("hero_cards", []))
    board  = get_board_summary(hand)
    action = format_action_summary(hand)

    allin_ev = hand.get("result", {}).get("allin_ev", {})
    ev_info  = ""
    if allin_ev:
        hero_name = next((p["name"] for p in hand.get("players", []) if p.get("is_hero")), None)
        if hero_name and hero_name in allin_ev:
            ev_info = f"\nAll-in EV: {allin_ev[hero_name]:+.2f}bb"

    return f"""=== ハンド {idx} ===
ヒーロー: {pos} / 手札: {cards}
ボード: {board}
アクション履歴:
{action}{ev_info}"""


def build_batch_prompt(indexed_hands: list) -> str:
    blocks     = [build_hand_block(idx, hand) for idx, hand in indexed_hands]
    hands_text = "\n\n".join(blocks)
    n          = len(indexed_hands)

    return f"""あなたはポーカーのGTOコーチです。以下の{n}ハンドを一括評価してください。

{hands_text}

以下のJSON配列形式のみで回答してください（説明文・コードブロック記号なし）:
[
  {{
    "id": <ハンド番号>,
    "gto_rating": "✅良好 or ⚠️改善 or ❌エラー or 🎲クーラー",
    "ichi": "20字以内の結論",
    "detail": "60字以内の詳細",
    "kaizen": "❌⚠️の場合のみ正しいアクション40字以内、それ以外は空文字",
    "ev_loss": "❌の場合のみ例: -15bb、それ以外は空文字"
  }}
]"""


def reconstruct_evaluation(j: dict) -> str:
    lines = [f"GTO評価: {j['gto_rating']}"]
    if j.get("ichi"):   lines.append(f"一言: {j['ichi']}")
    if j.get("detail"): lines.append(f"詳細: {j['detail']}")
    if j.get("kaizen"): lines.append(f"改善: {j['kaizen']}")
    if j.get("ev_loss"):lines.append(f"EV損失推定: {j['ev_loss']}")
    return "\n".join(lines)


# ─── API呼び出し ──────────────────────────────────────────────────────────────

def evaluate_batch(client: genai.Client, indexed_hands: list) -> dict:
    """{hand_idx: evaluation_text} を返す"""
    prompt  = build_batch_prompt(indexed_hands)
    retries = 0
    while True:
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            results = json.loads(raw)
            return {item["id"]: reconstruct_evaluation(item) for item in results}
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and retries < MAX_RETRY:
                retries += 1
                ids = [idx for idx, _ in indexed_hands]
                print(f"  [429] バッチ{ids}: レート制限 — {RETRY_WAIT}秒後リトライ ({retries}/{MAX_RETRY})", file=sys.stderr)
                time.sleep(RETRY_WAIT)
                continue
            # 429以外はそのまま raise して呼び出し元にエラーを伝える
            raise


# ─── フラグ設定 ───────────────────────────────────────────────────────────────

def apply_rating_flags(hand: dict, evaluation: str):
    rating = ""
    for line in evaluation.split("\n"):
        stripped = line.strip()
        if stripped.startswith("GTO評価:"):
            rating = stripped[len("GTO評価:"):].strip()
            break
    if not rating:
        rating = evaluation

    if rating.startswith("❌"):
        hand["has_gto_error"] = True
        hand["is_good_play"]  = False
    elif rating.startswith("✅") or rating.startswith("🎲"):
        hand["has_gto_error"] = False
        hand["is_good_play"]  = True
    else:
        hand["has_gto_error"] = False
        hand["is_good_play"]  = False


# ─── JSON保存 ─────────────────────────────────────────────────────────────────

def save_json(json_path: str, data: dict):
    data["analyzed_at"] = datetime.now().isoformat()
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── メイン処理 ───────────────────────────────────────────────────────────────

def analyze_file(json_path: str, progress_cb=None, api_key: str = None):
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY が .env に設定されていません", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hands   = data.get("hands", [])
    total   = len(hands)
    pending = [(i + 1, hand) for i, hand in enumerate(hands) if not hand.get("analyzed", False)]
    cached  = total - len(pending)

    if cached > 0:
        print(f"  [SKIP] {cached}ハンドは評価済みのためスキップ")
    if not pending:
        print(f"  [SKIP] 全{total}ハンドが評価済みです")
        return total, 0

    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    print(f"  [ANALYZE] {len(pending)}ハンドを{len(batches)}バッチで評価します（{MODEL}）")

    errors    = 0
    completed = cached

    for batch_idx, batch in enumerate(batches):
        result_map = evaluate_batch(client, batch)

        for hand_idx, hand in batch:
            evaluation = result_map.get(hand_idx, "評価エラー")
            hand["gto_evaluation"] = evaluation
            hand["analyzed"]       = True
            apply_rating_flags(hand, evaluation)
            if evaluation == "評価エラー":
                errors += 1

        completed += len(batch)
        pct = int(completed / total * 100) if total > 0 else 100
        print(f"  分析中... {completed}/{total}ハンド完了 ({pct}%)")

        # バッチごとに即時保存（Ctrl+C対策）
        save_json(json_path, data)

        if progress_cb:
            progress_cb({
                "type":              "batch_progress",
                "batch_current":     batch_idx + 1,
                "batch_total":       len(batches),
                "hands_done":        completed,
                "hands_total":       total,
                "current_hand_info": get_hand_summary(batch[-1][1]),
            })

    if errors > 0:
        print(f"  [WARN] {errors}ハンドが評価エラーでした")

    return total, errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze.py <data.json>")
        sys.exit(1)

    json_path = sys.argv[1]
    total, errors = analyze_file(json_path)
    print(f"  Done: {total} hands analyzed, {errors} errors")


if __name__ == "__main__":
    main()
