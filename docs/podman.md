# Podman SDK on KDE Plasma (Linux amd64)

Current `dev` packages the CHIA-style HW/SW Emulator & QA SDK as `0.3.0a5-r1`
(`localhost/socsim-sdk:0.3.0a5-r1`, archive `socsim-sdk-0.3.0a5-r1-linux-amd64.oci.tar`),
adding `usp qa`, `socsim.sdk` (`EmulatorNode.run_in_podman()`), unseen-design
QA (`Helios_QA_SoC`) and `bash scripts/podman.sh qa <suite>`. The earlier
published tag `container-v0.2.0-r1` (`0.2.0-r1`) remains immutable and separate.
KDE Plasma can use this CLI from Konsole; guest serial output stays in the
terminal. No X11/Wayland socket, desktop session, privileged mode, host
container socket or KVM access is required.

## Load the release

Install rootless Podman using your distribution packages. Fedora normally uses
`sudo dnf install podman`; Debian/Ubuntu use `sudo apt install podman uidmap
slirp4netns fuse-overlayfs`. Check `podman info` as your normal user. Do not use
sudo for the SDK. Download the OCI archive, image-inspect.json and SHA256SUMS
from the repository's container release into one directory, then in Konsole:

```sh
sha256sum --check --ignore-missing SHA256SUMS
podman load --input socsim-sdk-0.2.0-r1-linux-amd64.oci.tar
git clone https://github.com/Duy-Huan/IP-XACT.git
cd IP-XACT
git checkout container-v0.2.0-r1
bash scripts/podman.sh smoke
bash scripts/podman.sh run
```

The release workflow tags the image `localhost/socsim-sdk:0.2.0-r1`. If another
import tool changes its name, inspect `podman images` and set USP_CONTAINER_IMAGE
to that image name or immutable ID before using the wrapper. SHA256 detects file
changes; it is not an independent signature. Releases are created by the hosted
workflow only after actual rootless Podman build, offline smoke, OCI export/load
and a second smoke pass. A pushed Git tag alone does not establish that assets
were published; inspect the actual release/job result.

## Build locally

```sh
git switch dev
bash scripts/podman.sh build
bash scripts/podman.sh smoke
bash scripts/podman.sh export dist/container
```

Build uses `git archive HEAD` in a temporary context. Commit tracked changes
first; untracked source files are not included until committed. Git history,
attached ZIP/PDF references, secrets, build outputs and local caches are not sent
to the builder. `.containerignore` and `.dockerignore` additionally allow only
the public source inputs needed by the Containerfile. No automatic downloading
of company inputs or uploading local work is performed. Base Debian and uv OCI
digests, Python 3.12.14, uv 0.13.5, the frozen Python lock and key native tool
versions are explicit. Remaining APT package versions are recorded inside the
image at /usr/local/share/sdk-packages.tsv; bit-identical rebuilds across changing
Debian indexes are not claimed. TLS and APT signature validation stay enabled.

If your build network needs a custom combined CA bundle, set USP_BUILD_CA_FILE
to its path. It is passed as the optional `proxy_ca` build secret, used only by
network build steps and never copied into image layers. Normal desktop builds
do not need this variable. Build requires network access to the OCI registries,
signed Debian repositories, PyPI and pinned Python release downloads.

## Source, caches and results

`run` binds the checkout at /workspace and executes as the host UID/GID with
Podman `--userns=keep-id`. Generated files remain owned by your normal account.
Separate writable directories under `.podman/` map as follows:

| Host directory | Container directory | Purpose |
|---|---|---|
| .podman/build | /workspace/build | Generated models, firmware and native builds |
| .podman/tools | /workspace/.tools | Pinned upstream checkouts and source-build tools |
| .podman/results | /workspace/results | JSON/Markdown reports and native logs |
| .podman/venv | /workspace/.venv | Optional checkout Python environment |
| .podman/cache | /cache | HOME, uv and user caches |

