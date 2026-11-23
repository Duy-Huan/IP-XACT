"""Enterprise IP Plugin Scaffolder (E11).

Generates a complete, SHA-256-pinned `native-model-package-1` IP plugin directory ready for
`usp registry publish` and `usp package build`, containing:
  - IEEE 1685-2022 IP-XACT component XML (`ipxact/<name>.xml`)
  - Synthesizable SystemVerilog RTL template (`rtl/<name>_rtl.sv`)
  - SystemC/TLM-2.0 `usp-mmio32-v1` model (`tlm-<name>.cpp`)
  - Bare-metal RV64 acceptance driver (`selftest.c`)
  - Apache-2.0 LICENSE and SHA-256-verified `package.yaml`
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .domain import ContractError
from .ipxact import digest
from .native_package import read_package

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
VENDOR_RE = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")


def scaffold_ip_package(
    *,
    name: str,
    out_dir: Path,
    vendor: str = "enterprise.soc",
    version: str = "1.0",
) -> dict[str, Any]:
    """Scaffold a complete, hash-pinned IP plugin package ready for registry publishing."""
    if not NAME_RE.fullmatch(name):
        raise ContractError(f"E_SCAFFOLD_NAME invalid IP identifier: {name}")
    if not VENDOR_RE.fullmatch(vendor):
        raise ContractError(f"E_SCAFFOLD_VENDOR invalid vendor identifier: {vendor}")

    out_dir = out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ContractError(f"E_SCAFFOLD_OUT output directory must be empty or fresh: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rtl").mkdir(exist_ok=True)
    (out_dir / "ipxact").mkdir(exist_ok=True)

    upper = name.upper()
    cls_name = "".join(part.capitalize() for part in name.split("_"))
    pkg_id = f"user.{name}"
    vlnv = f"{vendor}:peripheral:{name}:{version}"

    license_path = out_dir / "LICENSE"
    license_path.write_text(
        "Apache License\nVersion 2.0, January 2004\n"
        "http://www.apache.org/licenses/\n"
        f"SPDX-License-Identifier: Apache-2.0\nGenerated IP plugin: {pkg_id}\n"
    )

    tlm_filename = f"tlm-{name}.cpp"
    tlm_path = out_dir / tlm_filename
    tlm_path.write_text(
        f"""// SPDX-License-Identifier: Apache-2.0
// Scaffolded SystemC/TLM-2.0 MMIO32 IP plugin ({pkg_id}).
#include <systemc>
#include <tlm>
#include <tlm_utils/simple_initiator_socket.h>
#include <tlm_utils/simple_target_socket.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <endian.h>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fstream>

struct {cls_name}Model : sc_core::sc_module {{
    tlm_utils::simple_target_socket<{cls_name}Model> target;
    uint32_t ctrl = 0, data_in = 0, data_out = 0, irq = 0;

    explicit {cls_name}Model(sc_core::sc_module_name nm) : sc_module(nm), target("target") {{
        target.register_b_transport(this, &{cls_name}Model::transport);
    }}

    void reset_state() {{ ctrl = 0; data_in = 0; data_out = 0; irq = 0; }}

    void transport(tlm::tlm_generic_payload &tx, sc_core::sc_time &delay) {{
        if (tx.get_data_length() != 4) {{
            tx.set_response_status(tlm::TLM_BURST_ERROR_RESPONSE);
            return;
        }}
        uint32_t val = 0;
        std::memcpy(&val, tx.get_data_ptr(), 4);
        const uint64_t off = tx.get_address();
        if (tx.is_write()) {{
            switch (off) {{
                case 0x00:
                    if (val == 1u) {{ data_out = data_in ^ 0xA5A55A5Au; ctrl++; irq = 1u; }}
                    else if (val == 2u) {{ reset_state(); }}
                    else {{ tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return; }}
                    break;
                case 0x04: data_in = val; break;
                case 0x08: tx.set_response_status(tlm::TLM_COMMAND_ERROR_RESPONSE); return;
                case 0x0C: if (val & 1u) irq = 0u; break;
                default: tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
            }}
        }} else {{
            switch (off) {{
                case 0x00: val = ctrl; break;
                case 0x04: val = data_in; break;
                case 0x08: val = data_out; break;
                case 0x0C: val = irq; break;
                default: tx.set_response_status(tlm::TLM_ADDRESS_ERROR_RESPONSE); return;
            }}
            std::memcpy(tx.get_data_ptr(), &val, 4);
        }}
        delay += sc_core::sc_time(10, sc_core::SC_NS);
        tx.set_response_status(tlm::TLM_OK_RESPONSE);
    }}
}};

