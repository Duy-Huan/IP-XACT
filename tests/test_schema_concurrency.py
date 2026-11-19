"""Exercise the actual cold libxml schema factory in a fresh interpreter."""

import subprocess
import sys
from pathlib import Path


def test_cold_parallel_intake_shares_one_fully_initialized_schema():
    root = Path(__file__).resolve().parents[1]
    script = """
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from socsim.project import load_project
from socsim.ipxact import schema
barrier = Barrier(4)
def read(_):
    barrier.wait()
    p = load_project(Path('examples/rv64-ips').resolve())
    return p.ir.vlnv, len(p.ir.instances), id(schema())
with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(read, range(4)))
assert len(set(results)) == 1, results
assert results[0][1] == 9
print('COLD_PARALLEL_SCHEMA_OK')
"""
    run = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "COLD_PARALLEL_SCHEMA_OK"