These mounts isolate container builds from pre-existing host .venv, .tools and
build binaries, which may have incompatible ABI/absolute paths. They deliberately
start empty; pre-existing cloud artifacts are not distributed. Open `.podman/results`
in Dolphin (enable hidden files with Alt+.) to inspect outputs. Override
USP_CONTAINER_STATE with another directory on the host to relocate all caches.
Do not remove it if you need its source models or results.

SELinux hosts use private `:Z` relabeling of these project directories. Set
USP_SELINUX_LABEL=z for explicitly shared concurrent containers, or set it empty
on hosts where relabeling is unsuitable. The wrapper never recursively chowns
your checkout. Never use it to mount all of your home directory. TTY is requested
only when launched interactively; arguments are forwarded as an array.

```sh
bash scripts/podman.sh run usp validate examples/rv64-ips
bash scripts/podman.sh run usp generate systems/AI_SOC/bringup --out build/AI_SOC
bash scripts/podman.sh run usp generate systems/AIZ_SOC/bringup --out build/AIZ_SOC
```

## Native model and software builds

The SDK includes the Python CLI and locked dev/bringup/model dependencies,
reference Debian QEMU 10.0.13, GCC unknown-elf 14.2.0, a separate Linux cross
compiler, SystemC 3.0.1, Verilator 5.032, Yosys 0.52, Java 21 and native build
prerequisites. The SDK's QEMU is the legacy `virt` smoke runtime, not the generated
QEMU 10.0.0 custom machine. The offline smoke compiles real firmware and requires
actual guest boot, failure, timeout and cancellation tests; it also validates
the source-IP input and generates both mapped bring-up designs.

The checkout's current Python source is selected through PYTHONPATH when using
`run`. For source changes needing different locked dependencies, synchronize the
isolated workspace environment first. In a container shell:

```sh
uv sync --frozen --all-extras --python 3.12.14
source .venv/bin/activate
export USP_FETCH_LINUX=1 USP_FETCH_MODELS=1 USP_FETCH_FREERTOS=1 USP_FETCH_CHIPYARD=1
bash scripts/fetch_upstreams.sh
bash scripts/bringup.sh
bash scripts/setup_chipyard_tools.sh
bash scripts/source_ips_bringup.sh
```

The last two commands build selected base/source-IP machines and firmware; follow
the relevant example, CPU, coherent DMA and RTL OS guides for further build roots,
CPU submodule acquisition, generator setup and required acceptance variables.
`scripts/setup_chipyard_tools.sh` is needed for the pinned public Chisel generator
tools. First source builds require approved network access and significant disk/
RAM; budget at least 100 GiB free storage and 32 GiB RAM for broad model work.
The bundled SDK itself requires much less. Do not reuse absolute-path native
artifacts from another checkout or call the SDK smoke a full-system regression.

Full prebuilt gem5/Chipyard/OpenTitan/Zephyr/Linux/Yocto images, CPU masters and
OpenROAD/PDK layouts are not embedded. Source model downloads and tested adapters
remain explicit. The current physical runner uses its separate Docker/OpenROAD
contract; running it inside this Podman SDK is not yet accepted. No host socket
is mounted automatically to hide that limitation. Bare-metal/OS, full inventory,
bus/MMU, timing and public-PDK limits in next-version.md remain unchanged.

## Validation and release scope

Local cloud validation may use `USP_OCI_ENGINE=docker` with the same Containerfile
when nested rootless user namespaces are unavailable. Set USP_DOCKER_HOST only
to select an intentional Docker endpoint. This compatibility test is distinct
from native Podman acceptance. Docker export is converted offline to a real OCI
archive using bundled skopeo; it is not a Docker archive renamed to .oci.tar.
If skopeo is installed on the host, Docker export uses it directly to avoid an
additional SDK container copy on VFS storage drivers.
Local compatibility tests never establish a hosted Podman success. Logs and exact
tested revision must be reported separately, along with any release/API blocker.