struct Initiator : sc_core::sc_module {{
    tlm_utils::simple_initiator_socket<Initiator> socket;
    explicit Initiator(sc_core::sc_module_name nm) : sc_module(nm), socket("socket") {{}}
}};

static bool transfer(int fd, void *buf, bool writing) {{
    auto *p = static_cast<char *>(buf);
    for (unsigned n = 0; n < 20;) {{
        ssize_t rc = writing ? send(fd, p + n, 20 - n, MSG_NOSIGNAL) : recv(fd, p + n, 20 - n, 0);
        if (rc < 0 && errno == EINTR) continue;
        if (rc <= 0) return false;
        n += static_cast<unsigned>(rc);
    }}
    return true;
}}

int sc_main(int argc, char **argv) {{
    if (argc != 3) return 2;
    sc_core::sc_set_time_resolution(1, sc_core::SC_PS);
    {cls_name}Model model("{name}");
    Initiator host("host");
    host.socket.bind(model.target);
    sc_core::sc_start(sc_core::SC_ZERO_TIME);
    std::ofstream trace(argv[2]);
    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{{}};
    addr.sun_family = AF_UNIX;
    if (std::strlen(argv[1]) >= sizeof(addr.sun_path) || !trace || srv < 0) return 2;
    std::strcpy(addr.sun_path, argv[1]);
    if (bind(srv, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) || listen(srv, 1)) return 2;
    int fd = accept(srv, nullptr, nullptr);
    if (fd < 0) return 2;
    std::array<uint32_t, 5> req{{}}, res{{}};
    while (transfer(fd, req.data(), false)) {{
        for (auto &w : req) w = le32toh(w);
        uint32_t op = req[2], off = req[3], val = req[4], status = 0;
        if (req[0] != 0x31505355u || op > 2u) return 2;
        sc_core::sc_time delay = sc_core::SC_ZERO_TIME;
        if (op == 2u) {{ model.reset_state(); val = 0u; }}
        else {{
            tlm::tlm_generic_payload tx;
            tx.set_command(op ? tlm::TLM_WRITE_COMMAND : tlm::TLM_READ_COMMAND);
            tx.set_address(off);
            tx.set_data_ptr(reinterpret_cast<unsigned char *>(&val));
            tx.set_data_length(4); tx.set_streaming_width(4);
            host.socket->b_transport(tx, delay);
            status = tx.is_response_ok() ? 0u : 1u;
            if (status != 0u) val = 0u;
        }}
        sc_core::sc_start(
            delay == sc_core::SC_ZERO_TIME ? sc_core::sc_time(1, sc_core::SC_NS) : delay);
        res = {{htole32(0x31505355u), htole32(req[1]), htole32(status),
               htole32(val), htole32(model.irq)}};
        if (!transfer(fd, res.data(), true)) return 2;
    }}
    close(fd); close(srv); unlink(argv[1]);
    return 0;
}}
"""
    )

    selftest_path = out_dir / "selftest.c"
    selftest_path.write_text(
        f"""/* SPDX-License-Identifier: Apache-2.0 */
