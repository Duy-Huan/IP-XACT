"""Real Chipyard Chisel GCD and OpenTitan TL-UL GPIO source/build/run adapter."""

import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .domain import ContractError
from .execution import cleanup_on_termination
from .hybrid import OPENTITAN_SHA
from .ipblocks_codegen import RESOURCES
from .ipxact import digest
from .pipeline import execute, run_custom, verify_generated, verify_source
from .runner import tool_version, write_json

CHIPYARD_SHA = "69eba860a352343e4ac6b6df0f3638a79a86ec78"
GCD_PATH = "generators/chipyard/src/main/scala/example/GCD.scala"
GCD_SHA256 = "33bed5d1cd7628770d2adccff2ec4c33e6a8e903d4ad26faabfdf45ff66135d7"
SBT_SHA256 = "3cca02818047327a83efde776103a1ef92f76f72c062badbbb062499a3270c07"
FIRTOOL_SHA256 = "0dd855e895e7763d4fe07f537c48631f8cbd79725a01256995ebcc1fcacb97aa"


def adapter_identity():
    return {
        "adapter": digest(Path(__file__)),
        "resources": {p.name: digest(p) for p in sorted(RESOURCES.iterdir()) if p.is_file()},
    }


def select_gcd(source):
    """Select complete verbatim classes, no algorithm edits or text-to-model inference."""
    path = source / GCD_PATH
    if digest(path) != GCD_SHA256:
        raise ContractError("Chipyard GCD source content mismatch")
    text = path.read_text()
    io = text[text.index("class GCDIO(") : text.index("class HLSGCDAccelIO(")]
    core = text.split("// DOC include start: GCD chisel\n")[1].split(
        "// DOC include end: GCD chisel"
    )[0]
    return (
        "// Derived from Chipyard, BSD-3-Clause; see licenses/Chipyard-LICENSE.\n"
        "package chipyard.example\nimport chisel3._\nimport chisel3.util._\n" + io + core
    )


def verify_dependencies(paths):
    expected = json.loads((RESOURCES / "maven-lock.json").read_text())
    actual: dict[str, str] = {}
    for path in paths:
        if path.name in actual and actual[path.name] != digest(path):
            raise ContractError("ambiguous Maven artifact name")
        actual[path.name] = digest(path)
    if actual != expected:
        raise ContractError(
            "Maven artifact inventory/content differs from the pinned generator lock"
        )
    return actual


