"""CHIA-inspired Python SDK for composable HW/SW SoC emulation nodes.

Provides typed immutable artifacts, explicit process-group cleanup,
content-addressed caching with mandatory replay labeling, and structured
execution profiling over the qualified USP compiler and flow primitives.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain import ContractError
from .ipxact import digest
from .runner import write_json
from .validation import canonical_json_dict, require_identifier, require_sha256


@dataclass(frozen=True)
class BuildArtifact:
    """Typed, hash-bound hardware/software generation artifact."""

    name: str
    design: str
    output_dir: str
    manifest_sha256: str
    ir_sha256: str
    success: bool
    duration_seconds: float
    replayed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return canonical_json_dict(self)


@dataclass(frozen=True)
class RunResult:
    """Typed execution result for an emulator or verification command."""

    name: str
    command: tuple[str, ...]
    returncode: int
    success: bool
    duration_seconds: float
    stdout_sha256: str
    stderr_sha256: str
    replayed: bool = False
    timed_out: bool = False
    log_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return canonical_json_dict(self)


@dataclass(frozen=True)
class CrossEngineDiffResult:
    """Structured cross-engine agreement check (QEMU vs gem5 / RTL)."""

    design: str
    engines: tuple[str, str]
    same_ir: bool
    same_elf: bool
    trace_match: bool
    matched: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return canonical_json_dict(self)


class ProcessRegistry:
    """Tracks owned child process groups and guarantees zero orphan processes."""

    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen[bytes]] = {}

    def spawn(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout: Any = subprocess.PIPE,
        stderr: Any = subprocess.PIPE,
    ) -> subprocess.Popen[bytes]:
        if not argv or any(not isinstance(a, str) for a in argv):
            raise ContractError("E_SDK_CMD command must be a non-empty argument array")
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        self._processes[proc.pid] = proc
        return proc

    def active_pids(self) -> list[int]:
        alive: list[int] = []
        for pid, proc in list(self._processes.items()):
            if proc.poll() is None:
                alive.append(pid)
            else:
                self._processes.pop(pid, None)
        return sorted(alive)

    def terminate_all(self, grace_seconds: float = 2.0) -> list[int]:
        """Terminate all tracked process groups cleanly (SIGTERM then SIGKILL)."""
        terminated: list[int] = []
        for pid, proc in list(self._processes.items()):
            if proc.poll() is not None:
                self._processes.pop(pid, None)
                continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            terminated.append(pid)
        deadline = time.monotonic() + max(0.1, grace_seconds)
        for pid in list(terminated):
            tracked = self._processes.get(pid)
            if tracked is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                tracked.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                tracked.wait(timeout=5)
            self._processes.pop(pid, None)
        return sorted(terminated)

    def __enter__(self) -> ProcessRegistry:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.terminate_all()


class ArtifactCache:
    """Content-addressed JSON artifact cache with tamper checks and replay tagging."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for(tag: str, inputs: dict[str, Any]) -> str:
        require_identifier(tag, "cache tag")
        canonical = json.dumps({"tag": tag, "inputs": inputs}, sort_keys=True).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _entry_path(self, key: str) -> Path:
        require_sha256(key, "cache key")
        return self.root / f"{key}.json"

    def put(self, tag: str, inputs: dict[str, Any], payload: dict[str, Any]) -> str:
        key = self.key_for(tag, inputs)
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        envelope = {
            "schema_version": "usp-sdk-cache-1",
            "tag": tag,
            "key": key,
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "payload": payload,
        }
        write_json(self._entry_path(key), envelope)
        return key

    def get(self, tag: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
        key = self.key_for(tag, inputs)
        path = self._entry_path(key)
        if not path.is_file():
            return None
        envelope = json.loads(path.read_text())
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema_version") != "usp-sdk-cache-1"
            or envelope.get("key") != key
            or envelope.get("tag") != tag
        ):
            raise ContractError("E_CACHE_TAMPER cache envelope metadata mismatch")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ContractError("E_CACHE_TAMPER cached artifact payload must be a mapping")
        actual_sha = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if actual_sha != envelope.get("payload_sha256"):
            raise ContractError("E_CACHE_TAMPER cached artifact payload digest mismatch")
        replayed = dict(payload)
        replayed["replayed"] = True
        return replayed


