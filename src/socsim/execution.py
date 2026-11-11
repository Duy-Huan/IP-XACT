"""Scoped cancellation handling for owned native subprocesses."""

import signal
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
