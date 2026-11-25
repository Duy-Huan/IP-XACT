"""Source-built, identity-checked model packages for the native MMIO adapter."""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from .domain import ContractError
from .execution import cleanup_on_termination, execute_build
from .ipxact import digest
from .native_external import validate as validate_external
from .project import inside, keys, read_yaml
from .runner import tool_version, write_json
from .soc_spec import ROLE, catalog

CONTRACT = "native-model-package-1"


EXPECTED_INTERFACE = {
    "protocol": "usp-mmio32-v1",
    "access_bits": 32,
    "aperture_bytes": 4096,
    "endianness": "little",
    "irq_mask": 1,
    "irq_trigger": "level-high",
    "state_owner": "peripheral-process",
    "time": "annotated-systemc",
}
EXPECTED_INTERFACE_V2_REQUIRED = {
    "protocol": "usp-bus-v2",
    "access_bits": 64,
    "endianness": "little",
    "irq_mask": 1,
    "irq_trigger": "level-high",
    "state_owner": "peripheral-process",
    "time": "quantum-synced-systemc",
}
EXPECTED_INTERFACE_V2_OPTIONAL = frozenset(
    {"supports_dma_master", "supports_burst64", "supports_quantum_sync", "v1_backward_compat"}
)
USP_V1_MAGIC = 0x31505355
USP_V2_MAGIC = 0x32425355
USP_V2_STRUCT = struct.Struct("<IIHHIIIQQQ")
USP_V2_OP_RESPONSE = 0x8000
USP_V2_OP_DMA_READ = 0x0010
USP_V2_OP_DMA_WRITE = 0x0011
USP_V2_FLAG_PAYLOAD = 1 << 0
USP_V2_FLAG_BURST64 = 1 << 1
USP_V2_FLAG_QUANTUM = 1 << 3
QUAL_DMA_RAM_BASE = 0x80100000
QUAL_DMA_RAM_SIZE = 65536
RESERVED_ARTIFACTS = frozenset({"model", "package.yaml", "package-build.json", "build.log"})
SUPPORTED_REGISTER_ACCESS = frozenset({"read-only", "read-write", "write-one-clear"})


def _is_valid_interface(iface):
    if not isinstance(iface, dict):
        return False
    if iface == EXPECTED_INTERFACE:
        return True
    if iface.get("protocol") != "usp-bus-v2":
        return False
    allowed = (
        set(EXPECTED_INTERFACE_V2_REQUIRED) | {"aperture_bytes"} | EXPECTED_INTERFACE_V2_OPTIONAL
    )
    if not set(iface).issubset(allowed):
        return False
    if any(iface.get(k) != v for k, v in EXPECTED_INTERFACE_V2_REQUIRED.items()):
        return False
    ap = iface.get("aperture_bytes")
    if type(ap) is not int or ap not in {4096, 8192, 16384, 32768, 65536}:
        return False
    return all(isinstance(iface[k], bool) for k in set(iface) & EXPECTED_INTERFACE_V2_OPTIONAL)


def _validate_package_files(root, data):
    if not isinstance(data["sources"], dict) or not 1 <= len(data["sources"]) <= 64:
        raise ContractError("package requires explicit source hashes")
    if data["build"]["entry"] not in data["sources"]:
        raise ContractError("package build entry is not a pinned source")
    files = dict(data["sources"])
    for item in (data["license"], data["driver"]):
        name = item.get("file", item.get("source"))
        if not isinstance(name, str):
            raise ContractError("package source/license paths must be strings")
        if name in files and files[name] != item["sha256"]:
            raise ContractError("ambiguous package source identity")
        files[name] = item["sha256"]
    for name, sha in files.items():
        if name in RESERVED_ARTIFACTS:
            raise ContractError("package input collides with a reserved build artifact")
        p = inside(root, name)
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha) or digest(p) != sha:
            raise ContractError("modified package input: " + name)
    return files


