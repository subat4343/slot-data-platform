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


def test_run_collect_logs_stderr_and_returns_exit_code():
    log_dir = ROOT / "var" / "log"
    before = set(log_dir.glob("collect-*.log")) if log_dir.exists() else set()
    proc = subprocess.run(
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
        capture_output=True,
        timeout=120,
    )
    # 偽 slot-data の collect は終了コード 3 を返す。スクリプトはそれをそのまま返す
    assert proc.returncode == 3, proc.stderr.decode("utf-8", "replace")
    new_logs = sorted(set(log_dir.glob("collect-*.log")) - before)
    assert new_logs, "ログファイルが作られていない"
    text = new_logs[-1].read_text(encoding="utf-8-sig")
    try:
        assert "parsed 台番号=0277 [2]" in text  # 標準エラーの全行（日本語含む）
        assert '{"command": "collect", "status": "succeeded"}' in text  # 標準出力
        assert "collect exit=3" in text
        assert "audit exit=0" in text  # 収集の後も最後まで実行された
    finally:
        for log in new_logs:
            log.unlink()