def build_ipblocks(out, chipyard, opentitan, tools, scala_cache, jobs=2):
    chipyard = verify_source(chipyard, CHIPYARD_SHA)
    opentitan = verify_source(opentitan, OPENTITAN_SHA)
    for path, sha in (
        (tools / "sbt-launch-1.10.7.jar", SBT_SHA256),
        (tools / "firtool", FIRTOOL_SHA256),
    ):
        if digest(path) != sha:
            raise ContractError("generator tools content mismatch; run setup_chipyard_tools.sh")
    java, java_version = tool_version("java")
    if not re.search(r"(?:version |openjdk )\"?21\.", java_version):
        raise ContractError("the isolated generator profile requires Java 21")
    _, verilator_version = tool_version("verilator")
    if not verilator_version.startswith("Verilator 5.032"):
        raise ContractError("source IP adapter pins Verilator 5.032")
    flags = shlex.split(
        subprocess.check_output(["pkg-config", "--cflags", "--libs", "systemc"], text=True)
    )
    out.mkdir(parents=True, exist_ok=True)
    generator = out / "generator"
    (generator / "src/main/scala").mkdir(parents=True, exist_ok=True)
    (generator / "project").mkdir(exist_ok=True)
    (generator / "src/main/scala/GCD.scala").write_text(select_gcd(chipyard))
    for resource, target in (
        ("Generate.scala", "src/main/scala/Generate.scala"),
        ("build.sbt", "build.sbt"),
    ):
        shutil.copyfile(RESOURCES / resource, generator / target)
    (generator / "project/build.properties").write_text("sbt.version=1.10.7\n")
    (generator / "repositories").write_text(
        "[repositories]\nlocal\ncentral: https://repo.maven.apache.org/maven2/\n"
    )
    env = os.environ.copy()
    env.update(
        COURSIER_CACHE=str(scala_cache / "coursier"),
        XDG_CACHE_HOME=str(scala_cache / "xdg"),
        CHISEL_FIRTOOL_PATH=str(tools),
    )
    command = [java, "-XX:ActiveProcessorCount=" + str(jobs), "-Xmx3G"]
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
        str(tools / "sbt-launch-1.10.7.jar"),
    ]
    execute([*command, "inventory"], generator, out / "scala-resolve.log", 600, env)
    dependencies = verify_dependencies(
        [Path(p) for p in (generator / "dependencies.paths").read_text().splitlines()]
    )
    execute([*command, "runMain Generate"], generator, out / "chisel-build.log", 600, env)
    rtl = generator / "rtl/GCDMMIOChiselModule.sv"
    if not rtl.read_text().startswith("// Generated by CIRCT firtool-1.62.0"):
        raise ContractError("unexpected Chisel/CIRCT output identity")
    args = [
        "verilator",
        "--cc",
        "--exe",
        "--build",
        "--top-module",
        "usp_ipblocks",
        "--Mdir",
        str(out / "rtl"),
        "-j",
        str(jobs),
        "-Wno-fatal",
        "-I" + str(opentitan / "hw/ip/prim/rtl"),
    ]
    for directory in ("hw/ip/prim/rtl", "hw/ip/prim_generic/rtl", "hw/ip/tlul/rtl"):
        args += ["-y", str(opentitan / directory)]
    args += [
        "-CFLAGS",
        " ".join(["-std=c++17", *[f for f in flags if f.startswith("-I")]]),
        "-LDFLAGS",
        " ".join(f for f in flags if not f.startswith("-I")),
    ]
    args += [
        str(opentitan / name)
        for name in (
            "hw/top_earlgrey/rtl/top_pkg.sv",
            "hw/ip/prim/rtl/prim_secded_pkg.sv",
            "hw/ip/prim/rtl/prim_alert_pkg.sv",
            "hw/ip/tlul/rtl/tlul_pkg.sv",
            "hw/ip/gpio/rtl/gpio_reg_pkg.sv",
            "hw/ip/gpio/rtl/gpio_reg_top.sv",
            "hw/ip/gpio/rtl/gpio.sv",
        )
    ]
    args += [str(rtl), str(RESOURCES / "ipblocks.sv"), str(RESOURCES / "server.cpp")]
    execute(args, out, out / "verilator-build.log", 300)
    inputs = {}
    for line in (out / "rtl/Vusp_ipblocks__verFiles.dat").read_text().splitlines():
        if line.startswith("S "):
            path = Path(line.split('"')[1])
            label = (
                "opentitan/" + path.relative_to(opentitan).as_posix()
                if path.is_relative_to(opentitan)
                else path.name
            )
            inputs[label] = digest(path)
    licenses = out / "licenses"
    licenses.mkdir(exist_ok=True)
    for root, name in ((chipyard, "Chipyard"), (opentitan, "OpenTitan")):
        shutil.copyfile(root / "LICENSE", licenses / (name + "-LICENSE"))
    record = {
        "schema_version": "1",
        "contract": "usp-source-ips-v1",
        "adapter_identity": adapter_identity(),
        "chipyard_commit": CHIPYARD_SHA,
        "opentitan_commit": OPENTITAN_SHA,
        "original_gcd_sha256": GCD_SHA256,
        "selected_gcd_sha256": digest(generator / "src/main/scala/GCD.scala"),
        "generated_rtl_sha256": digest(rtl),
        "binary_sha256": digest(out / "rtl/Vusp_ipblocks"),
        "maven_artifacts": dependencies,
        "verilator_inputs": inputs,
        "java": {"version": java_version, "binary_sha256": digest(Path(java))},
        "generator_versions": {
            "sbt": "1.10.7",
            "scala": "2.13.12",
            "chisel": "6.5.0",
            "firtool": "1.62.0",
        },
        "verilator": verilator_version,
        "systemc": "3.0.1 (header/link ABI checked)",
        "limitations": [
            "Selected Chipyard GCD classes; no Rocket/Diplomacy/full Chipyard build",
            "Real OpenTitan GPIO/TL-UL; synchronous alerts and explicit output/input loopback",
            "10 ns per driven RTL cycle, transaction-driven progress; no CPU-cycle correlation",
            "GCD IRQ and MMIO shim are USP-owned, not the upstream TLRegisterNode",
        ],
    }
    write_json(out / "ipblocks-build.json", record)
    return record