def _validate_package_markers(markers):
    if (
        not isinstance(markers, list)
        or not markers
        or len(set(markers)) != len(markers)
        or any(
            not isinstance(m, str) or not re.fullmatch(r"[A-Z][A-Z0-9_:.-]{0,100}", m)
            for m in markers
        )
    ):
        raise ContractError("package driver requires explicit unique acceptance markers")


def _validate_package_registers(registers, aperture_bytes):
    if not isinstance(registers, list) or not registers:
        raise ContractError("package requires a register contract")
    offsets = set()
    register_names = set()
    for register in registers:
        keys(register, {"name", "offset", "access", "reset"}, "package register")
        offset = register["offset"]
        if (
            type(offset) is not int
            or offset % 4
            or not 0 <= offset < aperture_bytes
            or offset in offsets
            or not isinstance(register["name"], str)
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", register["name"])
            or register["name"] in register_names
            or not isinstance(register["access"], str)
            or register["access"] not in SUPPORTED_REGISTER_ACCESS
            or type(register["reset"]) is not int
            or not 0 <= register["reset"] < 2**64
        ):
            raise ContractError("invalid/unsupported package register contract")
        offsets.add(offset)
        register_names.add(register["name"])


def _validate_package_conformance(vectors, protocol="usp-mmio32-v1"):
    if not isinstance(vectors, list) or not 4 <= len(vectors) <= 64:
        raise ContractError("package requires bounded reset/MMIO/IRQ/error conformance vectors")
    allowed_ops = {0, 1, 2} if protocol == "usp-mmio32-v1" else {0, 1, 2, 3, 4, 5}
    max_val = 2**32 if protocol == "usp-mmio32-v1" else 2**64
    for vector in vectors:
        opt = (
            set(vector) & {"length", "burst_words", "expect_burst_words", "dma_expect_words"}
            if protocol == "usp-bus-v2"
            else set()
        )
        keys(
            vector, {"op", "offset", "data", "value", "status", "irq"} | opt, "package conformance"
        )
        for k in ("op", "offset", "data", "value", "status", "irq"):
            v = vector[k]
            if type(v) is not int or not 0 <= v < max_val:
                raise ContractError("invalid package conformance vector")
        if (
            vector["op"] not in allowed_ops
            or vector["status"] not in {0, 1, 2, 3}
            or vector["irq"] not in {0, 1}
        ):
            raise ContractError("invalid package conformance vector")
    if not (
        any(v["op"] == 2 for v in vectors)
        and any(v["status"] != 0 for v in vectors)
        and any(v["irq"] == 1 for v in vectors)
        and any(v["op"] == 1 for v in vectors)
    ):
        raise ContractError("package qualification must cover reset, write, IRQ and bus error")


def package_capabilities(package):
    """Return explicit transaction, reset, error, register and driver capability metadata."""
    iface = package["interface"]
    vectors = package["conformance"]
    is_v2 = iface["protocol"] == "usp-bus-v2"
    return {
        "contract": package["schema_version"],
        "package_id": package["id"],
        "vlnv": package["vlnv"],
        "protocol": iface["protocol"],
        "access_bits": iface["access_bits"],
        "aperture_bytes": iface["aperture_bytes"],
        "endianness": iface["endianness"],
        "irq_trigger": iface["irq_trigger"],
        "state_owner": iface["state_owner"],
        "time_model": iface["time"],
        "supports_reset": any(v["op"] == 2 for v in vectors),
        "supports_bus_error": any(v["status"] != 0 for v in vectors),
        "supports_irq": any(v["irq"] == 1 for v in vectors),
        "supports_dma_master": bool(iface.get("supports_dma_master", False)) if is_v2 else False,
        "supports_burst64": bool(iface.get("supports_burst64", False)) if is_v2 else False,
        "supports_quantum_sync": bool(iface.get("supports_quantum_sync", False))
        if is_v2
        else False,
        "v1_backward_compat": bool(iface.get("v1_backward_compat", False)) if is_v2 else True,
        "register_count": len(package["registers"]),
        "access_modes": sorted({r["access"] for r in package["registers"]}),
        "driver_markers": list(package["driver"]["expected_markers"]),
    }


