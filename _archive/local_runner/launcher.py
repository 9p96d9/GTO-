"""
launcher.py - ポーカーGTO ランチャー
このファイルを PyInstaller で exe 化して使う
"""
import subprocess
import sys
import os


def main():
    # exe / .py 自身と同じフォルダの run.py を実行
    exe_dir  = os.path.dirname(os.path.abspath(sys.argv[0]))
    run_py   = os.path.join(exe_dir, "run.py")

    if not os.path.exists(run_py):
        print(f"[ERROR] run.py が見つかりません: {run_py}")
        input("\nEnterキーで終了...")
        sys.exit(1)

    result = subprocess.run(["python", run_py], cwd=exe_dir)

    print()
    if result.returncode == 0:
        print("処理が完了しました。")
    else:
        print(f"[ERROR] run.py がエラーで終了しました (code={result.returncode})")

    input("\nEnterキーで終了...")


if __name__ == "__main__":
    main()