/* Scaffolded bare-metal RV64 acceptance test for {pkg_id}. */
#include <stdint.h>
#include "soc.h"
#include "driver_bindings.h"
#define R32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define R8(a)  (*(volatile uint8_t  *)(uintptr_t)(a))
volatile unsigned boot_ready;
static void text(const char *s) {{
    while (*s) {{ while (!(R8(USP_CONSOLE_BASE + 5) & 0x20)) {{}} R8(USP_CONSOLE_BASE) = *s++; }}
}}
static void finish(unsigned code) {{
    while (!(R8(USP_CONSOLE_BASE + 5) & 0x40)) {{}}
    R32(USP_FINISHER_BASE) = code;
    for (;;) __asm__ volatile("wfi");
}}
void secondary_main(uintptr_t hart) {{ (void)hart; for (;;) __asm__ volatile("wfi"); }}
int main(void) {{
    R32(usp_driver_bases[0] + 0x04U) = 0x12345678U;
    R32(usp_driver_bases[0] + 0x00U) = 1U;
    if (R32(usp_driver_bases[0] + 0x08U) != (0x12345678U ^ 0xA5A55A5AU)) finish(0x13333U);
    R32(usp_driver_bases[0] + 0x0CU) = 1U;
    text("USP_{upper}_SELFTEST_OK\\n");
    finish(0x5555U);
    return 0;
}}
"""
    )

    rtl_path = out_dir / "rtl" / f"{name}_rtl.sv"
    rtl_path.write_text(
        f"""// SPDX-License-Identifier: Apache-2.0
// Synthesizable SystemVerilog RTL template for {pkg_id}.
module {name}_rtl (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_valid_i,
    input  logic        req_write_i,
    input  logic [7:0]  req_addr_i,
    input  logic [31:0] req_wdata_i,
    output logic [31:0] rsp_rdata_o,
    output logic        rsp_err_o,
    output logic        irq_o
);
    logic [31:0] ctrl_q, data_in_q, data_out_q;
    logic        irq_q;
    assign irq_o = irq_q;

    always_comb begin
        rsp_rdata_o = 32'd0;
        rsp_err_o   = 1'b0;
        if (req_valid_i && !req_write_i) begin
            case (req_addr_i)
                8'h00: rsp_rdata_o = ctrl_q;
                8'h04: rsp_rdata_o = data_in_q;
                8'h08: rsp_rdata_o = data_out_q;
                8'h0C: rsp_rdata_o = {{31'd0, irq_q}};
                default: rsp_err_o = 1'b1;
            endcase
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            ctrl_q <= 32'd0; data_in_q <= 32'd0; data_out_q <= 32'd0; irq_q <= 1'b0;
        end else if (req_valid_i && req_write_i) begin
            case (req_addr_i)
                8'h00: if (req_wdata_i == 32'd1) begin
                    data_out_q <= data_in_q ^ 32'hA5A5_5A5A;
                    ctrl_q     <= ctrl_q + 32'd1;
                    irq_q      <= 1'b1;
                end
                8'h04: data_in_q <= req_wdata_i;
                8'h0C: if (req_wdata_i[0]) irq_q <= 1'b0;
                default: ;
            endcase
        end
    end