def generate_package_header(package, prefix=None):
    """Synthesize a deterministic C register/reset/access contract header for a package."""
    guard = re.sub(r"[^A-Z0-9]+", "_", (prefix or package["id"]).upper()).strip("_")
    proto = package["interface"]["protocol"]
    lines = [
        f"#ifndef USP_PKG_{guard}_H",
        f"#define USP_PKG_{guard}_H",
        "#include <stdint.h>",
        f"/* Generated from package {package['id']} ({package['vlnv']}); protocol {proto}. */",
        f"#define USP_{guard}_APERTURE_BYTES 0x{package['interface']['aperture_bytes']:X}U",
        f"#define USP_{guard}_ACCESS_BITS {package['interface']['access_bits']}U",
    ]
    for reg in sorted(package["registers"], key=lambda r: r["offset"]):
        reg_name = reg["name"].upper()
        lines.append(
            f"#define USP_{guard}_REG_{reg_name}_OFFSET "
            f"0x{reg['offset']:03X}U /* {reg['access']} */"
        )
        lines.append(f"#define USP_{guard}_REG_{reg_name}_RESET 0x{reg['reset']:08X}U")
    lines.append(f"#endif /* USP_PKG_{guard}_H */")
    return "\n".join(lines) + "\n"


def read_package(path):
    path = path.resolve()
    data = read_yaml(path)
    keys(
        data,
        {
            "schema_version",
            "id",
            "vlnv",
            "license",
            "sources",
            "build",
            "interface",
            "registers",
            "conformance",
            "driver",
        },
        "model package",
    )
    if data["schema_version"] != CONTRACT or not re.fullmatch(
        r"[a-z][a-z0-9_.-]{0,63}", str(data["id"])
    ):
        raise ContractError("unsupported package version/identity")
    keys(data["license"], {"spdx", "file", "sha256"}, "package license")
    keys(data["build"], {"language", "entry"}, "package build")
    keys(data["driver"], {"source", "sha256", "expected_markers"}, "package driver")
    if not _is_valid_interface(data["interface"]) or data["build"]["language"] != "systemc-c++17":
        raise ContractError("package requires an unimplemented transaction/build adapter")
    files = _validate_package_files(path.parent, data)
    _validate_package_markers(data["driver"]["expected_markers"])
    _validate_package_registers(data["registers"], data["interface"]["aperture_bytes"])
    _validate_package_conformance(data["conformance"], data["interface"]["protocol"])
    # Reuse the engine's exact identity/protocol validation without pretending a binary exists.
    validate_external(
        {
            data["id"]: {
                "vlnv": data["vlnv"],
                "executable": "models/model",
                "sha256": digest(path),
                "license": data["license"]["spdx"],
                "protocol": data["interface"]["protocol"],
                "compatible": "socsim,packaged-model",
            }
        },
        ROLE,
        [m["vlnv"] for m in catalog().values()],
    )
    return data, {"package.yaml": digest(path), **files}


def _recv_exact(sock_obj, n):
    buf = b""
    while len(buf) < n:
        block = sock_obj.recv(n - len(buf))
        if not block:
            raise ContractError("incomplete package response")
        buf += block
    return buf


