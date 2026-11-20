"""Positive and negative tests for CHIA-style Emulator SDK and real QA/QC runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from socsim.cli import main as cli_main
from socsim.domain import ContractError
from socsim.qa_runner import run_qa, verify_scorecard
from socsim.sdk import ArtifactCache, EmulatorNode, ProcessRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_process_registry_terminates_owned_children_without_orphans():
    with ProcessRegistry() as registry:
        proc = registry.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
        pid = proc.pid
        assert pid in registry.active_pids()
        terminated = registry.terminate_all(grace_seconds=1.0)
        assert pid in terminated
        assert registry.active_pids() == []


def test_artifact_cache_labels_replay_and_rejects_tampering(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    inputs = {"spec": "helios", "version": 1}
    assert cache.get("build", inputs) is None
    key = cache.put("build", inputs, {"status": "ok", "replayed": False})
    hit = cache.get("build", inputs)
    assert hit is not None
    assert hit["status"] == "ok"
    assert hit["replayed"] is True

    entry = tmp_path / "cache" / f"{key}.json"
    tampered = json.loads(entry.read_text())
    tampered["payload"]["status"] = "forged"
    entry.write_text(json.dumps(tampered))
    with pytest.raises(ContractError, match="E_CACHE_TAMPER"):
        cache.get("build", inputs)


def test_emulator_node_unseen_soc_generation_cache_replay_and_timeout(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    spec = ROOT / "specs/qa/unseen_dual_ip_soc.yaml"
    with EmulatorNode("qa_node", cache=cache) as node:
        first = node.generate_soc(spec, tmp_path / "gen1", engine="both", allow_replay=True)
        assert first.success is True
        assert first.design == "Helios_QA_SoC"
        assert first.replayed is False

        second = node.generate_soc(spec, tmp_path / "gen2", engine="both", allow_replay=True)
        assert second.replayed is True
        assert second.ir_sha256 == first.ir_sha256

        timed = node.run_command(
            "timeout_probe",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.2,
            log_path=tmp_path / "timeout.log",
        )
        assert timed.timed_out is True
        assert timed.success is False
        assert node.registry.active_pids() == []

        profile_path = node.write_profile(tmp_path / "profile.json")
        profile = json.loads(profile_path.read_text())
        assert len(profile["events"]) == 3


def test_qa_runner_unseen_suite_and_tamper_verification(tmp_path):
    qa_out = tmp_path / "qa-unseen"
    report = run_qa(ROOT, qa_out, suite="unseen")
    assert report["status"] == "PASSED"
    assert report["gates"]["unseen_design"]["design"] == "Helios_QA_SoC"
    assert all(report["gates"]["unseen_design"]["checks"].values())
    assert (qa_out / "scorecard.md").is_file()

    verified = verify_scorecard(qa_out)
    assert verified["status"] == "PASSED"
    assert cli_main(["qa", "scorecard", str(qa_out)]) == 0

    # Tampering with scorecard.json must fail verification
    bad = json.loads((qa_out / "scorecard.json").read_text())
    bad["status"] = "FORGED"
    (qa_out / "scorecard.json").write_text(json.dumps(bad))
    with pytest.raises(ContractError, match="E_QA_VERIFY"):
        verify_scorecard(qa_out)
    assert cli_main(["qa", "scorecard", str(qa_out)]) == 2
