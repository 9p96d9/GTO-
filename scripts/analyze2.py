"""
analyze2.py - ハンドのGTO評価をOpenAI互換APIで生成する（Groq / Gemini 両対応）

使用法:
  python scripts/analyze2.py data/file.json

環境変数:
  GROQ_API_KEY   → Groq優先（未設定時はGeminiにフォールバック）
  GEMINI_API_KEY → Geminiフォールバック用

プロバイダー優先順位:
  1. GROQ_API_KEY があれば Groq (llama-3.3-70b-versatile)
  2. なければ GEMINI_API_KEY で Gemini (gemini-2.5-flash) 互換エンドポイント

BYOKフロー（routes/cart.py から呼ばれる場合）:
  - キーが "gsk_" で始まる → Groq
  - それ以外 → Gemini

解析モード:
  MODE = "standard" : 数値あり・従来互換
  MODE = "detail"   : 数値なし・rep（Hero表現レンジ）追加・システムプロンプト使用
  MODE = "explain"  : 長文教育解説・1ハンド単位・詳細解説ボタン用
"""

import json
import re
import sys
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI  # pip install openai

load_dotenv()

# ─── プロバイダー設定 ─────────────────────────────────────────────────────────

PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model":    "llama-3.3-70b-versatile",
        "env_key":  "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model":    "gemini-2.5-flash",
        "env_key":  "GEMINI_API_KEY",
    },
}

BATCH_SIZE = 10
RETRY_WAIT = 5.0
MAX_RETRY  = 3

# ─── 解析モード ───────────────────────────────────────────────────────────────

MODE = "detail"  # "standard" or "detail"

SYSTEM_PROMPT_DETAIL = """あなたはポーカーのGTOコーチです。
個別ハンドの勝敗ではなく「このスポットでHeroのレンジは均衡しているか」を評価軸にしてください。

各ハンドを以下の構造で必ず日本語で出力してください。

- spot_range: このポジション・アクションシーケンス・ボードでHeroが均衡上持ちうるレンジの概要（1〜2文）
- balance_note: ハンドブロック内の[GTO数学]を踏まえ、ポジション・ストリート・相手テンデンシーで補正した均衡コメント（1〜2文）
  ※フロップ/ターンのブラフはエクイティを持つためMDFは目安に過ぎない。OOPはMDFより多めにフォールドが均衡に近い場合がある。
- hand_reading: 各ストリートで相手レンジがどう変化したか（1〜2文）
- opp_exploit: 相手のGTOからの逸脱と、それに対する具体的な搾取戦略（アクション名で）
- rep: Heroが表現できるハンドを2〜3個列挙（例：ハートフラッシュ・セット88）
- kaizen: 別の有効なラインがあれば提示。均衡上問題なければ「このラインで十分」も可

数値（bb・%）はbalance_noteとmath_contextの解釈に限り使用可。"""


def resolve_provider() -> tuple[str, str, str]:
    """(provider_name, api_key, model) を返す。キーがなければ終了。"""
    for name, cfg in PROVIDERS.items():
        key = os.environ.get(cfg["env_key"], "").strip()
        if key:
            return name, key, cfg["model"]
    print("[ERROR] GROQ_API_KEY または GEMINI_API_KEY が未設定です", file=sys.stderr)
    sys.exit(1)


def detect_provider(api_key: str) -> str:
    """キーの形式からプロバイダーを推定する。"""
    if api_key.startswith("gsk_"):
        return "groq"
    return "gemini"


def make_client(provider_name: str, api_key: str) -> OpenAI:
    cfg = PROVIDERS[provider_name]
    return OpenAI(api_key=api_key, base_url=cfg["base_url"])


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


# ─── GTO数学的文脈の事前計算 ──────────────────────────────────────────────────

