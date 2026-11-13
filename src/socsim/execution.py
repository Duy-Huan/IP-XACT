"""Scoped cancellation handling for owned native subprocesses."""

import os
import signal
import subprocess
import threading
from functools import wraps


def _terminate(signum, frame):
    # Raising allows each owner's existing finally/BaseException cleanup to run.
    raise SystemExit(128 + signum)


def cleanup_on_termination(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            return function(*args, **kwargs)
        previous = signal.signal(signal.SIGTERM, _terminate)
        try:
            return function(*args, **kwargs)
        finally:
            signal.signal(signal.SIGTERM, previous)

    return wrapped


def _stop_build(process, grace=10):
    """Let an owned build checkpoint, then remove unresponsive descendants."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


@cleanup_on_termination
def execute_build(argv, cwd, log, timeout=7200, env=None):
    """Bound native builds while preserving SCons' interrupted-build database."""
    from .domain import ContractError
    from .runner import write_json

    log.parent.mkdir(parents=True, exist_ok=True)
    command = list(map(str, argv))
    record = {"argv": command, "cwd": str(cwd), "termination_grace_seconds": 10}
    command_file = log.with_suffix(".command.json")
    write_json(command_file, record)
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_build(process)
            write_json(command_file, dict(record, returncode=process.returncode, timed_out=True))
            raise ContractError(f"command timeout; see {log}") from exc
        except BaseException:
            _stop_build(process)
            write_json(command_file, dict(record, returncode=process.returncode, interrupted=True))
            raise
    write_json(command_file, dict(record, returncode=rc, timed_out=False))
    if rc:
        raise ContractError(
            f"command failed ({rc}); see {log}:\n{log.read_text(errors='replace')[-2500:]}"
        )
    return rc
