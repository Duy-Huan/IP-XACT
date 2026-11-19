"""Pinned Chipyard RocketTile elaboration and actual AXI/SystemC executable."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from .domain import ContractError
from .ipxact import digest
from .pipeline import execute, verify_source
from .rocket_elaboration import lower_annotations
from .runner import tool_version, write_json

RESOURCES = Path(__file__).parent / "resources/rocket_cpu"
PINS = {
    "chipyard": "69eba860a352343e4ac6b6df0f3638a79a86ec78",
    "rocket-chip": "72690b07c0939a58670418b97e5442835b2049b5",
    "rocket-diplomacy": "6b7dc988a771ff21237b102fd02a1f2d2c2e7b72",
    "rocket-hardfloat": "4225367ed282aadd70f4cbd131f6959f4d00c502",
    "rocket-cde": "2bcaeae2b9914bd25497ce3c6fa62dc5ca80e09f",
}
PARENT_PATHS = {
    "rocket-chip": "generators/rocket-chip",
    "rocket-diplomacy": "generators/diplomacy",
    "rocket-hardfloat": "generators/hardfloat",
    "rocket-cde": "tools/cde",
}
SBT_SHA256 = "b14490ad2ce5b7650bf2da813ca0f57d123318f819a1ce69207b8351057386a6"
FIRTOOL_SHA256 = "0dd855e895e7763d4fe07f537c48631f8cbd79725a01256995ebcc1fcacb97aa"
JDK_SHA256 = "992f96e7995075ac7636bb1a8de52b0c61d71ed3137fafc979ab96b4ab78dd75"
CPU_CONTRACT = {
    "model": "rocket64-imac-sv39-axi32",
    "isa": "rv64imac_zicsr_zifencei",
    "privilege_modes": "MSU",
    "mmu": "sv39",
    "harts": 1,
    "physical_address_bits": 32,
    "axi_data_bits": 64,
}


def adapter_identity():
    return {
        "adapter": digest(Path(__file__)),
        "annotations": digest(Path(__file__).with_name("rocket_elaboration.py")),
        "resources": {p.name: digest(p) for p in sorted(RESOURCES.iterdir()) if p.is_file()},
    }


def ram_contract(base, size):
    if (
        type(base) is not int
        or type(size) is not int
        or base < 0
        or not 4096 <= size <= 1 << 32
        or size & (size - 1)
        or base % size
        or base + size > 1 << 32
    ):
        raise ContractError("Rocket AXI32 requires an aligned power-of-two RAM inside 32-bit PA")
    return {"base": base, "size": size}


def address_map_contract(ram, regions):
    if not isinstance(regions, list) or not 1 <= len(regions) <= 96:
        raise ContractError("Rocket requires explicit bounded ROM/MMIO regions")
    intervals = [(ram["base"], ram["base"] + ram["size"])]
    for region in regions:
        if not isinstance(region, dict) or set(region) != {"base", "size", "kind"}:
            raise ContractError("invalid Rocket memory-region shape")
        base, size = region["base"], region["size"]
        if (
            type(base) is not int
            or type(size) is not int
            or base < 0
            or size <= 0
            or base + size > 1 << 32
            or region["kind"] not in ("rom", "mmio")
        ):
            raise ContractError("unsupported Rocket region type/range")
        intervals.append((base, base + size))
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ContractError("overlapping Rocket PMA regions")
    return sorted(regions, key=lambda r: r["base"])


def source_contract(upstream):
    sources = {name: verify_source(upstream / name, sha) for name, sha in PINS.items()}
    for name, relative in PARENT_PATHS.items():
        line = subprocess.check_output(
            ["git", "ls-tree", "HEAD", "--", relative], cwd=sources["chipyard"], text=True
        ).split()
        if len(line) < 3 or line[:3] != ["160000", "commit", PINS[name]]:
            raise ContractError("Rocket sources differ from pinned Chipyard gitlinks")
    return sources


def maven_inventory(path):
    expected = json.loads((RESOURCES / "maven-lock.json").read_text())
    actual: dict[str, str] = {}
    for line in path.read_text().splitlines():
        p = Path(line)
        if p.name in actual and actual[p.name] != digest(p):
            raise ContractError("ambiguous Rocket Maven artifact")
        actual[p.name] = digest(p)
    if actual != expected:
        raise ContractError("Rocket Maven/compiler dependencies differ from the reviewed lock")
    return actual


def verify_jdk(java_home):
    archive = java_home.parent / "jdk17.tar.gz"
    if digest(archive) != JDK_SHA256:
        raise ContractError("Rocket JDK archive differs from its official pinned checksum")
    with tarfile.open(archive) as bundle:
        for entry in bundle:
            path = java_home.parent / entry.name
            if not path.resolve().is_relative_to(java_home.resolve()):
                raise ContractError("escaped JDK installation entry")
            if entry.isfile():
                stream = bundle.extractfile(entry)
                if stream is None:
                    raise ContractError("missing JDK archive entry")
                with stream:
                    hasher = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                    expected = hasher.hexdigest()
                if digest(path) != expected:
                    raise ContractError("modified JDK installation")
            elif entry.issym() and (not path.is_symlink() or os.readlink(path) != entry.linkname):
                raise ContractError("modified JDK symlink")
    return JDK_SHA256


def elaborate(out, upstream, tools, java_home, scala_cache, ram_base, ram_size, regions, jobs=2):
    out, upstream, tools, java_home, scala_cache = (
        p.resolve() for p in (out, upstream, tools, java_home, scala_cache)
    )
    ram = ram_contract(ram_base, ram_size)
    regions = address_map_contract(ram, regions)
    if type(jobs) is not int or not 1 <= jobs <= 8:
        raise ContractError("invalid Rocket build concurrency")
    if out.exists():
        raise ContractError("preserve existing Rocket build output")
    sources = source_contract(upstream)
    sbt, firtool = tools / "sbt-launch-1.8.2.jar", tools / "firtool"
    if digest(sbt) != SBT_SHA256 or digest(firtool) != FIRTOOL_SHA256:
        raise ContractError("Rocket SBT/firtool identity mismatch")
    verify_jdk(java_home)
    java, java_version = tool_version(str(java_home / "bin/java"))
    release = (java_home / "release").read_text()
    if 'JAVA_VERSION="17.0.17"' not in release or "17.0.17" not in java_version:
        raise ContractError("pinned SBT 1.8.2 requires the qualified JDK 17.0.17")
    _, verilator_version = tool_version("verilator")
    if not verilator_version.startswith("Verilator 5.032"):
        raise ContractError("Rocket adapter pins Verilator 5.032")
    identity = adapter_identity()
    generator = out / "generator"
    (generator / "src/main/scala").mkdir(parents=True)
    (generator / "project").mkdir()
    shutil.copy2(RESOURCES / "Generate.scala", generator / "src/main/scala/Generate.scala")
    escaped = json.dumps(str(upstream))[1:-1]
    (generator / "build.sbt").write_text(
        (RESOURCES / "build.sbt.in").read_text().replace("@SOURCE_ROOT@", escaped)
    )
    (generator / "project/build.properties").write_text("sbt.version=1.8.2\n")
    (generator / "repositories").write_text(
        "[repositories]\nlocal\ncentral: https://repo.maven.apache.org/maven2/\n"
    )
    write_json(generator / "address-map.json", regions)
    env = dict(
        os.environ,
        COURSIER_CACHE=str(scala_cache / "coursier"),
        XDG_CACHE_HOME=str(scala_cache / "xdg"),
        JAVA_HOME=str(java_home),
    )
    command = [java, f"-XX:ActiveProcessorCount={jobs}", "-Xmx5G"]
    trust = Path("/etc/ssl/certs/java/cacerts")
    if trust.is_file():
        command.append("-Djavax.net.ssl.trustStore=" + str(trust))
    for protocol in ("http", "https"):
        proxy = urlparse(env.get(protocol.upper() + "_PROXY", ""))
        if proxy.hostname:
            command += [
                f"-D{protocol}.proxyHost={proxy.hostname}",
                f"-D{protocol}.proxyPort={proxy.port or 80}",
            ]
    command += [
        "-Dsbt.override.build.repos=true",
        "-Dsbt.repository.config=repositories",
        "-Dsbt.boot.directory=" + str(scala_cache / "boot"),
        "-Dsbt.global.base=" + str(scala_cache / "global"),
        "-Dsbt.ivy.home=" + str(scala_cache / "ivy"),
        "-Dsbt.supershell=false",
        "-jar",
        str(sbt),
    ]
    execute(command + ["inventory"], generator, out / "resolve.log", 600, env)
    dependencies = maven_inventory(generator / "dependencies.paths")
    execute(
        command + [f"runMain Generate {ram_base} {ram_size} address-map.json"],
        generator,
        out / "elaboration.log",
        600,
        env,
    )
    rtl = generator / "rtl"
    lower_annotations(rtl / "UspRocket.fir", rtl / "lower.fir", rtl / "descriptive.json")
    execute(
        [
            str(firtool),
            str(rtl / "lower.fir"),
            "--split-verilog",
            "-o",
            str(out / "rtl"),
            "-disable-all-randomization",
            "--lowering-options=disallowLocalVariables",
        ],
        out,
        out / "lowering.log",
        300,
    )
    flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "--libs", "systemc"], text=True)
    )
    sv = sorted([*(out / "rtl").glob("*.sv"), *(out / "rtl").glob("*.v")])
    if not sv or not (out / "rtl/UspRocket.sv").is_file():
        raise ContractError("Rocket lowering emitted no actual top RTL")
    execute(
        [
            "verilator",
            "--cc",
            "--exe",
            "--build",
            "--assert",
            "--top-module",
            "UspRocket",
            "--Mdir",
            str(out / "sim"),
            "-j",
            str(jobs),
            "-Wno-fatal",
            "-CFLAGS",
            "-std=c++17 " + shlex.join([f for f in flags if f.startswith("-I")]),
            "-LDFLAGS",
            shlex.join([f for f in flags if not f.startswith("-I")]),
            *(str(p) for p in sv),
            str(RESOURCES / "master.cpp"),
        ],
        out,
        out / "verilator.log",
        900,
    )
    licenses = out / "licenses"
    licenses.mkdir()
    for name, source in sources.items():
        for p in source.glob("LICENSE*"):
            if p.is_file():
                shutil.copy2(p, licenses / (name + "-" + p.name))
    source_contract(upstream)
    if adapter_identity() != identity:
        raise ContractError("Rocket adapter changed during compilation")
    managed = [
        generator / "src/main/scala/Generate.scala",
        generator / "build.sbt",
        generator / "project/build.properties",
        generator / "repositories",
        generator / "dependencies.paths",
        generator / "address-map.json",
        rtl / "UspRocket.fir",
        rtl / "lower.fir",
        rtl / "descriptive.json",
        out / "sim/VUspRocket",
        *sv,
        *licenses.iterdir(),
    ]
    record = {
        "contract": "chipyard-rocket-axi32-build-v1",
        **CPU_CONTRACT,
        "cpu_provider": "chipyard-pinned-rocket-chip",
        "source_pins": PINS,
        "ram": ram,
        "regions": regions,
        "irq_order": ["debug-disabled", "msip", "mtip", "meip", "seip"],
        "reset_vector": "explicit ELF entry inside RAM",
        "coherence": "upstream TLBroadcast for tile-internal clients; no external DMA coherence",
        "memory_transport": "aligned MMIO32 operations; explicit split of 64-bit AXI beats",
        "adapter_identity": identity,
        "tools": {
            "java": java_version,
            "jdk_archive_sha256": JDK_SHA256,
            "sbt_sha256": SBT_SHA256,
            "firtool_sha256": FIRTOOL_SHA256,
            "verilator": verilator_version,
        },
        "maven": dependencies,
        "files": {str(p.relative_to(out)): digest(p) for p in managed},
        "scope": "real single RocketTile, L1 caches/PTW/TLBroadcast/AXI; not full Chipyard board",
    }
    write_json(out / "rocket-build.json", record)
    return record


def verify_build(out):
    record = json.loads((out / "rocket-build.json").read_text())
    if record.get("contract") != "chipyard-rocket-axi32-build-v1":
        raise ContractError("unrecognized Rocket build contract")
    if record["adapter_identity"] != adapter_identity() or record["source_pins"] != PINS:
        raise ContractError("stale Rocket CPU source/adapter identity")
    if any(
        type(record.get(key)) is not type(value) or record.get(key) != value
        for key, value in CPU_CONTRACT.items()
    ):
        raise ContractError("Rocket CPU capabilities differ from its qualified source profile")
    ram_contract(record["ram"]["base"], record["ram"]["size"])
    address_map_contract(record["ram"], record["regions"])
    for name, expected in record["files"].items():
        path = (out / name).resolve()
        if not path.is_relative_to(out.resolve()) or digest(path) != expected:
            raise ContractError("modified/escaped Rocket CPU build artifact")
    required = {
        "sim/VUspRocket",
        "rtl/UspRocket.sv",
        "generator/rtl/UspRocket.fir",
        "generator/rtl/lower.fir",
        "generator/rtl/descriptive.json",
        "generator/src/main/scala/Generate.scala",
        "generator/build.sbt",
    }
    if not required.issubset(record["files"]):
        raise ContractError("Rocket build lacks its actual executable")
    return record