def _compute_gto_math(hand: dict) -> str:
    """ベット/ポットからα・MDF・スポットタイプを計算してプロンプト用文字列を返す。
    計算できない場合は空文字。"""
    clf      = hand.get("bluered_classification") or {}
    category = clf.get("category", "")
    streets  = hand.get("streets") or {}

    # 最終ストリートとそのbet/potを抽出
    last_street = None
    last_bet    = None
    last_pot    = None
    hero_pos    = hand.get("hero_position", "")

    for street_name in ("river", "turn", "flop"):
        s = streets.get(street_name)
        if not s or not isinstance(s, dict):
            continue
        actions = s.get("actions") or []
        pot_bb  = s.get("pot_bb")
        for a in reversed(actions):
            amt = a.get("amount_bb")
            if amt and pot_bb:
                last_street = street_name
                last_bet    = float(amt)
                last_pot    = float(pot_bb)
                break
        if last_bet is not None:
            break

    if last_bet is None or last_pot is None or last_pot <= 0:
        return ""

    alpha = last_bet / (last_pot + last_bet)
    mdf   = 1 - alpha

    # スポットタイプとメッセージ
    spot_map = {
        "fold_unknown":        ("フォールドスポット", f"MDF基準={mdf:.0%}（Heroがフォールド）"),
        "hero_aggression_won": ("ブラフ/アグレッションスポット", f"必要成功率={alpha:.0%}（相手フォールドが必要な頻度）"),
        "value_success":       ("バリュースポット", f"バリューターゲット={alpha:.0%}（相手コールレンジに必要な劣勢ハンド率）"),
        "bluff_catch":         ("ブラフキャッチスポット", f"ポットオッズ={alpha:.0%}（相手ベットへのコール基準）"),
    }
    spot_type, spot_msg = spot_map.get(category, ("", ""))

    if not spot_type:
        # カテゴリ不明でもbet/potが取れた場合は基本情報だけ出す
        spot_type = "ベットスポット"
        spot_msg  = f"α={alpha:.0%} / MDF={mdf:.0%}"

    street_label = {"river": "リバー", "turn": "ターン", "flop": "フロップ"}.get(last_street, last_street or "")
    oop_note = "（OOP: MDF目安より多めのフォールドが均衡に近い場合あり）" if hero_pos in ("BB", "SB") else ""

    return (
        f"[GTO数学] {spot_type} | {street_label} | "
        f"ベット={last_bet:.1f}bb / ポット={last_pot:.1f}bb | "
        f"α={alpha:.0%} | {spot_msg}{oop_note}"
    )


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
    math   = _compute_gto_math(hand)

    allin_ev = hand.get("result", {}).get("allin_ev", {})
    ev_info  = ""
    if allin_ev:
        hero_name = next((p["name"] for p in hand.get("players", []) if p.get("is_hero")), None)
        if hero_name and hero_name in allin_ev:
            ev_info = f"\nAll-in EV: {allin_ev[hero_name]:+.2f}bb"

    math_line = f"\n{math}" if math else ""
    return f"""=== ハンド {idx} ===
ヒーロー: {pos} / 手札: {cards}
ボード: {board}{math_line}
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
    "detail": "80字以内の詳細（なぜその評価か）",
    "kaizen": "正しいアクションまたは代替ライン（✅🎲でも別の有効ラインを提示、❌⚠️は必須）50字以内",
    "ev_loss": "❌の場合のみ例: -15bb、それ以外は空文字",
    "hand_reading": "各ストリートの相手ハンドレンジ読み（例: Flop強いドロー多い、Turn 2pairをバリューベット）80字以内",
    "opp_gto_diff": "相手のGTOからのずれと搾取ポイント（過剰なCbet、コールステーションなど、わからなければ空文字）60字以内"
  }}
]"""


def reconstruct_evaluation(j: dict) -> str:
    lines = [f"GTO評価: {j['gto_rating']}"]
    if j.get("ichi"):          lines.append(f"一言: {j['ichi']}")
    if j.get("detail"):        lines.append(f"詳細: {j['detail']}")
    if j.get("kaizen"):        lines.append(f"代替ライン: {j['kaizen']}")
    if j.get("ev_loss"):       lines.append(f"EV損失推定: {j['ev_loss']}")
    if j.get("hand_reading"):  lines.append(f"ハンドリーディング: {j['hand_reading']}")
    if j.get("opp_gto_diff"):  lines.append(f"相手GTOずれ: {j['opp_gto_diff']}")
    return "\n".join(lines)


