# USP HW/SW Emulator MCP Server (`usp mcp`)

| Metadata Field | Value |
|---|---|
| **Document ID** | `SOCSIM-MCP-001` |
| **Current Version** | `0.3.0a5` (20 Tools · 5 Resources · 3 Prompts) |
| **Status** | Active / CI-Verified (`tests/test_mcp_server.py` + `containers/smoke.sh`) |
| **Normative Authority** | `src/socsim/mcp_server.py`, `tests/test_mcp_server.py`, `scripts/podman.sh` |

---

## Document Revision History

| Version | Release Date | Milestone / Gate | Author / Scope | Detailed Summary of Changes |
|---|---|---|---|---|
| `0.3.0a5` (Initial) | `2026-11-20` | MCP Baseline | AI Tool Control | Launched zero-dependency MCP JSON-RPC 2.0 `stdio` server with 14 tools, 5 resources, 3 prompts, workspace sandboxing, and Podman stdio bridge. |
| `0.3.0a5-E4–E12` | `2026-11-23` | `PR #1–#3` (`911b450`) | Enterprise Expansion | Expanded MCP server to **20 tools** adding unified SystemC/RTL execution, IP registry inspection, parallel DSE sweeps, CHIA cyclic loop, IP scaffolder, and HTML5 dashboard generation. |

---

## 1. Architecture & Protocol Guarantees

The USP/SocSim emulator ships a zero-dependency **Model Context Protocol (MCP)** JSON-RPC 2.0 server over standard `stdio` (`src/socsim/mcp_server.py`) enabling AI coding agents (Claude Desktop, Cursor, VS Code Copilot, Antigravity, Codex CLI, or automated verification orchestrators) to inspect, validate, generate, compile, simulate, and optimize RISC-V SoCs directly.

- **Pure `stdio` JSON-RPC 2.0 Framing**: Supports MCP protocol versions `2025-03-26` and `2024-11-05` (both newline-delimited JSON and `Content-Length` framing). `stdout` carries strictly framed JSON-RPC messages; all tool diagnostics route to `stderr`.
- **Workspace Sandboxing (`E_MCP_SANDBOX`)**: Every path argument is resolved and verified to remain inside `--root` unless `--allow-external-paths` is explicitly provided. Directory traversal (`..`) and symlink escapes are rejected before tool execution.
- **Fail-Closed Hardware Contracts**: Missing native simulators (`qemu-system-riscv64`, `libsystemc-dev`, `verilator`) return structured `BLOCKED` responses rather than synthetic outputs or silent ISS fallbacks.
- **Rootless Podman SDK Bridge**: `bash scripts/podman.sh mcp` runs `podman run -i` (no PTY) so AI clients gain full access to QEMU, RISC-V GCC, SystemC 3.0.1, Verilator 5.032, and Yosys 0.52 inside the container over pure `stdio`.

---

## 2. Complete Capability Reference

### 2.1 Tools (20)

| # | Tool Name | Category | Purpose |
|---:|---|---|---|
| 1 | `emulator_doctor` | Environment | Probe QEMU, RISC-V GCC, SystemC, Verilator, Yosys, and Podman availability (read-only) |
| 2 | `emulator_list_specs` | Discovery | List SoC YAML specs, `native-flow-1` manifests, and SystemC model packages in workspace |
| 3 | `emulator_list_models` | Discovery | List qualified native models, CPU/bus adapters, and 33-entry pinned IP readiness catalog |
| 4 | `emulator_check_cpu_adapter` | Qualification | Verify `(provider, model, protocol, engine)` qualification (strict no-ISS-fallback check) |
| 5 | `emulator_validate_spec` | Compiler | Validate native SoC YAML from file path or inline string (address map, IRQs, boot entry) |
| 6 | `emulator_generate_soc` | Compiler | Generate IP-XACT 2022 XML, Native IR, DTS, headers, and QEMU/gem5 build targets |
| 7 | `emulator_preflight_flow` | Flow | Run ownership, clock/reset domain, and workload preflight on a `native-flow-1` YAML |
| 8 | `emulator_derive_domains` | IR Analysis | Derive explicit clock/reset domains and per-initiator address views from `native-ir.json` |
| 9 | `emulator_synthesize_bsp` | Software | Synthesize deterministic `soc.h`, `soc.ld`, and BSP manifest from `native-ir.json` |
| 10 | `emulator_derive_noc_topology` | NoC | Derive IR-bound gem5 Ruby/Garnet mesh topology with PIO bypass disclosure & ROI calibration |
| 11 | `emulator_package_inspect` | IP Model | Verify SHA-256 hashes, inspect MMIO/IRQ/reset/error contracts, and synthesize C register header |
| 12 | `emulator_package_build` | IP Model | Compile SystemC/C++17 `native-model-package-1` and execute `usp-mmio32-v1` conformance vectors |
| 13 | `emulator_run_qa` | QA/QC | Run `quick`, `unseen`, `multiplatform`, `wheel`, or `full` QA suite & verify SHA-256 scorecard |
| 14 | `emulator_run_qemu` | Simulation | Boot an RV64 ELF firmware on QEMU for a validated IP-XACT project and return console + hashes |
| 15 | `emulator_run_unified_soc` | Unified SoC | Compile & run the Dual-Hart SMP + 6× Verilator RTL + IR 2×2 NoC unified SystemC SoC with RV64 SW |
| 16 | `emulator_registry_list` | Registry | List versioned, SHA-256-locked IP packages in a shared IP plugin registry |
| 17 | `emulator_run_dse_sweep` | DSE | Run CHIA-style parallel DSE (`thread`/`process`/`ray`) & RTL-vs-TLM calibration with `ArtifactCache` |
| 18 | `emulator_run_chia_loop` | Optimization | Run CHIA-style cyclic HW/SW NoC placement/latency optimization loop under isolated verification oracle |
| 19 | `emulator_scaffold_ip` | Authoring | Scaffold a hash-pinned IP plugin (IP-XACT 2022 XML, SV RTL, SystemC TLM-2.0, `selftest.c`, `package.yaml`) |
| 20 | `emulator_generate_dashboard` | Reporting | Render zero-CDN HTML5 Verification, Coverage, Calibration, DSE & CHIA Convergence Dashboard |

### 2.2 Resources (5) & Prompts (3)

- **Resources:** `usp://doctor/environment`, `usp://catalog/models`, `usp://catalog/ip-readiness`, `usp://specs/list`, `usp://milestones/status`.
- **Prompts:** `design_and_validate_soc`, `audit_multiplatform_architecture`, `package_and_test_systemc_ip`.

---

## 3. Client Integration & Configuration

Generate ready-to-paste client JSON configs directly from the CLI:

```bash
# Inspect all 20 tools + JSON schemas locally
usp mcp tools --root .

# Generate client configuration for local Python .venv
usp mcp config --client claude
usp mcp config --client cursor
usp mcp config --client vscode

# Generate client configuration backed by Rootless Podman SDK (recommended for full RTL/SystemC)
usp mcp config --client podman --root /path/to/IP-XACT
```

Example `mcpServers` block using Rootless Podman SDK:

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

Or via Podman Compose ([`containers/compose.yaml`](../containers/compose.yaml)):

```bash
podman compose -f containers/compose.yaml run --rm -T mcp
```
