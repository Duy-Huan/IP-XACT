"""Regression guards for project standardization: requirements sync, setup scripts,
container compose, Makefile, and industrial documentation metadata."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _parse_requirements(path: Path) -> tuple[list[str], list[str]]:
    """Return (includes, pinned_packages) from a requirements*.txt file."""
    includes: list[str] = []
    packages: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            includes.append(line.split(None, 1)[1].strip())
        else:
            packages.append(line)
    return includes, packages


def test_requirements_files_match_pyproject_exact_pins() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    opt = project["optional-dependencies"]

    inc_base, pkg_base = _parse_requirements(ROOT / "requirements.txt")
    assert inc_base == []
    assert pkg_base == project["dependencies"]

    for extra_name in ("dev", "models", "bringup"):
        req_file = ROOT / f"requirements-{extra_name}.txt"
        assert req_file.is_file(), f"Missing {req_file.name}"
        includes, pkgs = _parse_requirements(req_file)
        assert includes == ["requirements.txt"]
        assert pkgs == opt[extra_name]

    inc_all, pkg_all = _parse_requirements(ROOT / "requirements-all.txt")
    assert inc_all == [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-models.txt",
        "requirements-bringup.txt",
    ]
    assert pkg_all == []


@pytest.mark.parametrize(
    ("script_rel", "expected_token"),
    [
        ("scripts/setup_venv.sh", "--method auto|uv|pip"),
        ("scripts/setup_qemu.sh", "7c949c53e936aa3a658d84ab53bae5cadaa5d59c"),
        ("scripts/setup_gem5.sh", "b9da2bfe1e21f6efa2730edd4e80e58c5d965458"),
        ("scripts/setup_chipyard.sh", "69eba860a352343e4ac6b6df0f3638a79a86ec78"),
        ("scripts/setup_opentitan.sh", "ed044fc9760bdf9fc075d0015ba1db07fa075355"),
        ("scripts/setup_all.sh", "--opentitan"),
    ],
)
def test_setup_scripts_syntax_executable_and_help(script_rel: str, expected_token: str) -> None:
    script = ROOT / script_rel
    assert script.is_file()
    assert os.access(script, os.X_OK), f"{script_rel} must be executable"

    syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    help_run = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
    assert help_run.returncode == 0, help_run.stderr
    assert expected_token in help_run.stdout


def test_setup_venv_check_passes_for_active_environment() -> None:
    venv_dir = Path(sys.prefix)
    res = subprocess.run(
        ["bash", str(ROOT / "scripts/setup_venv.sh"), "--check", "--venv", str(venv_dir)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Python venv audit PASSED" in res.stdout


def test_container_compose_and_makefile_contracts() -> None:
    compose = yaml.safe_load((ROOT / "containers/compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"sdk", "mcp", "qa", "unified-soc"} <= set(services.keys())
    for name in ("sdk", "mcp", "qa", "unified-soc"):
        svc = services[name]
        assert "ALL" in svc["cap_drop"]
        assert "no-new-privileges:true" in svc["security_opt"]
    assert services["mcp"]["tty"] is False
    assert services["mcp"]["stdin_open"] is True

    makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "venv:",
        "setup-check:",
        "setup-qemu:",
        "setup-gem5:",
        "setup-chipyard:",
        "setup-opentitan:",
        "lint:",
        "typecheck:",
        "test:",
        "check:",
        "podman-build:",
        "podman-smoke:",
    ):
        assert target in makefile_text, f"Missing Makefile target: {target}"


@pytest.mark.parametrize(
    "doc_rel",
    [
        "docs/README.md",
        "docs/architecture.md",
        "docs/setup-and-environment.md",
        "docs/unified-systemc-soc.md",
        "docs/mcp-server.md",
        "docs/podman.md",
        "src/README.md",
        "scripts/README.md",
        "containers/README.md",
        "examples/ASoC/README.md",
        "docs/examples/ASoC.md",
        "examples/UspNextGen/README.md",
        "docs/examples/UspNextGen.md",
        "upgrade/usp-v2-core-upgrades.md",
    ],
)
def test_industrial_docs_have_metadata_and_revision_history(doc_rel: str) -> None:
    text = (ROOT / doc_rel).read_text(encoding="utf-8")
    assert "**Document ID**" in text, f"{doc_rel} missing Document ID metadata"
    assert "**Normative Authority**" in text, f"{doc_rel} missing Normative Authority metadata"
    assert "## Document Revision History" in text, f"{doc_rel} missing Document Revision History"


def test_mcp_doc_covers_all_registered_mcp_tools() -> None:
    from socsim.mcp_server import EmulatorMcpServer

    mcp_doc = (ROOT / "docs/mcp-server.md").read_text(encoding="utf-8")
    tools = EmulatorMcpServer(ROOT).list_tools()
    assert len(tools) >= 20
    for tool in tools:
        assert f"`{tool['name']}`" in mcp_doc, (
            f"MCP tool {tool['name']} undocumented in docs/mcp-server.md"
        )