def _qualify_v2_session(client, package, actual, checks):
    """Run live usp-bus-v2 conformance vectors + v1 backward-compat probe + reverse DMA RAM."""
    qual_ram = bytearray(QUAL_DMA_RAM_SIZE)
    # Pre-seed 64-bit test pattern at QUAL_DMA_RAM_BASE (0x80100000) for reverse DMA tests.
    for i in range(64):
        struct.pack_into("<Q", qual_ram, i * 8, 0x1000000000000000 + i * 0x1111)

    if package["interface"].get("v1_backward_compat", False):
        # Prove wire-level usp-mmio32-v1 compatibility on the exact same binary & socket!
        client.sendall(struct.pack("<IIIII", USP_V1_MAGIC, 0, 2, 0, 0))
        v1_res = struct.unpack("<IIIII", _recv_exact(client, 20))
        checks["v1_backward_compat_reset"] = (
            v1_res[0] == USP_V1_MAGIC and v1_res[1] == 0 and v1_res[2] == 0
        )

    dma_stats = {"reads": 0, "writes": 0, "bytes": 0}
    for seq, vector in enumerate(package["conformance"], start=1):
        op = vector["op"]
        length = int(vector.get("length", 4))
        flags = 0
        payload = b""
        if op == 4:  # BURST_WRITE
            words = vector.get("burst_words", [])
            payload = b"".join(struct.pack("<Q", int(w)) for w in words)
            length = len(payload)
            flags = USP_V2_FLAG_PAYLOAD | USP_V2_FLAG_BURST64
        elif op == 3:  # BURST_READ
            flags = USP_V2_FLAG_BURST64
        elif op == 5:  # TIME_SYNC
            flags = USP_V2_FLAG_QUANTUM
        elif length == 8:
            flags = USP_V2_FLAG_BURST64

        hdr = USP_V2_STRUCT.pack(
            USP_V2_MAGIC,
            seq,
            op,
            0,
            length,
            0,
            flags,
            int(vector["offset"]),
            int(vector["data"]),
            seq * 100_000,
        )
        client.sendall(hdr + payload)

        # Full-duplex loop: service reverse DMA requests from plugin until our response arrives.
        while True:
            msg = USP_V2_STRUCT.unpack(_recv_exact(client, 48))
            m_magic, m_seq, m_op, m_status, m_len, m_irq, m_flags, m_addr, m_data, m_time = msg
            if m_magic != USP_V2_MAGIC:
                raise ContractError("invalid usp-bus-v2 magic in qualification")
            if m_op in (USP_V2_OP_DMA_READ, USP_V2_OP_DMA_WRITE):
                is_wr = m_op == USP_V2_OP_DMA_WRITE
                dma_data = _recv_exact(client, m_len) if (is_wr and m_len > 0) else b""
                ram_off = m_addr - QUAL_DMA_RAM_BASE
                if m_len == 0 or ram_off < 0 or ram_off + m_len > QUAL_DMA_RAM_SIZE:
                    dres = USP_V2_STRUCT.pack(
                        USP_V2_MAGIC,
                        m_seq,
                        m_op | USP_V2_OP_RESPONSE,
                        3,
                        0,
                        0,
                        0,
                        m_addr,
                        0,
                        m_time + 10_000,
                    )
                    client.sendall(dres)
                elif is_wr:
                    qual_ram[ram_off : ram_off + m_len] = dma_data
                    dma_stats["writes"] += 1
                    dma_stats["bytes"] += m_len
                    dres = USP_V2_STRUCT.pack(
                        USP_V2_MAGIC,
                        m_seq,
                        m_op | USP_V2_OP_RESPONSE,
                        0,
                        0,
                        0,
                        0,
                        m_addr,
                        0,
                        m_time + 10_000,
                    )
                    client.sendall(dres)
                else:
                    out_bytes = bytes(qual_ram[ram_off : ram_off + m_len])
                    dma_stats["reads"] += 1
                    dma_stats["bytes"] += m_len
                    dres = USP_V2_STRUCT.pack(
                        USP_V2_MAGIC,
                        m_seq,
                        m_op | USP_V2_OP_RESPONSE,
                        0,
                        m_len,
                        0,
                        USP_V2_FLAG_PAYLOAD | USP_V2_FLAG_BURST64,
                        m_addr,
                        0,
                        m_time + 10_000,
                    )
                    client.sendall(dres + out_bytes)
                continue

            rx_payload = (
                _recv_exact(client, m_len)
                if ((m_flags & USP_V2_FLAG_PAYLOAD) and m_len > 0)
                else b""
            )
            row = {
                "op": op,
                "offset": vector["offset"],
                "data": vector["data"],
                "value": m_data,
                "status": m_status,
                "irq": 1 if m_irq else 0,
            }
            for extra_k in ("length", "burst_words", "expect_burst_words", "dma_expect_words"):
                if extra_k in vector:
                    row[extra_k] = vector[extra_k]
            actual.append(row)

            ok = (
                m_op == (op | USP_V2_OP_RESPONSE)
                and m_seq == seq
                and m_status == vector["status"]
                and (1 if m_irq else 0) == vector["irq"]
                and (op == 5 or m_data == vector["value"])
            )
            if "expect_burst_words" in vector:
                got_words = list(struct.unpack(f"<{len(rx_payload) // 8}Q", rx_payload))
                ok = ok and got_words == [int(w) for w in vector["expect_burst_words"]]
            if "dma_expect_words" in vector:
                exp_map = vector["dma_expect_words"]
                base_addr = int(exp_map["addr"])
                exp_words = [int(w) for w in exp_map["words"]]
                off = base_addr - QUAL_DMA_RAM_BASE
                got_dma = list(
                    struct.unpack(
                        f"<{len(exp_words)}Q",
                        qual_ram[off : off + len(exp_words) * 8],
                    )
                )
                ok = (
                    ok
                    and got_dma == exp_words
                    and dma_stats["reads"] > 0
                    and dma_stats["writes"] > 0
                )
            checks[str(seq - 1)] = ok
            break
    return dma_stats


