"""Automatic SystemVerilog Port Introspector & Pin-Level Bus Adapter Codegen.

Directly inspects synthesizable SystemVerilog RTL modules emitted by Chipyard
(`firtool`/Chisel -> SV), OpenTitan (TL-UL), or standard AMBA AXI4 / AXI4-Lite IP
generators and emits cycle-accurate C++ `PinTraits` bindings for:
  - `verilator-axi4-full`  (`VerilatorAxi4FullAdapter`: 32/64-bit AMBA AXI4 INCR bursts)
  - `verilator-axi4-lite`  (`VerilatorAxi4LiteAdapter`: 5-channel `aw/w/b/ar/r` valid-ready)
  - `verilator-tl-ul`      (`VerilatorTlulAdapter`: OpenTitan `a_valid/a_ready/d_valid/d_ready`)
  - `verilator-mmio32`     (`VerilatorMmioWrapper`: legacy simple MMIO32 ports)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .domain import ContractError

SUPPORTED_RTL_PROTOCOLS = frozenset({"auto", "mmio32", "axi4-lite", "axi4", "tl-ul"})

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_PORT_DIR_RE = re.compile(r"\b(input|output|inout)\b")
_RANGE_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")


def _strip_sv_comments(text: str) -> str:
    text = _BLOCK_COMMENT_RE.sub(" ", text)
    return _LINE_COMMENT_RE.sub(" ", text)


def _extract_balanced(text: str, open_idx: int) -> tuple[str, int]:
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i + 1
    raise ContractError("E_COMPOSE_RTL_PARSE unbalanced parentheses in SystemVerilog module header")


def parse_sv_module_ports(sv_path: Path, module_name: str) -> dict[str, dict[str, Any]]:
    """Extract `{port_name: {'direction': ..., 'width': int}}` from a SystemVerilog module."""
    raw = _strip_sv_comments(sv_path.read_text(encoding="utf-8"))
    mod_pat = re.compile(rf"\bmodule\s+{re.escape(module_name)}\b")
    m = mod_pat.search(raw)
    if not m:
        raise ContractError(
            f"E_COMPOSE_RTL_PARSE module `{module_name}` not found in {sv_path.name}"
        )
    idx = m.end()
    n = len(raw)
    while idx < n and raw[idx].isspace():
        idx += 1
    if idx < n and raw[idx] == "#":
        idx += 1
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx >= n or raw[idx] != "(":
            raise ContractError(f"E_COMPOSE_RTL_PARSE malformed parameter block in {module_name}")
        _, idx = _extract_balanced(raw, idx)
        while idx < n and raw[idx].isspace():
            idx += 1
    if idx >= n or raw[idx] != "(":
        raise ContractError(f"E_COMPOSE_RTL_PARSE missing ANSI port list in {module_name}")
    port_blob, _ = _extract_balanced(raw, idx)

    ports: dict[str, dict[str, Any]] = {}
    cur_dir = ""
    cur_width = 1
    for item in port_blob.split(","):
        tok = item.strip()
        if not tok:
            continue
        dm = _PORT_DIR_RE.search(tok)
        if dm:
            cur_dir = dm.group(1)
            tok = tok[dm.end() :].strip()
            rm = _RANGE_RE.search(tok)
            if rm:
                hi, lo = int(rm.group(1)), int(rm.group(2))
                cur_width = abs(hi - lo) + 1
                tok = tok[rm.end() :].strip()
            else:
                cur_width = 1
        else:
            rm = _RANGE_RE.search(tok)
            if rm:
                hi, lo = int(rm.group(1)), int(rm.group(2))
                cur_width = abs(hi - lo) + 1
                tok = tok[rm.end() :].strip()
        # Last identifier token is the port name (ignoring wire/logic/reg/signed/type qualifiers)
        tok = re.sub(r"\s*=.*$", "", tok).strip()
        parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", tok)
        if not parts or not cur_dir:
            continue
        pname = parts[-1]
        ports[pname] = {"direction": cur_dir, "width": cur_width}
    if not ports:
        raise ContractError(f"E_COMPOSE_RTL_PARSE no ports extracted for module `{module_name}`")
    return ports


def _find_port(
    ports: dict[str, dict[str, Any]],
    patterns: tuple[str, ...],
    *,
    direction: str | None = None,
    overrides: dict[str, str] | None = None,
    canonical: str = "",
) -> str | None:
    if overrides and canonical in overrides:
        ov = overrides[canonical]
        if ov not in ports:
            raise ContractError(
                f"E_COMPOSE_RTL_PROTO port override `{canonical}={ov}` not found in module ports"
            )
        return ov
    for pat in patterns:
        rx = re.compile(pat, re.IGNORECASE)
        for name, info in ports.items():
            if direction and info["direction"] != direction:
                continue
            if rx.fullmatch(name):
                return name
    return None


def _match_mmio32(
    ports: dict[str, dict[str, Any]], overrides: dict[str, str]
) -> dict[str, str] | None:
    req = {
        "clk": (r"clk_i", r"clock", r"clk"),
        "rst": (r"rst_ni", r"rst_n", r"resetn", r"reset"),
        "req_valid": (r"req_valid_i",),
        "req_write": (r"req_write_i",),
        "req_addr": (r"req_addr_i",),
        "req_wdata": (r"req_wdata_i",),
        "rsp_rdata": (r"rsp_rdata_o",),
        "rsp_err": (r"rsp_err_o",),
    }
    found: dict[str, str] = {}
    for k, pats in req.items():
        p = _find_port(ports, pats, overrides=overrides, canonical=k)
        if p is None:
            return None
        found[k] = p
    for k, pats in (("irq", (r"irq_o", r"io_irq", r"intr_o")), ("rtl_cycles", (r"rtl_cycles_o",))):
        p = _find_port(ports, pats, direction="output", overrides=overrides, canonical=k)
        if p is not None:
            found[k] = p
    return found


def _match_axi4(
    ports: dict[str, dict[str, Any]], overrides: dict[str, str]
) -> tuple[str, dict[str, str]] | None:
    """Detect AXI4 Full or AXI4-Lite across Chipyard `firtool`, AMBA `s_axi_*`, and flat naming."""
    req_specs: dict[str, tuple[str, tuple[str, ...]]] = {
        "clk": ("input", (r"aclk", r"clk_i", r"clock", r"clk", r".*clk.*")),
        "rst": ("input", (r"aresetn", r"rst_ni", r"rst_n", r"resetn", r"reset", r"rst_i", r"rst")),
        "aw_valid": ("input", (r"(?:.*_)?aw_?valid(?:_i)?",)),
        "aw_ready": ("output", (r"(?:.*_)?aw_?ready(?:_o)?",)),
        "aw_addr": ("input", (r"(?:.*_)?aw_(?:bits_)?addr(?:_i)?", r"(?:.*_)?awaddr(?:_i)?")),
        "w_valid": ("input", (r"(?:.*_)?(?<!a)w_?valid(?:_i)?",)),
        "w_ready": ("output", (r"(?:.*_)?(?<!a)w_?ready(?:_o)?",)),
        "w_data": (
            "input",
            (r"(?:.*_)?(?<!a)w_(?:bits_)?data(?:_i)?", r"(?:.*_)?(?<!a)wdata(?:_i)?"),
        ),
        "w_strb": (
            "input",
            (r"(?:.*_)?(?<!a)w_(?:bits_)?strb(?:_i)?", r"(?:.*_)?(?<!a)wstrb(?:_i)?"),
        ),
        "b_valid": ("output", (r"(?:.*_)?b_?valid(?:_o)?",)),
        "b_ready": ("input", (r"(?:.*_)?b_?ready(?:_i)?",)),
        "b_resp": ("output", (r"(?:.*_)?b_(?:bits_)?resp(?:_o)?", r"(?:.*_)?bresp(?:_o)?")),
        "ar_valid": ("input", (r"(?:.*_)?ar_?valid(?:_i)?",)),
        "ar_ready": ("output", (r"(?:.*_)?ar_?ready(?:_o)?",)),
        "ar_addr": ("input", (r"(?:.*_)?ar_(?:bits_)?addr(?:_i)?", r"(?:.*_)?araddr(?:_i)?")),
        "r_valid": ("output", (r"(?:.*_)?(?<!a)r_?valid(?:_o)?",)),
        "r_ready": ("input", (r"(?:.*_)?(?<!a)r_?ready(?:_i)?",)),
        "r_data": (
            "output",
            (r"(?:.*_)?(?<!a)r_(?:bits_)?data(?:_o)?", r"(?:.*_)?(?<!a)rdata(?:_o)?"),
        ),
        "r_resp": (
            "output",
            (r"(?:.*_)?(?<!a)r_(?:bits_)?resp(?:_o)?", r"(?:.*_)?(?<!a)rresp(?:_o)?"),
        ),
    }
    found: dict[str, str] = {}
    for k, (direction, pats) in req_specs.items():
        p = _find_port(ports, pats, direction=direction, overrides=overrides, canonical=k)
        if p is None:
            return None
        found[k] = p

    opt_specs: dict[str, tuple[str, tuple[str, ...]]] = {
        "aw_prot": ("input", (r"(?:.*_)?aw_(?:bits_)?prot(?:_i)?", r"(?:.*_)?awprot(?:_i)?")),
        "ar_prot": ("input", (r"(?:.*_)?ar_(?:bits_)?prot(?:_i)?", r"(?:.*_)?arprot(?:_i)?")),
        "aw_id": ("input", (r"(?:.*_)?aw_(?:bits_)?id(?:_i)?", r"(?:.*_)?awid(?:_i)?")),
        "aw_len": ("input", (r"(?:.*_)?aw_(?:bits_)?len(?:_i)?", r"(?:.*_)?awlen(?:_i)?")),
        "aw_size": ("input", (r"(?:.*_)?aw_(?:bits_)?size(?:_i)?", r"(?:.*_)?awsize(?:_i)?")),
        "aw_burst": ("input", (r"(?:.*_)?aw_(?:bits_)?burst(?:_i)?", r"(?:.*_)?awburst(?:_i)?")),
        "w_last": (
            "input",
            (r"(?:.*_)?(?<!a)w_(?:bits_)?last(?:_i)?", r"(?:.*_)?(?<!a)wlast(?:_i)?"),
        ),
        "b_id": ("output", (r"(?:.*_)?b_(?:bits_)?id(?:_o)?", r"(?:.*_)?bid(?:_o)?")),
        "ar_id": ("input", (r"(?:.*_)?ar_(?:bits_)?id(?:_i)?", r"(?:.*_)?arid(?:_i)?")),
        "ar_len": ("input", (r"(?:.*_)?ar_(?:bits_)?len(?:_i)?", r"(?:.*_)?arlen(?:_i)?")),
        "ar_size": ("input", (r"(?:.*_)?ar_(?:bits_)?size(?:_i)?", r"(?:.*_)?arsize(?:_i)?")),
        "ar_burst": ("input", (r"(?:.*_)?ar_(?:bits_)?burst(?:_i)?", r"(?:.*_)?arburst(?:_i)?")),
        "r_id": ("output", (r"(?:.*_)?(?<!a)r_(?:bits_)?id(?:_o)?", r"(?:.*_)?(?<!a)rid(?:_o)?")),
        "r_last": (
            "output",
            (r"(?:.*_)?(?<!a)r_(?:bits_)?last(?:_o)?", r"(?:.*_)?(?<!a)rlast(?:_o)?"),
        ),
        "irq": ("output", (r"irq_o", r"io_irq", r"intr_o", r".*(?:irq|intr).*")),
    }
    for k, (direction, pats) in opt_specs.items():
        p = _find_port(ports, pats, direction=direction, overrides=overrides, canonical=k)
        if p is not None:
            found[k] = p

    has_burst = all(k in found for k in ("aw_len", "w_last", "ar_len", "r_last"))
    return ("axi4" if has_burst else "axi4-lite"), found


def _match_tlul(
    ports: dict[str, dict[str, Any]], overrides: dict[str, str]
) -> dict[str, str] | None:
    """Detect OpenTitan TileLink-UL (`a_valid/a_ready/d_valid/d_ready`) pins."""
    req_specs: dict[str, tuple[str, tuple[str, ...]]] = {
        "clk": ("input", (r"clk_i", r"clock", r"clk")),
        "rst": ("input", (r"rst_ni", r"rst_n", r"resetn", r"reset", r"rst_i")),
        "a_valid": ("input", (r"(?:.*_)?a_valid(?:_i)?",)),
        "a_ready": ("output", (r"(?:.*_)?a_ready(?:_o)?",)),
        "a_opcode": ("input", (r"(?:.*_)?a_(?:bits_)?opcode(?:_i)?",)),
        "a_size": ("input", (r"(?:.*_)?a_(?:bits_)?size(?:_i)?",)),
        "a_address": ("input", (r"(?:.*_)?a_(?:bits_)?addr(?:ess)?(?:_i)?",)),
        "a_mask": ("input", (r"(?:.*_)?a_(?:bits_)?mask(?:_i)?",)),
        "a_data": ("input", (r"(?:.*_)?a_(?:bits_)?data(?:_i)?",)),
        "d_valid": ("output", (r"(?:.*_)?d_valid(?:_o)?",)),
        "d_ready": ("input", (r"(?:.*_)?d_ready(?:_i)?",)),
        "d_opcode": ("output", (r"(?:.*_)?d_(?:bits_)?opcode(?:_o)?",)),
        "d_data": ("output", (r"(?:.*_)?d_(?:bits_)?data(?:_o)?",)),
        "d_error": ("output", (r"(?:.*_)?d_(?:bits_)?error(?:_o)?",)),
    }
    found: dict[str, str] = {}
    for k, (direction, pats) in req_specs.items():
        p = _find_port(ports, pats, direction=direction, overrides=overrides, canonical=k)
        if p is None:
            return None
        found[k] = p

    opt_specs: dict[str, tuple[str, tuple[str, ...]]] = {
        "a_param": ("input", (r"(?:.*_)?a_(?:bits_)?param(?:_i)?",)),
        "a_source": ("input", (r"(?:.*_)?a_(?:bits_)?source(?:_i)?",)),
        "d_param": ("output", (r"(?:.*_)?d_(?:bits_)?param(?:_o)?",)),
        "d_size": ("output", (r"(?:.*_)?d_(?:bits_)?size(?:_o)?",)),
        "d_source": ("output", (r"(?:.*_)?d_(?:bits_)?source(?:_o)?",)),
        "irq": ("output", (r"irq_o", r"intr_gpio_o", r"intr_o", r"io_irq", r".*(?:irq|intr).*")),
    }
    for k, (direction, pats) in opt_specs.items():
        p = _find_port(ports, pats, direction=direction, overrides=overrides, canonical=k)
        if p is not None:
            found[k] = p
    return found


def introspect_sv_rtl_interface(
    sv_path: Path,
    module_name: str,
    requested_protocol: str = "auto",
    port_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Auto-detect or validate an RTL module's bus protocol and exact Verilator port mapping."""
    if requested_protocol not in SUPPORTED_RTL_PROTOCOLS:
        raise ContractError(f"E_COMPOSE_RTL_PROTO unsupported rtl_protocol: {requested_protocol}")
    overrides = dict(port_overrides or {})
    ports = parse_sv_module_ports(sv_path, module_name)

    mmio = _match_mmio32(ports, overrides)
    axi = _match_axi4(ports, overrides)
    tlul = _match_tlul(ports, overrides)

    detected_proto: str
    pin_map: dict[str, str]
    if requested_protocol == "auto":
        if mmio is not None:
            detected_proto, pin_map = "mmio32", mmio
        elif axi is not None:
            detected_proto, pin_map = axi
        elif tlul is not None:
            detected_proto, pin_map = "tl-ul", tlul
        else:
            raise ContractError(
                f"E_COMPOSE_RTL_PROTO unable to auto-detect supported bus protocol "
                f"(mmio32/axi4/axi4-lite/tl-ul) on module `{module_name}` in {sv_path.name}"
            )
    elif requested_protocol == "mmio32":
        if mmio is None:
            raise ContractError(f"E_COMPOSE_RTL_PROTO `{module_name}` lacks mmio32 ports")
        detected_proto, pin_map = "mmio32", mmio
    elif requested_protocol in {"axi4", "axi4-lite"}:
        if axi is None:
            raise ContractError(
                f"E_COMPOSE_RTL_PROTO `{module_name}` lacks {requested_protocol} channels"
            )
        if requested_protocol == "axi4" and axi[0] != "axi4":
            raise ContractError(
                f"E_COMPOSE_RTL_PROTO `{module_name}` requested axi4 burst channels "
                "(aw_len/w_last/ar_len/r_last) not present"
            )
        detected_proto, pin_map = requested_protocol, axi[1]
    else:  # tl-ul
        if tlul is None:
            raise ContractError(f"E_COMPOSE_RTL_PROTO `{module_name}` lacks OpenTitan TL-UL ports")
        detected_proto, pin_map = "tl-ul", tlul

    rst_name = pin_map["rst"].lower()
    reset_active_low = rst_name.endswith("n") or rst_name.endswith("_ni") or "resetn" in rst_name

    data_port = pin_map.get("w_data") or pin_map.get("a_data") or pin_map.get("req_wdata") or ""
    data_bits = int(ports[data_port]["width"]) if data_port in ports else 32
    if data_bits not in {32, 64}:
        raise ContractError(
            f"E_COMPOSE_RTL_PROTO `{module_name}` unsupported bus data width: {data_bits} bits"
        )

    id_port = pin_map.get("aw_id") or pin_map.get("a_source") or ""
    id_bits = int(ports[id_port]["width"]) if id_port in ports else 0

    return {
        "module": module_name,
        "protocol": detected_proto,
        "reset_active_low": reset_active_low,
        "data_bits": data_bits,
        "data_bytes": data_bits // 8,
        "id_bits": id_bits,
        "ports": pin_map,
    }


