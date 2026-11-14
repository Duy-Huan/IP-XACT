"""Exercise the command wrapper with an argument-recording OCI client, without a daemon."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    project = tmp_path / "source with spaces"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/podman.sh", project / "scripts/podman.sh")
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "podman"
    stub.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\n"
        "with open(os.environ['OCI_CALLS'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
    )
    stub.chmod(0o755)
    log = tmp_path / "argv.jsonl"
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"], OCI_CALLS=str(log))
    env.pop("USP_OCI_ENGINE", None)
    env.pop("USP_CONTAINER_STATE", None)
    env.pop("USP_SELINUX_LABEL", None)
    return project, env, log


def test_rootless_mounts_keep_uid_and_literal_arguments(client):
    project, env, log = client
    args = ["printf", "%s", "literal; $(touch should-not-exist)"]
    p = subprocess.run(["bash", "scripts/podman.sh", "run", *args], cwd=project, env=env)
    assert p.returncode == 0
    call = json.loads(log.read_text())
    assert "--userns=keep-id" in call
    assert call[call.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert call[-3:] == args
    assert "--privileged" not in call and "--cap-drop=ALL" in call
    assert f"{project}:/workspace:Z" in call
    assert f"{project}/.podman/results:/workspace/results:Z" in call
    assert not (project / "should-not-exist").exists()


def test_sdk_smoke_is_offline_and_does_not_mount_host_sources(client):
    project, env, log = client
    p = subprocess.run(["bash", "scripts/podman.sh", "smoke"], cwd=project, env=env)
    assert p.returncode == 0
    call = json.loads(log.read_text())
    assert "--network=none" in call and "--volume" not in call


@pytest.mark.parametrize("case", ["engine", "label", "build-args"])
def test_invalid_wrapper_configuration_does_not_start_a_container(client, case):
    project, env, log = client
    args = ["run", "true"]
    if case == "engine":
        env["USP_OCI_ENGINE"] = "sh -c injected"
    if case == "label":
        env["USP_SELINUX_LABEL"] = "rw,U"
    if case == "build-args":
        args = ["build", "ignored"]
    p = subprocess.run(["bash", "scripts/podman.sh", *args], cwd=project, env=env)
    assert p.returncode != 0 and not log.exists()


def test_build_context_uses_committed_snapshot_and_preserves_private_files(client):
    project, env, log = client
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "scripts/podman.sh"], cwd=project, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=project,
        check=True,
    )
    private = project / "private.zip"
    private.write_text("must never be passed to the builder")
    binary = Path(env["PATH"].split(os.pathsep)[0]) / "podman"
    binary.write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys\n"
        "p=pathlib.Path(sys.argv[-1]); assert not (p/'private.zip').exists()\n"
        "assert (p/'scripts/podman.sh').exists()\n"
    )
    p = subprocess.run(["bash", "scripts/podman.sh", "build"], cwd=project, env=env)
    assert p.returncode == 0 and private.exists()
    (project / "scripts/podman.sh").write_text("dirty")
    # Invoke an unchanged copy to check the refusal against the actual dirty checkout.
    shutil.copy2(ROOT / "scripts/podman.sh", project / "scripts/runner.sh")
    p = subprocess.run(["bash", "scripts/runner.sh", "build"], cwd=project, env=env)
    assert p.returncode != 0


def test_docker_and_podman_context_filters_are_identical_and_exclude_private_inputs():
    text = (ROOT / ".containerignore").read_text()
    assert text == (ROOT / ".dockerignore").read_text()
    assert "**/*.zip" in text and "**/*.key" in text
    assert "!.git\n" not in text and "!ref/**" not in text
    assert "!src/**" in text