@cleanup_on_termination
def qualify_package(out, package):
    actual: list[dict] = []
    checks: dict[str, bool] = {}
    protocol = package["interface"]["protocol"]
    dma_stats = None
    with tempfile.TemporaryDirectory(prefix="usp-package-") as tmp:
        endpoint = Path(tmp) / "model.sock"
        command = [str(out / "model"), str(endpoint), str(out / "conformance.trace.jsonl")]
        write_json(out / "conformance.command.json", {"argv": command})
        with (out / "conformance.log").open("wb") as log:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                deadline = time.monotonic() + 5
                while not endpoint.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("source-built package failed to start")
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(3)
                    client.connect(str(endpoint))
                    if protocol == "usp-bus-v2":
                        dma_stats = _qualify_v2_session(client, package, actual, checks)
                    else:
                        for seq, vector in enumerate(package["conformance"]):
                            client.sendall(
                                struct.pack(
                                    "<IIIII",
                                    USP_V1_MAGIC,
                                    seq,
                                    vector["op"],
                                    vector["offset"],
                                    vector["data"],
                                )
                            )
                            response = _recv_exact(client, 20)
                            magic, echoed, status, value, irq = struct.unpack("<IIIII", response)
                            actual.append(
                                {
                                    "op": vector["op"],
                                    "offset": vector["offset"],
                                    "data": vector["data"],
                                    "value": value,
                                    "status": status,
                                    "irq": irq,
                                }
                            )
                            checks[str(seq)] = (
                                magic == USP_V1_MAGIC and echoed == seq and actual[-1] == vector
                            )
                checks["process_exit"] = process.wait(timeout=3) == 0
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    result = {
        "status": "PASSED" if checks and all(checks.values()) else "FAILED",
        "protocol": protocol,
        "checks": checks,
        "actual": actual,
        "binary_sha256": digest(out / "model"),
        "trace_sha256": digest(out / "conformance.trace.jsonl"),
        "scope": "actual source-model protocol/reset/MMIO/IRQ/error vectors; "
        "SoC IRQ delivery is separate",
        **({"dma_qualification": dma_stats} if dma_stats is not None else {}),
    }
    trace = [
        json.loads(line) for line in (out / "conformance.trace.jsonl").read_text().splitlines()
    ]
    if protocol == "usp-bus-v2":
        checks["actual_model_trace"] = len(trace) >= len(actual)
    else:
        checks["actual_model_trace"] = len(trace) == len(actual) and all(
            all(tx.get(key) == value for key, value in response.items())
            for tx, response in zip(trace, actual, strict=False)
        )
    result["status"] = "PASSED" if checks and all(checks.values()) else "FAILED"
    write_json(out / "qualification.json", result)
    if result["status"] != "PASSED":
        raise ContractError("source model differs from declared package conformance")
    return result


