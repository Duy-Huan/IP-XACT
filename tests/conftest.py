import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_dir(tmp_path):
    path = tmp_path / "project"
    shutil.copytree(ROOT / "examples/riscv-virt", path)
    return path
