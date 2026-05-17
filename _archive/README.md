# _archive/ — 現用でないファイルの保管庫

> 削除ではなく参照用に保管。git履歴でも追跡可能。

| フォルダ/ファイル | 元の場所 | 理由 |
|---|---|---|
| `aws/` | `aws/` | ECSクラスター・ALB削除済み（2026-05-18）。EC2+Cloudflare Tunnel構成に移行 |
| `scripts/generate.js` | `scripts/` | WeasyPrint版（generate.py）に置き換え済み |
| `scripts/generate_noapilist.js` | `scripts/` | 同上（generate_noapilist.py） |
| `scripts/export_powerbi.py` | `scripts/` | export_firebase_csv.py に統合済み |
| `scripts/quick_analyzer.py` | `scripts/` | state.pyに定義はあるがroutes/pipelinesから未使用 |
| `local_runner/` | ルート | 旧ローカル一括処理CLI（run.py/run.bat/launcher.py）。削除済みのanalyze.pyとgenerate.jsに依存 |
| `bookmarklet.js` | ルート | RailwayURL切れ・Chrome拡張機能で完全代替済み |
| `test.js` | ルート | Node.js/docxライブラリの動作確認用。WeasyPrint移行後は不要 |
| `test_sample.txt` | ルート | テスト用ハンド履歴データ |
| `package.json` / `package-lock.json` | ルート | npmパッケージ管理。Node.jsスクリプト廃止に伴い不要 |
| `static/css_test.html` | `static/` | CSS確認用の開発時モックファイル |