endmodule
"""
    )

    ipxact_path = out_dir / "ipxact" / f"{name}.xml"
    ipxact_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<ipxact:component xmlns:ipxact="http://www.accellera.org/XMLSchema/IPXACT/1685-2022">
  <ipxact:vendor>{vendor}</ipxact:vendor>
  <ipxact:library>peripheral</ipxact:library>
  <ipxact:name>{name}</ipxact:name>
  <ipxact:version>{version}</ipxact:version>
  <ipxact:memoryMaps>
    <ipxact:memoryMap>
      <ipxact:name>mmio_regs</ipxact:name>
      <ipxact:addressBlock>
        <ipxact:name>regs</ipxact:name>
        <ipxact:baseAddress>0x0</ipxact:baseAddress>
        <ipxact:range>0x1000</ipxact:range>
        <ipxact:width>32</ipxact:width>
        <ipxact:register><ipxact:name>ctrl</ipxact:name><ipxact:addressOffset>0x0</ipxact:addressOffset><ipxact:size>32</ipxact:size></ipxact:register>
        <ipxact:register><ipxact:name>data_in</ipxact:name><ipxact:addressOffset>0x4</ipxact:addressOffset><ipxact:size>32</ipxact:size></ipxact:register>
        <ipxact:register><ipxact:name>data_out</ipxact:name><ipxact:addressOffset>0x8</ipxact:addressOffset><ipxact:size>32</ipxact:size></ipxact:register>
        <ipxact:register><ipxact:name>irq_status</ipxact:name><ipxact:addressOffset>0xC</ipxact:addressOffset><ipxact:size>32</ipxact:size></ipxact:register>
      </ipxact:addressBlock>
    </ipxact:memoryMap>
  </ipxact:memoryMaps>
</ipxact:component>
"""
    )

    package_doc: dict[str, Any] = {
        "schema_version": "native-model-package-1",
        "id": pkg_id,
        "vlnv": vlnv,
        "license": {
            "spdx": "Apache-2.0",
            "file": "LICENSE",
            "sha256": digest(license_path),
        },
        "sources": {tlm_filename: digest(tlm_path)},
        "build": {"language": "systemc-c++17", "entry": tlm_filename},
        "interface": {
            "protocol": "usp-mmio32-v1",
            "access_bits": 32,
            "aperture_bytes": 4096,
            "endianness": "little",
            "irq_mask": 1,
            "irq_trigger": "level-high",
            "state_owner": "peripheral-process",
            "time": "annotated-systemc",
        },
        "registers": [
            {"name": "ctrl", "offset": 0, "access": "read-write", "reset": 0},
            {"name": "data_in", "offset": 4, "access": "read-write", "reset": 0},
            {"name": "data_out", "offset": 8, "access": "read-only", "reset": 0},
            {"name": "irq_status", "offset": 12, "access": "write-one-clear", "reset": 0},
        ],
        "driver": {
            "source": "selftest.c",
            "sha256": digest(selftest_path),
            "expected_markers": [f"USP_{upper}_SELFTEST_OK"],
        },
        "conformance": [
            {"op": 2, "offset": 0, "data": 0, "value": 0, "status": 0, "irq": 0},
            {"op": 1, "offset": 4, "data": 305419896, "value": 305419896, "status": 0, "irq": 0},
            {"op": 1, "offset": 0, "data": 1, "value": 1, "status": 0, "irq": 1},
            {"op": 0, "offset": 8, "data": 0, "value": 3079736354, "status": 0, "irq": 1},
            {"op": 1, "offset": 12, "data": 1, "value": 1, "status": 0, "irq": 0},
            {"op": 1, "offset": 8, "data": 99, "value": 0, "status": 1, "irq": 0},
        ],
    }
    pkg_yaml = out_dir / "package.yaml"
    pkg_yaml.write_text(yaml.safe_dump(package_doc, sort_keys=False))

    # Self-verify immediately against the strict native-model-package-1 loader
    read_package(pkg_yaml)

    return {
        "contract": "usp-ip-scaffold-1",
        "status": "PASSED",
        "package_id": pkg_id,
        "vlnv": vlnv,
        "package_yaml": str(pkg_yaml),
        "ipxact_xml": str(ipxact_path),
        "rtl_sv": str(rtl_path),
        "tlm_cpp": str(tlm_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usp ip")
    commands = parser.add_subparsers(dest="command", required=True)
    sc = commands.add_parser(
        "scaffold", help="scaffold an IEEE 1685-2022 + SystemC + SV RTL IP plugin package"
    )
    sc.add_argument("name", help="lowercase IP name (e.g. neural_dma)")
    sc.add_argument("--out", type=Path, required=True)
    sc.add_argument("--vendor", default="enterprise.soc")
    sc.add_argument("--version", default="1.0")
    args = parser.parse_args(argv)
    try:
        res = scaffold_ip_package(
            name=args.name, out_dir=args.out, vendor=args.vendor, version=args.version
        )
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
