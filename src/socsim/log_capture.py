"""Lossless gzip capture of long native stdout and an owned POSIX trace FIFO."""

import gzip
import hashlib
import os
import stat
import threading
from pathlib import Path

from .domain import ContractError
from .ipxact import digest
from .runner import write_json


class LogCapture:
    def __init__(self, out: Path):
        self.out = out.resolve()
        self.threads: list[threading.Thread] = []
        self.keepers: list[int] = []
        self.fifos: list[Path] = []
        self.records: dict[str, dict] = {}
        self.errors: list[str] = []
        self.closed = False

    def _path(self, name):
        path = self.out / name
        if Path(name).name != name or path.exists() or path.is_symlink():
            raise ContractError("native log capture requires a fresh owned filename")
        return path

    def capture(self, stream, name):
        path = self._path(name + ".gz")

        def drain():
            try:
                raw = hashlib.sha256()
                size = 0
                with stream, path.open("xb") as target:
                    with gzip.GzipFile(
                        filename="", mode="wb", fileobj=target, compresslevel=1, mtime=0
                    ) as compressed:
                        while chunk := stream.read(1024 * 1024):
                            raw.update(chunk)
                            size += len(chunk)
                            compressed.write(chunk)
                self.records[name] = {
                    "codec": "gzip",
                    "bytes": size,
                    "raw_sha256": raw.hexdigest(),
                    "compressed_sha256": digest(path),
                }
            except (OSError, ValueError) as exc:
                self.errors.append(str(exc))

        thread = threading.Thread(target=drain, name="native-gzip-" + name, daemon=True)
        self.threads.append(thread)
        thread.start()

    def fifo(self, name):
        path = self._path(name)
        os.mkfifo(path, 0o600)
        self.fifos.append(path)
        reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            # Keep EOF away until the native process has opened/closed its writer.
            keeper = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            self.keepers.append(keeper)
            os.set_blocking(reader, True)
            self.capture(os.fdopen(reader, "rb"), name)
        except BaseException:
            os.close(reader)
            raise
        return path

    def finish(self):
        if self.closed:
            return not self.errors
        self.closed = True
        for descriptor in self.keepers:
            os.close(descriptor)
        for thread in self.threads:
            thread.join(timeout=5)
            if thread.is_alive():
                self.errors.append("native log compressor did not finish")
        for path in self.fifos:
            if path.exists() and stat.S_ISFIFO(path.stat().st_mode):
                path.unlink()
        write_json(
            self.out / "capture.json",
            {"complete": not self.errors, "errors": self.errors, "streams": self.records},
        )
        return not self.errors


def open_log(path):
    return path.open() if path.exists() else gzip.open(str(path) + ".gz", "rt")
