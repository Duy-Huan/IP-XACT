#!/usr/bin/env bash
# Rootless, signed Debian package acquisition for this tested host profile only.
# No host installation, package hooks, Git changes, or relaxed trust checks.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tools_dir=${SOCSIM_TOOLS_DIR:-$repo/.tools/host}
mkdir -p "$tools_dir"
tools_dir=$(cd "$tools_dir" && pwd)
[[ "$(dpkg --print-architecture)" == amd64 ]] || { echo 'Requires amd64' >&2; exit 1; }
grep -q 'VERSION_CODENAME=trixie' /etc/os-release || { echo 'Requires Debian trixie' >&2; exit 1; }
test -r /usr/share/keyrings/debian-archive-keyring.gpg
cache="$tools_dir/cache"
mkdir -p "$cache/lists/partial" "$cache/archives/partial"
cat > "$cache/sources.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
EOF
apt_options=(
    -o "Dir::Etc::sourcelist=$cache/sources.list" -o Dir::Etc::sourceparts=-
    -o Dir::Etc::parts=- -o Dir::Etc::main=-
    -o "Dir::State::lists=$cache/lists" -o "Dir::Cache=$cache/archives"
    -o "APT::Sandbox::User=$(id -un)"
)
# Explicit package/version pins; APT validates Release signatures and package hashes.
packages=(
    qemu-system-riscv=1:10.0.13+ds-0+deb13u1 qemu-system-data=1:10.0.13+ds-0+deb13u1
    gcc-riscv64-unknown-elf=14.2.0+19 binutils-riscv64-unknown-elf=2.44-3+7+b1
    libcapstone5=5.0.7-1~deb13u1 libpmem1=1.13.1-1.1+b1 libfdt1=1.7.2-2+b1
    libvdeplug2t64=4.0.1-5.1+b1 libaio1t64=0.3.113-8+b1 liburing2=2.9-1
    libndctl6=81-1 libdaxctl1=81-1
)
/usr/bin/apt-get "${apt_options[@]}" update
# A fresh download directory avoids trusting unrelated or previously modified .debs.
download_dir=$(mktemp -d "$cache/download.XXXXXXXX")
trap 'rm -rf -- "$download_dir"' EXIT
cd "$download_dir"
/usr/bin/apt-get "${apt_options[@]}" download "${packages[@]}"
sha256sum ./*.deb > "$tools_dir/packages.sha256"
printf '%s\n' "${packages[@]}" > "$tools_dir/packages.lock"
for package in ./*.deb; do
    dpkg-deb -x "$package" "$tools_dir"
done
printf 'export PATH=%q:"$PATH"\n' "$tools_dir/usr/bin" > "$tools_dir/env.sh"
printf 'export LD_LIBRARY_PATH=%q"${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n' \
    "$tools_dir/usr/lib/x86_64-linux-gnu" >> "$tools_dir/env.sh"
source "$tools_dir/env.sh"
qemu-system-riscv64 --version
riscv64-unknown-elf-gcc --version
echo "Activate with: source $tools_dir/env.sh"
