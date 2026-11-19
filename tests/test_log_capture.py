import gzip
import hashlib
import subprocess
import sys

from socsim.log_capture import LogCapture


def test_capture_preserves_actual_process_stdout_and_fifo_bytes(tmp_path):
    group = LogCapture(tmp_path)
    fifo = group.fifo("trace.jsonl")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; p=open(sys.argv[1],'wb'); "
            "p.write(b'1234567890\\n'*10000); p.close(); print('real child stdout')",
            str(fifo),
        ],
        stdout=subprocess.PIPE,
    )
    group.capture(process.stdout, "cpu.log")
    assert process.wait(timeout=10) == 0
    assert group.finish()
    assert group.finish()  # idempotent cleanup cannot close reused descriptors.
    assert not fifo.exists()
    payload = b"1234567890\n" * 10000
    assert gzip.decompress((tmp_path / "trace.jsonl.gz").read_bytes()) == payload
    assert gzip.decompress((tmp_path / "cpu.log.gz").read_bytes()) == b"real child stdout\n"
    assert group.records["trace.jsonl"]["raw_sha256"] == hashlib.sha256(payload).hexdigest()


def test_capture_releases_fifo_when_child_never_opens_it(tmp_path):
    group = LogCapture(tmp_path)
    group.fifo("trace.jsonl")
    assert group.finish()
    assert group.records["trace.jsonl"]["bytes"] == 0
    assert gzip.decompress((tmp_path / "trace.jsonl.gz").read_bytes()) == b""