# ─── detail モード ────────────────────────────────────────────────────────────

def build_batch_prompt_detail(indexed_hands: list) -> str:
    """システムプロンプト用のユーザーメッセージ（手順・フィールド定義はsystemに委譲）"""
    blocks     = [build_hand_block(idx, hand) for idx, hand in indexed_hands]
    hands_text = "\n\n".join(blocks)
    n          = len(indexed_hands)

    return f"""以下の{n}ハンドを評価してください。
各ハンドに[GTO数学]ブロックがある場合はその数値を解釈に使ってください（計算は不要です）。

{hands_text}

以下のJSON配列形式のみで回答してください（説明文・コードブロック記号なし）:
[
  {{
    "id": <ハンド番号>,
    "spot_range": "このスポットでHeroが均衡上持ちうるレンジの概要（1〜2文）",
    "balance_note": "[GTO数学]の数値をポジション・ストリート・相手テンデンシーで補正した均衡コメント（1〜2文）",
    "hand_reading": "各ストリートの相手レンジ変化（1〜2文）",
    "opp_exploit": "相手のGTOからの逸脱と搾取戦略（具体的なアクション名で）",
    "rep": "Heroが表現できるハンド2〜3個（例: ハートフラッシュ・セット88）",
    "kaizen": "代替ライン or 「このラインで十分」"
  }}
]"""


# ─── explain モード ───────────────────────────────────────────────────────────

SYSTEM_PROMPT_EXPLAIN = """あなたはプロポーカープレイヤー兼GTOコーチです。

目的：
個別ハンドの勝敗ではなく「このスポットでHeroのレンジは均衡しているか」を軸に、
初心者〜中級者に向けて戦略的・論理的に深く解説すること。

要件：
- 最低400文字以上、最大1200文字程度
- 箇条書きと段落を組み合わせて読みやすく
- [GTO数学]ブロックがある場合はその数値を解釈に使う（再計算不要）
- MDF・必要成功率・バリューターゲットの意味を文脈に応じて説明する
- ポジション（OOP/IP）・レンジ構成・ブラフ:バリュー比・スタック深度に言及する
- 相手のGTOからの逸脱と搾取機会を具体的に述べる
- 「なぜそうなるか」の因果関係を必ず説明する
- 初心者にも理解できる言葉で、内容は浅くしない

出力形式（必ずこの構造で日本語で書くこと）：
均衡評価: [このスポットでのレンジ均衡についての一言]

（本文：以下のセクションを段落で展開）
・均衡レンジとHeroのポジション
・GTO数学的観点（MDF/必要成功率/バリューターゲット）
・相手レンジの変化と読み
・相手への搾取戦略
・代替ライン

禁止：
- JSON形式での出力
- 1〜2文だけの短い説明
- 根拠のない断定
- 個別ハンドの「良い/悪い/ミス」という結果論的評価"""


def build_explain_prompt(idx: int, hand: dict) -> str:
    """単一ハンドの詳細解説用ユーザーメッセージ"""
    block = build_hand_block(idx, hand)
    return f"""以下のハンドについて詳しく解説してください。
[GTO数学]ブロックがある場合はその数値を解釈に使ってください（再計算不要）。

{block}

特に以下を含めてください：
1. このスポットでHeroが均衡上持ちうるレンジとポジション（OOP/IP）の意味
2. [GTO数学]の数値（MDF・必要成功率・バリューターゲット）の解釈と意味
3. 相手レンジの変化と読み
4. 相手のGTO逸脱と搾取機会
5. 代替ライン

長文で詳しく日本語で説明してください。"""


