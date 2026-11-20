"""End-to-end and unit tests for the USP HW/SW Emulator MCP JSON-RPC 2.0 stdio server."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from socsim.mcp_server import (
    DEFAULT_PROTOCOL_VERSION,
    SERVER_NAME,
    EmulatorMcpServer,
    render_client_config,
    serve_stdio,
)
from socsim.multiplatform_qa import _has_systemc_toolchain

ROOT = Path(__file__).resolve().parents[1]


def _rpc_send(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _rpc_recv(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout unexpectedly"
    return json.loads(line.decode("utf-8"))


def test_mcp_protocol_lifecycle_resources_prompts_and_errors():
    server = EmulatorMcpServer(ROOT)

    init = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": DEFAULT_PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
        }
    )
    assert init is not None
    assert init["result"]["protocolVersion"] == DEFAULT_PROTOCOL_VERSION
    assert init["result"]["serverInfo"]["name"] == SERVER_NAME
    assert "tools" in init["result"]["capabilities"]

    # Notification produces no response
    assert (
        server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        is None
    )

    ping = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping == {"jsonrpc": "2.0", "id": 2, "result": {}}

    tools_res = server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert tools_res is not None
    tool_names = {t["name"] for t in tools_res["result"]["tools"]}
    assert {
        "emulator_doctor",
        "emulator_list_specs",
        "emulator_list_models",
        "emulator_check_cpu_adapter",
        "emulator_validate_spec",
        "emulator_generate_soc",
        "emulator_preflight_flow",
        "emulator_derive_domains",
        "emulator_synthesize_bsp",
        "emulator_derive_noc_topology",
        "emulator_package_inspect",
        "emulator_package_build",
        "emulator_run_qa",
        "emulator_run_qemu",
    }.issubset(tool_names)

    res_list = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert res_list is not None
    uris = [r["uri"] for r in res_list["result"]["resources"]]
    assert "usp://catalog/ip-readiness" in uris
    for uri in uris:
        read_res = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": f"read-{uri}",
                "method": "resources/read",
                "params": {"uri": uri},
            }
        )
        assert read_res is not None
        body = json.loads(read_res["result"]["contents"][0]["text"])
        assert isinstance(body, dict)

    prompts_list = server.handle_message({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
    assert prompts_list is not None
    assert len(prompts_list["result"]["prompts"]) == 3
    prompt_get = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "prompts/get",
            "params": {
                "name": "audit_multiplatform_architecture",
                "arguments": {"requested_ips": "gem5 NoC, Chipyard Rocket, Gemmini"},
            },
        }
    )
    assert prompt_get is not None
    assert "emulator_run_qa" in prompt_get["result"]["messages"][0]["content"]["text"]

    # JSON-RPC error handling
    unknown_method = server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "foo/bar"})
    assert unknown_method is not None and unknown_method["error"]["code"] == -32601
    unknown_tool = server.handle_message(
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "no_such_tool"}}
    )
    assert unknown_tool is not None and unknown_tool["error"]["code"] == -32602


def test_mcp_workspace_sandbox_blocks_path_escape(tmp_path):
    server = EmulatorMcpServer(ROOT, allow_external_paths=False)
    res = server.call_tool(
        "emulator_validate_spec",
        {"spec_path": str(tmp_path / "outside.yaml")},
    )
    assert res["isError"] is True
    assert "E_MCP_SANDBOX" in res["structuredContent"]["message"]


def test_mcp_tools_end_to_end_generation_domains_bsp_noc_and_package(tmp_path):
    server = EmulatorMcpServer(ROOT, allow_external_paths=True)

    # 1. Discover specs and check qualified vs. blocked CPU adapters
    specs = server.call_tool("emulator_list_specs")
    assert specs["isError"] is False
    assert (
        "specs/multiplatform/design1_rocket_chipyard_ot_qemu.yaml"
        in (specs["structuredContent"]["soc_specs"])
    )

    ok_adapter = server.call_tool(
        "emulator_check_cpu_adapter",
        {
            "provider": "chipyard",
            "model": "chipyard.rocket_axi32",
            "protocol": "axi4-32-pa32",
            "engine": "qemu",
        },
    )
    assert ok_adapter["isError"] is False
    assert ok_adapter["structuredContent"]["status"] == "QUALIFIED_ADAPTER"

    blocked_adapter = server.call_tool(
        "emulator_check_cpu_adapter",
        {
            "provider": "opentitan",
            "model": "opentitan.earlgrey_full",
            "protocol": "tilelink-ul-32",
            "engine": "qemu",
        },
    )
    assert blocked_adapter["isError"] is True
    assert "E_CPU_ADAPTER" in blocked_adapter["structuredContent"]["message"]

    # 2. Validate spec inline & from file
    spec_text = (ROOT / "specs/multiplatform/design2_gem5_noc_smp_hetero.yaml").read_text()
    val_inline = server.call_tool("emulator_validate_spec", {"spec_yaml": spec_text})
    assert val_inline["isError"] is False
    assert val_inline["structuredContent"]["name"] == "MP_Design2_Gem5NoC_SMP"

    # 3. Generate dual-engine SoC -> derive domains -> synthesize BSP -> derive NoC topology
    gen_dir = tmp_path / "gen-design2"
    gen = server.call_tool(
        "emulator_generate_soc",
        {
            "spec_path": "specs/multiplatform/design2_gem5_noc_smp_hetero.yaml",
            "out_dir": str(gen_dir),
            "engine": "both",
        },
    )
    assert gen["isError"] is False
    ir_path = gen["structuredContent"]["ir_path"]

    dom = server.call_tool("emulator_derive_domains", {"ir_path": ir_path})
    assert dom["isError"] is False
    assert len(dom["structuredContent"]["bindings"]) == 16

    bsp = server.call_tool(
        "emulator_synthesize_bsp",
        {"ir_path": ir_path, "out_dir": str(tmp_path / "bsp-design2")},
    )
    assert bsp["isError"] is False
    assert bsp["structuredContent"]["contract"] == "native-bsp-bundle-1"

    noc = server.call_tool(
        "emulator_derive_noc_topology",
        {
            "ir_path": ir_path,
            "rows": 2,
            "cols": 2,
            "pio_bypass_disclosed": True,
            "calibration": {
                "roi_name": "mcp_smp_roi",
                "reference_source": "native-gem5",
                "model_source": "qemu-analytical-estimate",
                "reference_cycles": 50000,
                "estimated_cycles": 52000,
                "max_relative_error": 0.10,
            },
        },
    )
    assert noc["isError"] is False
    assert noc["structuredContent"]["noc"]["routers"] == 4
    assert noc["structuredContent"]["calibration"]["within_budget"] is True

    # 4. Preflight flow & inspect SystemC MAC NPU package
    pf = server.call_tool(
        "emulator_preflight_flow",
        {"flow_path": "specs/multiplatform/design3_flow.yaml"},
    )
    assert pf["isError"] is False
    assert pf["structuredContent"]["status"] == "READY"

    pkg = server.call_tool(
        "emulator_package_inspect",
        {"package_path": "specs/multiplatform/packages/mac_npu/package.yaml"},
    )
    assert pkg["isError"] is False
    assert pkg["structuredContent"]["capabilities"]["package_id"] == "user.mac_npu"
    assert "USP_USER_MAC_NPU_REG_ACC_RESULT_OFFSET" in pkg["structuredContent"]["c_header"]


def test_mcp_content_length_framing_and_parse_error():
    server = EmulatorMcpServer(ROOT)
    req = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode("utf-8")
    framed_input = f"Content-Length: {len(req)}\r\n\r\n".encode("ascii") + req + b"not-valid-json\n"
    in_buf = io.BufferedReader(io.BytesIO(framed_input))
    raw_out = io.BytesIO()
    out_buf = io.BufferedWriter(raw_out)
    assert serve_stdio(server, in_stream=in_buf, out_stream=out_buf) == 0
    data = raw_out.getvalue()
    assert b"Content-Length:" in data
    assert b'"id":99' in data
    assert b"-32700" in data


def test_mcp_live_stdio_subprocess_and_real_systemc_when_available(tmp_path):
    """Spawn `usp mcp serve` as a real stdio subprocess and drive it over JSON-RPC 2.0."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "socsim.cli",
            "mcp",
            "serve",
            "--root",
            str(ROOT),
            "--allow-external-paths",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _rpc_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": DEFAULT_PROTOCOL_VERSION},
            },
        )
        init = _rpc_recv(proc)
        assert init["result"]["serverInfo"]["name"] == SERVER_NAME

        _rpc_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        _rpc_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "emulator_doctor", "arguments": {}},
            },
        )
        doc = _rpc_recv(proc)
        assert doc["result"]["isError"] is False
        assert "tools" in doc["result"]["structuredContent"]

        # Run full multiplatform QA suite via MCP over stdio!
        _rpc_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "emulator_run_qa",
                    "arguments": {"suite": "multiplatform", "out_dir": str(tmp_path / "mcp-qa")},
                },
            },
        )
        qa = _rpc_recv(proc)
        assert qa["result"]["isError"] is False
        assert qa["result"]["structuredContent"]["status"] == "PASSED"

        # Real SystemC package build via MCP when libsystemc-dev is available (e.g. Podman SDK)
        _rpc_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "emulator_package_build",
                    "arguments": {
                        "package_path": "specs/multiplatform/packages/mac_npu/package.yaml",
                        "out_dir": str(tmp_path / "mcp-mac-npu"),
                    },
                },
            },
        )
        pkg_build = _rpc_recv(proc)
        assert pkg_build["result"]["isError"] is False
        payload = pkg_build["result"]["structuredContent"]
        if _has_systemc_toolchain():
            assert payload["status"] == "PASSED"
            assert payload["conformance_vectors_passed"] == 17
        else:
            if os.environ.get("SOCSIM_REQUIRE_INTEGRATION") == "1":
                pytest.fail("SOCSIM_REQUIRE_INTEGRATION=1 requires real SystemC build via MCP")
            assert payload["status"] == "BLOCKED"
    finally:
        if proc.stdin:
            proc.stdin.close()
        rc = proc.wait(timeout=15)
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        assert rc == 0, stderr


def test_mcp_cli_tools_and_client_configs():
    for client in ("claude", "cursor", "vscode", "podman"):
        cfg = render_client_config(client, ROOT)
        if client == "vscode":
            assert "usp-emulator" in cfg["servers"]
        else:
            assert "usp-emulator" in cfg["mcpServers"]

    proc = subprocess.run(
        [sys.executable, "-m", "socsim.cli", "mcp", "tools", "--root", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["server"] == SERVER_NAME
    assert len(data["tools"]) == 14
    assert len(data["resources"]) == 5
    assert len(data["prompts"]) == 3
