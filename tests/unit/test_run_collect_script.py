"""scripts/run-collect.ps1 を Windows PowerShell 5.1 で実行し、標準エラーへの出力で止まらないことを確認する。

2026-10-27、収集用 PC の定期実行で、slot-data の INFO ログ（標準エラー）の 1 行目でスクリプトが止まった不具合の回帰テスト。
実サイト・実 DB には接続しない（slot-data の代わりに tests/scripts/fake_slot_data.py を使う）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from tests.conftest import ROOT

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell.exe") is None, reason="Windows PowerShell が必要"
)


def test_run_collect_logs_stderr_and_returns_exit_code(tmp_path):
    log_dir = ROOT / "var" / "log"
    before = set(log_dir.glob("collect-*.log")) if log_dir.exists() else set()
    console_path = tmp_path / "console.txt"
    with console_path.open("wb") as console:
        proc = _run_script(console)
    _check(log_dir, before, proc, console_path)


def _run_script(console):
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "run-collect.ps1"),
            "-SlotDataExe",
            sys.executable,
            "-PrefixArgs",
            str(ROOT / "tests" / "scripts" / "fake_slot_data.py"),
        ],
        # パイプで受けると、スクリプトが起動した PostgreSQL がパイプを引き継いで閉じず、終わらなくなるためファイルで受ける
        stdout=console,
        stderr=subprocess.STDOUT,
        timeout=120,
    )


def _check(log_dir, before, proc, console_path):
    # 偽 slot-data の collect は終了コード 3 を返す。スクリプトはそれをそのまま返す
    assert proc.returncode == 3, console_path.read_text(encoding="utf-8", errors="replace")
    new_files = sorted(set(log_dir.glob("collect-*.log")) - before)
    summaries = [p for p in new_files if not p.name.endswith((".stderr.log", ".stdout.log"))]
    assert summaries, "ログファイルが作られていない"
    text = summaries[-1].read_text(encoding="utf-8-sig")
    new_logs = new_files
    try:
        assert "parsed 台番号=0277 [2]" in text  # 標準エラーの全行（日本語含む）
        assert '{"command": "collect", "status": "succeeded"}' in text  # 標準出力
        assert "collect exit=3" in text
        assert "audit exit=0" in text  # 収集の後も最後まで実行された
        # 収集の出力は実行中から別ファイルに書かれ、打ち切られた場合に備えて残される
        stderr_file = summaries[-1].with_name(summaries[-1].stem + "-collect.stderr.log")
        assert stderr_file.exists() and "parsed 台番号=0277 [2]" in stderr_file.read_text(encoding="utf-8")
        assert not summaries[-1].with_name(summaries[-1].stem + "-audit.stderr.log").exists()
    finally:
        for log in new_logs:
            log.unlink()