def evaluate_explain_single(client: OpenAI, model: str, hand_idx: int, hand: dict) -> str:
    """1ハンドの詳細解説を生成して返す（free-text）"""
    prompt = build_explain_prompt(hand_idx, hand)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_EXPLAIN},
        {"role": "user",   "content": prompt},
    ]

    retries = 0
    while True:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.5,
                max_tokens=2000,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            err_str = str(e)
            is_retryable = (
                "429" in err_str
                or "rate_limit" in err_str.lower()
                or "503" in err_str
                or "UNAVAILABLE" in err_str.upper()
            )
            if is_retryable and retries < MAX_RETRY:
                retries += 1
                wait = RETRY_WAIT * retries
                print(
                    f"  [RETRY] explain H{hand_idx}: 過負荷/レート制限 — {wait:.0f}秒後リトライ "
                    f"({retries}/{MAX_RETRY})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise


def reconstruct_evaluation_detail(j: dict) -> str:
    lines = []
    if j.get("spot_range"):   lines.append(f"均衡レンジ: {j['spot_range']}")
    if j.get("balance_note"): lines.append(f"均衡評価: {j['balance_note']}")
    if j.get("hand_reading"): lines.append(f"ハンドリーディング: {j['hand_reading']}")
    if j.get("opp_exploit"):  lines.append(f"相手搾取: {j['opp_exploit']}")
    if j.get("rep"):          lines.append(f"Heroレンジ: {j['rep']}")
    if j.get("kaizen"):       lines.append(f"代替ライン: {j['kaizen']}")
    return "\n".join(lines)


# ─── API呼び出し（OpenAI互換） ────────────────────────────────────────────────

def _parse_json_response(raw: str) -> list:
    """コードブロック除去 → JSON直接パース → 失敗時は配列部分を抽出してリトライ"""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Llamaなどが前置き文章を付けた場合のフォールバック
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


def evaluate_batch(client: OpenAI, model: str, indexed_hands: list, mode: str = MODE) -> dict:
    """{hand_idx: evaluation_text} を返す"""
    if mode == "detail":
        prompt   = build_batch_prompt_detail(indexed_hands)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_DETAIL},
            {"role": "user",   "content": prompt},
        ]
        reconstruct = reconstruct_evaluation_detail
    else:
        prompt   = build_batch_prompt(indexed_hands)
        messages = [{"role": "user", "content": prompt}]
        reconstruct = reconstruct_evaluation

    retries = 0

    while True:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=4000,
            )
            raw     = response.choices[0].message.content.strip()
            results = _parse_json_response(raw)
            return {item["id"]: reconstruct(item) for item in results}

        except Exception as e:
            err_str = str(e)
            is_retryable = (
                "429" in err_str
                or "rate_limit" in err_str.lower()
                or "503" in err_str
                or "UNAVAILABLE" in err_str.upper()
            )
            if is_retryable and retries < MAX_RETRY:
                retries += 1
                wait = RETRY_WAIT * retries  # 指数バックオフ: 5s → 10s → 15s
                ids  = [idx for idx, _ in indexed_hands]
                print(
                    f"  [RETRY] バッチ{ids}: 過負荷/レート制限 — {wait:.0f}秒後リトライ "
                    f"({retries}/{MAX_RETRY})",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
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

def analyze_file(json_path: str, progress_cb=None, api_key: str = None, provider: str = None):
    """
    api_key: 外部から渡す場合（BYOKフロー）
    provider: "groq" or "gemini"（Noneなら api_key の形式 or 環境変数から自動解決）
    """
    if api_key:
        if not provider:
            provider = detect_provider(api_key)
        model  = PROVIDERS[provider]["model"]
        client = make_client(provider, api_key)
        print(f"  [PROVIDER] {provider} / {model} (BYOK)", file=sys.stderr)
    else:
        provider, api_key, model = resolve_provider()
        client = make_client(provider, api_key)
        print(f"  [PROVIDER] {provider} / {model} (env)", file=sys.stderr)

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
    print(f"  [ANALYZE] {len(pending)}ハンドを{len(batches)}バッチで評価します（{model}）")

    errors    = 0
    completed = cached

    for batch_idx, batch in enumerate(batches):
        result_map = evaluate_batch(client, model, batch)

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
        print("Usage: python scripts/analyze2.py <data.json>")
        sys.exit(1)

    json_path = sys.argv[1]
    total, errors = analyze_file(json_path)
    print(f"  Done: {total} hands analyzed, {errors} errors")


if __name__ == "__main__":
    main()
