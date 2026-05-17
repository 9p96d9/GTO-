"""
run.py - ポーカーGTOレポート 一括生成スクリプト
使用法: python run.py
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# Windows でのコンソール出力を UTF-8 に統一
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# プロジェクトルートをPATHに追加
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "scripts"))

INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
DONE_DIR = INPUT_DIR / "done"
SCRIPTS_DIR = ROOT / "scripts"


def find_input_files() -> list[Path]:
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir(parents=True)
    return sorted(INPUT_DIR.glob("*.txt"))


def run_parse(input_path: Path, json_path: Path) -> int:
    """parse.py を呼び出してJSONを生成する"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "parse.py"), str(input_path), str(json_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"parse.py failed with code {result.returncode}")

    # 生成されたJSONからハンド数を返す
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("hands", []))
    return 0


def run_analyze(json_path: Path, total_hands: int):
    """analyze.py を呼び出してGTO評価を追記する"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # analyze.py は JSON を直接更新する
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "analyze.py"), str(json_path)],
        capture_output=False,  # リアルタイム出力
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"analyze.py failed with code {result.returncode}")


def run_generate(json_paths: list[Path]):
    """generate.js を呼び出して全ファイル分をまとめて1つのdocxを生成する"""
    result = subprocess.run(
        ["node", str(SCRIPTS_DIR / "generate.js"), str(OUTPUT_DIR)] + [str(p) for p in json_paths],
        capture_output=False,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"generate.js failed with code {result.returncode}")


def move_to_done(input_path: Path):
    """処理済みファイルを input/done/ に移動する"""
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = DONE_DIR / input_path.name
    # 同名ファイルが既にある場合は番号付きで保存
    if dest.exists():
        stem = input_path.stem
        suffix = input_path.suffix
        counter = 1
        while dest.exists():
            dest = DONE_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
    shutil.move(str(input_path), str(dest))
    print(f"  Moved to: {dest}")


def check_dependencies():
    """依存パッケージの確認（ローカル版・API不要）"""
    errors = []

    # Node.js のみ必要（generate.js 用）
    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        errors.append("Node.js が見つかりません（https://nodejs.org からインストールしてください）")

    # # --- API版依存チェック（無効化中）---
    # try:
    #     import dotenv
    # except ImportError:
    #     errors.append("python-dotenv が未インストールです: pip install python-dotenv")
    # try:
    #     import anthropic
    # except ImportError:
    #     errors.append("anthropic が未インストールです: pip install anthropic")
    # pkg_result = subprocess.run(
    #     ["node", "-e", "require('puppeteer')"],
    #     capture_output=True, text=True, cwd=str(ROOT),
    # )
    # if pkg_result.returncode != 0:
    #     errors.append("puppeteer npm パッケージが未インストールです: npm install")
    # env_path = ROOT / ".env"
    # if not env_path.exists():
    #     errors.append(".env ファイルが見つかりません")
    # else:
    #     with open(env_path) as f:
    #         content = f.read()
    #     if "ANTHROPIC_API_KEY=" not in content:
    #         errors.append(".env に ANTHROPIC_API_KEY が設定されていません")

    return errors


def main():
    print("=" * 60)
    print("ポーカー GTO レポート生成システム")
    print("=" * 60)

    # 依存チェック
    dep_errors = check_dependencies()
    if dep_errors:
        print("\n[ERROR] 依存パッケージのエラー:")
        for e in dep_errors:
            print(f"  - {e}")
        sys.exit(1)

    # 出力・データディレクトリ作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 処理対象ファイルを取得
    input_files = find_input_files()
    if not input_files:
        print(f"\ninput/ フォルダに .txt ファイルが見つかりません。")
        print(f"  場所: {INPUT_DIR}")
        sys.exit(0)

    total_files = len(input_files)
    print(f"\n{total_files} ファイルを処理します\n")

    error_summary = []

    # Step 1: 全ファイルを parse
    parse_results: list[tuple[Path, Path, int]] = []  # (input_path, json_path, hand_count)
    for file_idx, input_path in enumerate(input_files, 1):
        print(f"[parse {file_idx}/{total_files}] {input_path.name} ...", end="", flush=True)
        json_path = DATA_DIR / (input_path.stem + ".json")
        try:
            hand_count = run_parse(input_path, json_path)
            print(f" {hand_count} hands")
            parse_results.append((input_path, json_path, hand_count))
        except Exception as e:
            print(f"\n  [ERROR] {input_path.name}: {e}")
            error_summary.append((input_path.name, str(e)))

    print()

    # Step 2: 全ファイルを analyze（parse 済みのもの）
    parsed_pairs: list[tuple[Path, Path]] = []
    for file_idx, (input_path, json_path, hand_count) in enumerate(parse_results, 1):
        print(f"[analyze {file_idx}/{len(parse_results)}] {input_path.name}")
        try:
            run_analyze(json_path, hand_count)
            parsed_pairs.append((input_path, json_path))
        except Exception as e:
            print(f"\n  [ERROR] {input_path.name}: {e}")
            error_summary.append((input_path.name, str(e)))
        print()

    # Step 3: 全ファイルをまとめて1つのdocxに生成
    if parsed_pairs:
        json_paths = [jp for _, jp in parsed_pairs]
        print(f"generating: GTO_Report_[日付範囲].docx ({len(json_paths)} ファイル結合) ...", end="", flush=True)
        try:
            run_generate(json_paths)
            print(" done\n")
            # Step 4: 成功したファイルを done/ に移動
            for input_path, _ in parsed_pairs:
                move_to_done(input_path)
        except Exception as e:
            print(f"\n  [ERROR] generate: {e}")
            error_summary.append(("generate.js", str(e)))
    else:
        print("生成対象のファイルがありません。")

    # サマリー
    print()
    print("=" * 60)
    print(f"完了: {len(parsed_pairs)}/{total_files} ファイル処理成功")
    if error_summary:
        print(f"\nエラーサマリー ({len(error_summary)} 件):")
        for fname, err in error_summary:
            print(f"  - {fname}: {err}")
    print(f"\n出力先: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