class EmulatorNode:
    """Composable CHIA-style node wrapping native USP generation and command runs."""

    def __init__(
        self,
        name: str,
        *,
        cache: ArtifactCache | None = None,
        registry: ProcessRegistry | None = None,
    ) -> None:
        self.name = require_identifier(name, "node name")
        self.cache = cache
        self.registry = registry or ProcessRegistry()
        self._owns_registry = registry is None
        self.profile: list[dict[str, Any]] = []

    def generate_soc(
        self,
        spec_path: Path,
        out_dir: Path,
        *,
        engine: str = "both",
        allow_replay: bool = False,
    ) -> BuildArtifact:
        """Generate IP-XACT, Canonical IR, QEMU/gem5 graphs and firmware from a spec."""
        from .soc_native import generate

        spec_path = spec_path.resolve()
        out_dir = out_dir.resolve()
        inputs = {"spec_sha256": digest(spec_path), "engine": engine}
        if allow_replay and self.cache is not None:
            cached = self.cache.get("generate_soc", inputs)
            if cached is not None:
                artifact = BuildArtifact(**cached)
                self._record("generate_soc", 0.0, True, replayed=True)
                return artifact

        start = time.monotonic()
        generate(spec_path, out_dir, engine=engine)
        duration = round(time.monotonic() - start, 4)
        from .native_ir import read_ir

        ir = read_ir(out_dir / "native-ir.json")
        artifact = BuildArtifact(
            name=self.name,
            design=ir.name,
            output_dir=str(out_dir),
            manifest_sha256=digest(out_dir / "manifest.json"),
            ir_sha256=digest(out_dir / "native-ir.json"),
            success=True,
            duration_seconds=duration,
            replayed=False,
            metadata={"engine": engine, "spec_sha256": inputs["spec_sha256"]},
        )
        if self.cache is not None:
            self.cache.put("generate_soc", inputs, artifact.to_dict())
        self._record("generate_soc", duration, True, replayed=False)
        return artifact

    def run_command(
        self,
        label: str,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        log_path: Path | None = None,
    ) -> RunResult:
        """Execute a bounded external command with full group cleanup and log hashing."""
        require_identifier(label, "run label")
        start = time.monotonic()
        proc = self.registry.spawn(argv, cwd=cwd, env=env)
        timed_out = False
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            self.registry.terminate_all()
            stdout_b, stderr_b = proc.communicate(timeout=5)
            rc = -9
        duration = round(time.monotonic() - start, 4)
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(stdout_b + b"\n--- STDERR ---\n" + stderr_b)
        result = RunResult(
            name=label,
            command=tuple(argv),
            returncode=rc,
            success=(rc == 0 and not timed_out),
            duration_seconds=duration,
            stdout_sha256=hashlib.sha256(stdout_b).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr_b).hexdigest(),
            replayed=False,
            timed_out=timed_out,
            log_path=str(log_path) if log_path else None,
        )
        self._record(label, duration, result.success, replayed=False)
        return result

    def run_in_podman(
        self,
        label: str,
        command: list[str],
        *,
        repo_root: Path,
        engine: str = "podman",
        image: str | None = None,
        timeout: float = 300.0,
        log_path: Path | None = None,
    ) -> RunResult:
        """Run a command inside the rootless Podman/Docker SDK container worker."""
        if engine not in {"podman", "docker"}:
            raise ContractError("E_SDK_OCI engine must be podman or docker")
        if not command:
            raise ContractError("E_SDK_CMD container command cannot be empty")
        env = dict(os.environ)
        env["USP_OCI_ENGINE"] = engine
        if image is not None:
            env["USP_CONTAINER_IMAGE"] = image
        script = str(repo_root.resolve() / "scripts/podman.sh")
        return self.run_command(
            label,
            ["bash", script, "run", *command],
            cwd=repo_root.resolve(),
            env=env,
            timeout=timeout,
            log_path=log_path,
        )

    def write_profile(self, path: Path) -> Path:
        write_json(path, {"node": self.name, "events": self.profile})
        return path

    def _record(self, step: str, duration_s: float, success: bool, *, replayed: bool) -> None:
        self.profile.append(
            {
                "step": step,
                "duration_seconds": duration_s,
                "success": success,
                "replayed": replayed,
            }
        )

    def close(self) -> None:
        if self._owns_registry:
            self.registry.terminate_all()

    def __enter__(self) -> EmulatorNode:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
