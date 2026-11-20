# USP HW/SW Emulator MCP Server (`usp mcp`)

The USP/SocSim emulator ships a zero-dependency **Model Context Protocol (MCP)** JSON-RPC 2.0
server over standard `stdio` transport (`src/socsim/mcp_server.py`) so AI coding agents
(Claude Desktop, Cursor, VS Code Copilot, Antigravity, Codex CLI, or custom LLM orchestrators)
can inspect, design, validate, generate, compile, and run HW/SW SoC emulations directly.

## Key Properties

- **Standards-compliant stdio JSON-RPC 2.0**: Supports MCP `2025-03-26` and `2024-11-05`
  (both newline-delimited JSON and `Content-Length` framing), keeping `stdout` strictly pure
  for JSON-RPC frames and redirecting tool diagnostics to `stderr`.
- **Workspace sandboxing**: By default all file paths are confined inside `--root`
  (`E_MCP_SANDBOX` blocks directory traversal unless `--allow-external-paths` is explicitly set).
- **Honest hardware contracts**: Missing local simulators (`qemu-system-riscv64`,
  `libsystemc-dev`, `verilator`) return explicit `BLOCKED` responses rather than synthetic
  or silent ISS fallbacks.
- **Rootless Podman SDK integration**: `bash scripts/podman.sh mcp` exposes the full
  QEMU + RISC-V GCC + SystemC 3.0.1 + Verilator container directly over `stdio` (`podman run -i`
  without a PTY).

## Exposed Capabilities

### Tools (14)

| Tool | Purpose |
|---|---|
| `emulator_doctor` | Probe QEMU, RISC-V GCC, SystemC, Verilator, Yosys, and Podman availability |
| `emulator_list_specs` | Discover SoC YAML specs, flows, and SystemC packages in the workspace |
| `emulator_list_models` | List qualified native models, CPU/bus adapters, and 33-entry IP readiness catalog |
| `emulator_check_cpu_adapter` | Verify `(provider, model, protocol, engine)` qualification (no ISS fallback) |
| `emulator_validate_spec` | Validate a native SoC spec from workspace path or inline YAML |
| `emulator_generate_soc` | Generate IP-XACT 2022 XML, Native IR, DTS, headers, and QEMU/gem5 targets |
| `emulator_preflight_flow` | Run ownership, domain, and workload preflight on a `native-flow-1` file |
| `emulator_derive_domains` | Derive clock/reset domains and initiator address-space views from `native-ir.json` |
| `emulator_synthesize_bsp` | Generate deterministic `soc.h`, `soc.ld`, and BSP manifest from `native-ir.json` |
| `emulator_derive_noc_topology` | Derive IR-bound gem5 Ruby/Garnet mesh NoC topology + optional ROI calibration |
| `emulator_package_inspect` | Verify SHA-256 hashes, inspect capabilities, and generate C headers for `package.yaml` |
| `emulator_package_build` | Compile SystemC/C++17 model and execute all `usp-mmio32-v1` conformance vectors |
| `emulator_run_qa` | Run `quick`, `unseen`, `multiplatform`, `wheel`, or `full` QA and verify scorecard |
| `emulator_run_qemu` | Boot an RV64 ELF firmware on QEMU and return console output + artifact hashes |

### Resources (5) & Prompts (3)

- Resources: `usp://doctor/environment`, `usp://catalog/models`, `usp://catalog/ip-readiness`,
  `usp://specs/list`, `usp://milestones/status`.
- Prompts: `design_and_validate_soc`, `audit_multiplatform_architecture`,
  `package_and_test_systemc_ip`.

## Connecting AI Clients

Generate a ready-to-paste client config for Claude Desktop, Cursor, VS Code, or Rootless Podman:

```bash
# Local Python / virtualenv install
usp mcp config --client claude
usp mcp config --client cursor
usp mcp config --client vscode

# Rootless Podman SDK (includes QEMU + RISC-V GCC + SystemC 3.0.1 + Verilator)
usp mcp config --client podman
```

Example `mcpServers` entry using the Rootless Podman SDK:

```json
{
  "mcpServers": {
    "usp-emulator": {
      "command": "bash",
      "args": ["/path/to/IP-XACT/scripts/podman.sh", "mcp"]
    }
  }
}
```