def emit_cpp_pin_traits(struct_name: str, rtl_meta: dict[str, Any]) -> list[str]:
    """Emit a C++17 `PinTraits` struct binding exact Verilator `V<mod>` ports to our adapters."""
    proto = rtl_meta["protocol"]
    p = rtl_meta["ports"]
    rst_low = "true" if rtl_meta["reset_active_low"] else "false"
    data_bytes = int(rtl_meta["data_bytes"])
    id_bits = int(rtl_meta["id_bits"])
    id_mask = (1 << min(id_bits, 16)) - 1 if id_bits > 0 else 0
    irq_expr = f"(d.{p['irq']} != 0)" if "irq" in p else "false"

    def _assign(key: str, expr: str) -> str:
        return f"d.{p[key]} = {expr};" if key in p else ""

    def _cast(key: str, expr: str) -> str:
        if key not in p:
            return ""
        port = p[key]
        return f"d.{port} = static_cast<std::decay_t<decltype(d.{port})>>({expr});"

    def _fn(ret_sig: str, body: str) -> str:
        return f"    template <typename D> static {ret_sig} {{ {body} }}"

    lines = [
        f"struct {struct_name} {{",
        f"    static constexpr bool kResetActiveLow = {rst_low};",
        f"    static constexpr uint32_t kDataBytes = {data_bytes}u;",
        f"    static constexpr bool kHasId = {'true' if id_bits > 0 else 'false'};",
        f"    static constexpr bool kHasSource = {'true' if id_bits > 0 else 'false'};",
        f"    static constexpr uint32_t kIdMask = {id_mask}u;",
        f"    static constexpr uint32_t kSourceMask = {id_mask}u;",
        _fn("void set_clk(D &d, uint8_t v)", f"d.{p['clk']} = v;"),
        _fn("void set_rst(D &d, uint8_t v)", f"d.{p['rst']} = v;"),
        _fn("bool get_irq(const D &d)", f"(void)d; return {irq_expr};"),
    ]

    if proto == "axi4-lite":
        clear_stmts = [
            f"d.{p['aw_valid']} = 0;",
            f"d.{p['aw_addr']} = 0;",
            _assign("aw_prot", "0"),
            f"d.{p['w_valid']} = 0;",
            f"d.{p['w_data']} = 0;",
            f"d.{p['w_strb']} = 0;",
            f"d.{p['b_ready']} = 0;",
            f"d.{p['ar_valid']} = 0;",
            f"d.{p['ar_addr']} = 0;",
            _assign("ar_prot", "0"),
            f"d.{p['r_ready']} = 0;",
        ]
        lines.extend(
            [
                _fn("void clear_inputs(D &d)", " ".join(s for s in clear_stmts if s)),
                _fn(
                    "void set_aw(D &d, uint8_t v, uint64_t addr, uint8_t prot)",
                    f"(void)prot; d.{p['aw_valid']} = v; {_cast('aw_addr', 'addr')} "
                    f"{_assign('aw_prot', 'prot')}",
                ),
                _fn(
                    "void set_w(D &d, uint8_t v, uint64_t data, uint32_t strb)",
                    f"d.{p['w_valid']} = v; {_cast('w_data', 'data')} {_cast('w_strb', 'strb')}",
                ),
                _fn("void set_bready(D &d, uint8_t v)", f"d.{p['b_ready']} = v;"),
                _fn(
                    "void set_ar(D &d, uint8_t v, uint64_t addr, uint8_t prot)",
                    f"(void)prot; d.{p['ar_valid']} = v; {_cast('ar_addr', 'addr')} "
                    f"{_assign('ar_prot', 'prot')}",
                ),
                _fn("void set_rready(D &d, uint8_t v)", f"d.{p['r_ready']} = v;"),
                _fn("bool get_awready(const D &d)", f"return d.{p['aw_ready']} != 0;"),
                _fn("bool get_wready(const D &d)", f"return d.{p['w_ready']} != 0;"),
                _fn("bool get_bvalid(const D &d)", f"return d.{p['b_valid']} != 0;"),
                _fn(
                    "uint8_t get_bresp(const D &d)",
                    f"return static_cast<uint8_t>(d.{p['b_resp']});",
                ),
                _fn("bool get_arready(const D &d)", f"return d.{p['ar_ready']} != 0;"),
                _fn("bool get_rvalid(const D &d)", f"return d.{p['r_valid']} != 0;"),
                _fn(
                    "uint64_t get_rdata(const D &d)",
                    f"return static_cast<uint64_t>(d.{p['r_data']});",
                ),
                _fn(
                    "uint8_t get_rresp(const D &d)",
                    f"return static_cast<uint8_t>(d.{p['r_resp']});",
                ),
            ]
        )
    elif proto == "axi4":
        clear_stmts = [
            f"d.{p['aw_valid']} = 0;",
            f"d.{p['aw_addr']} = 0;",
            _assign("aw_id", "0"),
            _assign("aw_len", "0"),
            _assign("aw_size", "0"),
            _assign("aw_burst", "0"),
            _assign("aw_prot", "0"),
            f"d.{p['w_valid']} = 0;",
            f"d.{p['w_data']} = 0;",
            f"d.{p['w_strb']} = 0;",
            _assign("w_last", "0"),
            f"d.{p['b_ready']} = 0;",
            f"d.{p['ar_valid']} = 0;",
            f"d.{p['ar_addr']} = 0;",
            _assign("ar_id", "0"),
            _assign("ar_len", "0"),
            _assign("ar_size", "0"),
            _assign("ar_burst", "0"),
            _assign("ar_prot", "0"),
            f"d.{p['r_ready']} = 0;",
        ]
        bid_expr = f"static_cast<uint32_t>(d.{p['b_id']})" if "b_id" in p else "0u"
        rid_expr = f"static_cast<uint32_t>(d.{p['r_id']})" if "r_id" in p else "0u"
        rlast_expr = f"static_cast<uint8_t>(d.{p['r_last']})" if "r_last" in p else "1u"
        lines.extend(
            [
                _fn("void clear_inputs(D &d)", " ".join(s for s in clear_stmts if s)),
                _fn(
                    "void set_aw(D &d, uint8_t v, uint64_t addr, uint32_t id, "
                    "uint8_t len, uint8_t sz, uint8_t burst)",
                    f"(void)id; (void)len; (void)sz; (void)burst; d.{p['aw_valid']} = v; "
                    f"{_cast('aw_addr', 'addr')} {_cast('aw_id', 'id')} "
                    f"{_assign('aw_len', 'len')} {_assign('aw_size', 'sz')} "
                    f"{_assign('aw_burst', 'burst')}",
                ),
                _fn(
                    "void set_w(D &d, uint8_t v, uint64_t data, uint32_t strb, uint8_t last)",
                    f"(void)last; d.{p['w_valid']} = v; {_cast('w_data', 'data')} "
                    f"{_cast('w_strb', 'strb')} {_assign('w_last', 'last')}",
                ),
                _fn("void set_bready(D &d, uint8_t v)", f"d.{p['b_ready']} = v;"),
                _fn(
                    "void set_ar(D &d, uint8_t v, uint64_t addr, uint32_t id, "
                    "uint8_t len, uint8_t sz, uint8_t burst)",
                    f"(void)id; (void)len; (void)sz; (void)burst; d.{p['ar_valid']} = v; "
                    f"{_cast('ar_addr', 'addr')} {_cast('ar_id', 'id')} "
                    f"{_assign('ar_len', 'len')} {_assign('ar_size', 'sz')} "
                    f"{_assign('ar_burst', 'burst')}",
                ),
                _fn("void set_rready(D &d, uint8_t v)", f"d.{p['r_ready']} = v;"),
                _fn("bool get_awready(const D &d)", f"return d.{p['aw_ready']} != 0;"),
                _fn("bool get_wready(const D &d)", f"return d.{p['w_ready']} != 0;"),
                _fn("bool get_bvalid(const D &d)", f"return d.{p['b_valid']} != 0;"),
                _fn(
                    "uint8_t get_bresp(const D &d)",
                    f"return static_cast<uint8_t>(d.{p['b_resp']});",
                ),
                _fn("uint32_t get_bid(const D &d)", f"(void)d; return {bid_expr};"),
                _fn("bool get_arready(const D &d)", f"return d.{p['ar_ready']} != 0;"),
                _fn("bool get_rvalid(const D &d)", f"return d.{p['r_valid']} != 0;"),
                _fn(
                    "uint64_t get_rdata(const D &d)",
                    f"return static_cast<uint64_t>(d.{p['r_data']});",
                ),
                _fn(
                    "uint8_t get_rresp(const D &d)",
                    f"return static_cast<uint8_t>(d.{p['r_resp']});",
                ),
                _fn("uint32_t get_rid(const D &d)", f"(void)d; return {rid_expr};"),
                _fn("uint8_t get_rlast(const D &d)", f"(void)d; return {rlast_expr};"),
            ]
        )
    elif proto == "tl-ul":
        clear_stmts = [
            f"d.{p['a_valid']} = 0;",
            f"d.{p['a_opcode']} = 0;",
            _assign("a_param", "0"),
            f"d.{p['a_size']} = 0;",
            _assign("a_source", "0"),
            f"d.{p['a_address']} = 0;",
            f"d.{p['a_mask']} = 0;",
            f"d.{p['a_data']} = 0;",
            f"d.{p['d_ready']} = 0;",
        ]
        dsrc_expr = f"static_cast<uint32_t>(d.{p['d_source']})" if "d_source" in p else "0u"
        lines.extend(
            [
                _fn("void clear_inputs(D &d)", " ".join(s for s in clear_stmts if s)),
                _fn(
                    "void set_a(D &d, uint8_t v, uint8_t op, uint8_t param, uint8_t sz, "
                    "uint32_t src, uint64_t addr, uint32_t mask, uint64_t data)",
                    f"(void)param; (void)src; d.{p['a_valid']} = v; d.{p['a_opcode']} = op; "
                    f"{_assign('a_param', 'param')} d.{p['a_size']} = sz; "
                    f"{_cast('a_source', 'src')} {_cast('a_address', 'addr')} "
                    f"{_cast('a_mask', 'mask')} {_cast('a_data', 'data')}",
                ),
                _fn("void set_dready(D &d, uint8_t v)", f"d.{p['d_ready']} = v;"),
                _fn("bool get_aready(const D &d)", f"return d.{p['a_ready']} != 0;"),
                _fn("bool get_dvalid(const D &d)", f"return d.{p['d_valid']} != 0;"),
                _fn(
                    "uint64_t get_ddata(const D &d)",
                    f"return static_cast<uint64_t>(d.{p['d_data']});",
                ),
                _fn(
                    "uint8_t get_derror(const D &d)",
                    f"return static_cast<uint8_t>(d.{p['d_error']});",
                ),
                _fn("uint32_t get_dsource(const D &d)", f"(void)d; return {dsrc_expr};"),
            ]
        )
    lines.append("};")
    return lines
