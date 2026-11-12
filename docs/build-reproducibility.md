# Reproducible source-build profile

The custom-machine path pins QEMU 10.0.0 at
`7c949c53e936aa3a658d84ab53bae5cadaa5d59c` and Zephyr 4.2.0 at
`413b789deb391d3a37d06b463288a5fe765ee57e`. The legacy virt smoke independently
retains its Debian QEMU 10.0.13 profile. Never relabel those two binaries as equal.

Tested host: Debian trixie amd64, Python 3.12, GCC RISC-V unknown-elf 14.2.0+19,
binutils 2.44-3+7+b1. `uv.lock` pins Python runtime/dev/bringup dependencies.
Source-build host dependencies are GCC/G++, make, pkg-config, GLib development
headers/libraries, pixman development headers, zlib development headers, libfdt-dev
and dtc. CI installs them from signed Debian repositories. QEMU is configured only
for riscv64-softmmu, without default devices/features; four parallel jobs were used
in this workspace. Normal core parsing does not need the optional native toolchain.

The source-build adapter copies the exact clean QEMU revision into an ignored
build tree and applies its explicit Meson/Kconfig integration there. Upstream
source remains clean. Generated machine source is compiled and linked into the
binary. This is not a plugin ABI or a command-line virt configuration.

Use `uv sync --frozen --extra dev --extra bringup`, then follow the example guide.
On rootless Debian installations, downloaded development packages may be extracted
into a user prefix through verified APT acquisition. Set `USP_HOST_PREFIX` to that
prefix and use a pkg-config wrapper with its include/library prefix. Preserve the
system trust store and signed package indexes. Do not mix incompatible host libc
or overwrite system files. The current workspace prefix is
`/workspace/.local/socsim-tools`; it is not required on ordinary installed hosts.

No simulator process survives by assumption. Activation, build paths and startup
are explicit. Generated output contains IR, selected versioned model contracts,
source revisions, template/catalog hashes and file hashes. Build records bind
QEMU/ELF identities to the generation manifest. Run reports include raw logs and
hashes. This establishes input/artifact traceability; bit-identical binaries across
arbitrary hosts are not claimed. Toolchain paths, compiler/library versions and
build flags can affect binary output.

The optional custom-zephyr CI job rebuilds from sources. Quick core and legacy
runtime jobs remain separate. Main promotion requires the custom acceptance test
through USP_GENERATED_DIR and USP_REQUIRE_BRINGUP; absent tools must fail that gate.

QEMU 10.0.0 Meson wraps pin Berkeley softfloat/testfloat and keycodemapdb to exact
commits. First configuration currently performs those documented public downloads
from gitlab.com. The checked-in wrap files at the pinned QEMU source are the source
of truth; no floating branch is substituted. Simulation and generation have no
network acquisition steps. A fully offline source bundle must include these wraps'
resolved source trees as well as the original checkout.

Rootless Debian setup is reproducible with scripts/setup_user_tools.sh followed by
scripts/setup_bringup_tools.sh. Set USP_INSTALL_MODELS=1 to acquire SystemC 3.0.1 and
Verilator 5.032. Activate env_bringup.sh before building; it selects the user-prefix
pkg-config wrapper and library paths. Both helpers were exercised in this workspace
through signed indexes and verified Debian artifact downloads, without root access.
The Linux build uses the separate riscv64-linux-gnu compiler/linker; its bare-metal
counterpart cannot link a vDSO shared object. The adapter probes this before building.

The gem5 source profile is v24.1.0.3, commit
b9da2bfe1e21f6efa2730edd4e80e58c5d965458. Its source build uses SCons and the RISCV
build target, with Python development headers and an actual libpython runtime.
Relocated cloud Python distributions can have stale sysconfig LIBDIR values; the
adapter uses sys.base_prefix/lib for its subprocess loader path. This is a loader
configuration correction, not a skipped version or link check.

The optional FreeRTOS kernel is V11.2.0 at
0adc196d4bd52a2d91102b525b0aafc1e14a2386, acquired with USP_FETCH_FREERTOS=1.
It uses the existing GNU unknown-elf GCC 14.2.0 toolchain and unmodified upstream
RISC-V port. The software BSP has an independent resource/adapter identity and
binds its ELF to the validated hardware generation manifest. See freertos.md for
the real preemption/IRQ and negative guest acceptance contract.
