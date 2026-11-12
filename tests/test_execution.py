import json
import signal
import subprocess
import sys
import time

import pytest

from socsim.domain import ContractError
from socsim.execution import _stop_build, execute_build


def test_build_timeout_preserves_a_real_signal_checkpoint_and_remains_failure(tmp_path):
    script = tmp_path / "build.py"
    script.write_text(
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "def checkpoint(*args):\n"
        "    Path('checkpoint').write_text('completed build work')\n"
        "    sys.exit(2)\n"
        "signal.signal(signal.SIGTERM, checkpoint)\n"
        "time.sleep(30)\n"
    )
    log = tmp_path / "build.log"
    with pytest.raises(ContractError, match="timeout"):
        execute_build([sys.executable, str(script)], tmp_path, log, timeout=1)
    assert (tmp_path / "checkpoint").read_text() == "completed build work"
    record = json.loads(log.with_suffix(".command.json").read_text())
    assert record["returncode"] == 2 and record["timed_out"] is True


def test_unresponsive_owned_build_is_killed_after_the_grace_period(tmp_path):
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,sys,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "Path(sys.argv[1]).touch(); time.sleep(30)",
            str(ready),
        ],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        _stop_build(process, grace=0.05)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None:
            _stop_build(process, grace=0.05)