def verify_models(models):
    record = json.loads((models / "ipblocks-build.json").read_text())
    if (
        not isinstance(record, dict)
        or record.get("contract") != "usp-source-ips-v1"
        or record.get("adapter_identity") != adapter_identity()
        or record.get("chipyard_commit") != CHIPYARD_SHA
        or record.get("opentitan_commit") != OPENTITAN_SHA
        or record.get("original_gcd_sha256") != GCD_SHA256
        or record.get("maven_artifacts") != json.loads((RESOURCES / "maven-lock.json").read_text())
        or record.get("binary_sha256") != digest(models / "rtl/Vusp_ipblocks")
        or record.get("generated_rtl_sha256")
        != digest(models / "generator/rtl/GCDMMIOChiselModule.sv")
    ):
        raise ContractError("stale/modified source IP models; rebuild with the pinned sources")
    return record


@cleanup_on_termination
def run_ipblocks(project, generated, models, result_dir, timeout=20):
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ContractError("invalid timeout")
    verify_generated(project, generated)
    if project.manifest["profile"] != "usp-rv64-ips-v1":
        raise ContractError("source IP execution requires usp-rv64-ips-v1")
    record = verify_models(models)
    with tempfile.TemporaryDirectory(prefix="usp-ips-") as name:
        temp = Path(name)
        sock = temp / "bus.sock"
        trace = temp / "trace.jsonl"
        with (temp / "model.log").open("wb") as stream:
            process = subprocess.Popen(
                [str(models / "rtl/Vusp_ipblocks"), str(sock), str(trace)],
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not sock.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise ContractError("source IP model did not become ready")
                    time.sleep(0.01)
                report = run_custom(project, generated, result_dir, timeout, bridge_socket=sock)
                try:
                    if report["timed_out"] and process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                transactions = (
                    [json.loads(line) for line in trace.read_text().splitlines()]
                    if trace.exists()
                    else []
                )
                assertions = {
                    "chipyard_gcd_rtl_results": [
                        t["value"] for t in transactions if t["op"] == 0 and t["offset"] == 12
                    ]
                    == [6, 6, 255, 1],
                    "chipyard_completion_irq": any(t["irq"] & 1 for t in transactions),
                    "opentitan_gpio_edge_irq": any(
                        t["op"] == 1 and t["offset"] == 0x1014 and t["data"] == 1 and t["irq"] & 2
                        for t in transactions
                    ),
                    "opentitan_w1c_irq_clear": any(
                        t["op"] == 1
                        and t["offset"] == 0x1000
                        and t["data"] == 1
                        and not t["irq"] & 2
                        for t in transactions
                    ),
                    "no_model_bus_errors": all(t["status"] == 0 for t in transactions),
                    "model_exit_zero": process.returncode == 0,
                }
                report["assertions"].update(assertions)
                if not all(assertions.values()):
                    report["status"] = "FAILED"
                report["source_ip_models"] = record
                report["model_process"] = {"pid": process.pid, "returncode": process.returncode}
                report["limitations"] = record["limitations"]
                for src, dst in (
                    (trace, "bridge-trace.jsonl"),
                    (temp / "model.log", "bridge-model.log"),
                ):
                    if src.exists():
                        shutil.copyfile(src, result_dir / dst)
                    else:
                        (result_dir / dst).write_text("")
                shutil.copytree(models / "licenses", result_dir / "licenses")
                write_json(result_dir / "report.json", report)
                (result_dir / "report.md").write_text(
                    "# Chipyard/OpenTitan acceptance\n\nStatus: **"
                    + report["status"]
                    + "**\n\n"
                    + "\n".join(f"- {k}: {v}" for k, v in report["assertions"].items())
                    + "\n\n"
                    + "\n".join(record["limitations"])
                    + "\n"
                    + "\nRaw serial: stdout.log; ordered RTL transactions: bridge-trace.jsonl.\n"
                )
                return report
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