def build_package(path, out):
    path, out = path.resolve(), out.resolve()
    package, hashes = read_package(path)
    adapter_sha = digest(Path(__file__))
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, out / "package.yaml")
    for name in hashes.keys() - {"package.yaml"}:
        destination = inside(out, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(path.parent, name), destination)
    compiler, version = tool_version("c++")
    flags = shlex.split(
        subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "systemc"], text=True, timeout=15
        )
    )
    includes = [arg for f in flags if f.startswith("-I") for arg in ("-isystem", f[2:])]
    composer_inc = Path(__file__).resolve().parent / "resources" / "composer"
    if composer_inc.is_dir():
        includes += ["-I", str(composer_inc)]
    flags = [f for f in flags if not f.startswith("-I")]
    command = [
        compiler,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        *includes,
        str(inside(out, package["build"]["entry"])),
        *flags,
        "-o",
        str(out / "model"),
    ]
    execute_build(command, out, out / "build.log", 120)
    qualify_package(out, package)
    if (
        adapter_sha != digest(Path(__file__))
        or hashes != read_package(path)[1]
        or hashes != read_package(out / "package.yaml")[1]
    ):
        raise ContractError("package inputs changed during compilation")
    record = {
        "contract": CONTRACT,
        "package_id": package["id"],
        "vlnv": package["vlnv"],
        "adapter_sha256": digest(Path(__file__)),
        "inputs": hashes,
        "compiler": {"path": compiler, "version": version, "sha256": digest(Path(compiler))},
        "systemc_version": subprocess.check_output(
            ["pkg-config", "--modversion", "systemc"], text=True, timeout=15
        ).strip(),
        "binary_sha256": digest(out / "model"),
        "driver": package["driver"],
        "interface": package["interface"],
        "registers": package["registers"],
        "license": package["license"],
        "qualification_sha256": digest(out / "qualification.json"),
    }
    write_json(out / "package-build.json", record)
    verify_package(out)
    return record


def verify_package(out):
    out = out.resolve()
    package, hashes = read_package(out / "package.yaml")
    record = json.loads((out / "package-build.json").read_text())
    qualification = json.loads((out / "qualification.json").read_text())
    if (
        record.get("contract") != CONTRACT
        or record.get("inputs") != hashes
        or record.get("adapter_sha256") != digest(Path(__file__))
        or record.get("binary_sha256") != digest(out / "model")
        or any(record.get(k) != package[k] for k in ("driver", "interface", "registers", "license"))
        or record.get("package_id") != package["id"]
        or record.get("vlnv") != package["vlnv"]
        or record.get("qualification_sha256") != digest(out / "qualification.json")
        or qualification.get("status") != "PASSED"
        or not all(qualification["checks"].values())
        or qualification.get("binary_sha256") != record.get("binary_sha256")
        or qualification.get("trace_sha256") != digest(out / "conformance.trace.jsonl")
        or (
            package["interface"]["protocol"] == "usp-mmio32-v1"
            and qualification.get("actual") != package["conformance"]
        )
    ):
        raise ContractError("modified/stale model package; rebuild from pinned inputs")
    return record


def main(argv):
    parser = argparse.ArgumentParser(prog="usp package")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("package", type=Path)
    build.add_argument("--out", type=Path, required=True)
    commands.add_parser("verify").add_argument("out", type=Path)
    commands.add_parser("inspect").add_argument("package", type=Path)
    hdr = commands.add_parser("header")
    hdr.add_argument("package", type=Path)
    hdr.add_argument("--prefix")
    hdr.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            package, _ = read_package(args.package)
            print(json.dumps(package_capabilities(package), indent=2, sort_keys=True))
            return 0
        if args.command == "header":
            package, _ = read_package(args.package)
            text = generate_package_header(package, prefix=args.prefix)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text)
                print(json.dumps({"status": "PASSED", "header": str(args.out)}))
            else:
                print(text, end="")
            return 0
        result = (
            build_package(args.package, args.out)
            if args.command == "build"
            else verify_package(args.out)
        )
        print(
            json.dumps(
                {
                    "status": "PASSED",
                    "package_id": result["package_id"],
                    "binary_sha256": result["binary_sha256"],
                }
            )
        )
        return 0
    except (ContractError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 2
