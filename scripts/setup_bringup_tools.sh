#!/usr/bin/env bash
# Optional Debian trixie amd64 user-prefix development packages. Signed APT only.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${SOCSIM_TOOLS_DIR:-$repo/.tools/host}
[[ -f "$tools_dir/env.sh" ]] || { echo 'Run setup_user_tools.sh first' >&2; exit 1; }
tools_dir=$(cd "$tools_dir" && pwd)
cache="$tools_dir/cache"
options=(-o "Dir::Etc::sourcelist=$cache/sources.list" -o Dir::Etc::sourceparts=-
    -o Dir::Etc::parts=- -o Dir::Etc::main=- -o "Dir::State::lists=$cache/lists"
    -o "Dir::Cache=$cache/archives" -o "APT::Sandbox::User=$(id -un)")
packages=(libglib2.0-dev=2.84.4-3~deb13u5 libglib2.0-dev-bin=2.84.4-3~deb13u5
    libgio-2.0-dev=2.84.4-3~deb13u5 libglib2.0-0t64=2.84.4-3~deb13u5
    libglib2.0-data=2.84.4-3~deb13u5 libpixman-1-dev=0.44.0-3
    libfdt-dev=1.7.2-2+b1 device-tree-compiler=1.7.2-2+b1
    libffi-dev=3.4.8-2 libsysprof-capture-4-dev=48.0-2
    flex=2.6.4-8.2+b4 bison=2:3.8.2+dfsg-1+b2 bc=1.07.1-4 m4=1.4.19-8
    gcc-14-riscv64-linux-gnu=14.2.0-19cross1 cpp-14-riscv64-linux-gnu=14.2.0-19cross1
    gcc-riscv64-linux-gnu=4:14.2.0-1 binutils-riscv64-linux-gnu=2.44-3
    libgcc-14-dev-riscv64-cross=14.2.0-19cross1 libgcc-s1-riscv64-cross=14.2.0-19cross1
    libc6-dev-riscv64-cross=2.41-11cross1 libc6-riscv64-cross=2.41-11cross1
    linux-libc-dev-riscv64-cross=6.12.38-1cross1)
if [[ "${USP_INSTALL_MODELS:-0}" == 1 ]]; then
    packages+=(libsystemc=3.0.1-1 libsystemc-dev=3.0.1-1 verilator=5.032-1+b2)
fi
/usr/bin/apt-get "${options[@]}" update
download_dir=$(mktemp -d "$cache/build-deps.XXXXXXXX")
trap 'rm -rf -- "$download_dir"' EXIT
cd "$download_dir"
/usr/bin/apt-get "${options[@]}" download "${packages[@]}"
sha256sum ./*.deb > "$tools_dir/bringup-packages.sha256"
printf '%s\n' "${packages[@]}" > "$tools_dir/bringup-packages.lock"
for package in ./*.deb; do dpkg-deb -x "$package" "$tools_dir"; done
mkdir -p "$tools_dir/bin"
# APT paths and compiler prefixes remain under the user-owned prefix.
printf '#!/usr/bin/env bash\nexport PKG_CONFIG_PATH=%q\nexec /usr/bin/pkg-config --define-variable=prefix=%q "$@"\n' \
    "$tools_dir/usr/lib/x86_64-linux-gnu/pkgconfig" "$tools_dir/usr" > "$tools_dir/bin/pkg-config"
chmod +x "$tools_dir/bin/pkg-config"
printf 'source %q\nexport PATH=%q:"$PATH"\nexport USP_HOST_PREFIX=%q\n' \
    "$tools_dir/env.sh" "$tools_dir/bin" "$tools_dir" > "$tools_dir/env_bringup.sh"
if [[ "${USP_INSTALL_MODELS:-0}" == 1 ]]; then
    printf 'export VERILATOR_ROOT=%q\n' "$tools_dir/usr/share/verilator" >> "$tools_dir/env_bringup.sh"
fi
echo "Activate: source $tools_dir/env_bringup.sh"
