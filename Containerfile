# SDK packaging revision 0.3.0a5-r1 (CHIA-style HW/SW Emulator & QA SDK).
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS sdk
ARG SOURCE_REVISION
LABEL org.opencontainers.image.title="SoC co-design HW/SW Emulator SDK" \
      org.opencontainers.image.source="https://github.com/Duy-Huan/IP-XACT" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="0.3.0a5-r1"
COPY --from=uv /uv /uvx /usr/local/bin/
COPY containers/with-trust.sh /usr/local/bin/with-trust
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy UV_SYSTEM_CERTS=true
WORKDIR /opt/socsim
# Both OCI clients apply the same allowlist; the wrapper uses git archive HEAD.
COPY . .
RUN --mount=type=secret,id=proxy_ca \
    with-trust sh -ec 'apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git curl xz-utils unzip zip bash tini procps file shellcheck=0.10.0-1 \
      gcc g++ make bzip2 pkg-config python3 python3-dev \
      libglib2.0-dev libpixman-1-dev zlib1g-dev libfdt-dev device-tree-compiler \
      flex bison bc m4 libboost-dev openjdk-21-jdk-headless \
      libsystemc-dev=3.0.1-1 verilator=5.032-1+b2 \
      yosys=0.52-2 yosys-abc=0.52-2 \
      gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu \
      qemu-system-riscv=1:10.0.13+ds-0+deb13u1 \
      qemu-system-data=1:10.0.13+ds-0+deb13u1 \
      gcc-riscv64-unknown-elf=14.2.0+19 binutils-riscv64-unknown-elf=2.44-3+7+b1 \
      skopeo; \
    dpkg-query -W > /usr/local/share/sdk-packages.tsv; \
    rm -rf /var/lib/apt/lists/*' && \
    with-trust uv python install 3.12.14 && \
    with-trust uv sync --frozen --all-extras --no-editable --python 3.12.14 && \
    rm -rf /root/.cache/uv && \
    cp containers/entrypoint.sh /usr/local/bin/socsim-entrypoint && \
    chmod 755 /usr/local/bin/with-trust /usr/local/bin/socsim-entrypoint && \
    mkdir -p /workspace /cache && chmod 1777 /workspace /cache && \
    printf '%s\n' "${SOURCE_REVISION}" > /usr/local/share/socsim-revision && \
    chmod -R a+rX /opt/python /opt/socsim
ENV PATH=/opt/socsim/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/cache/home XDG_CACHE_HOME=/cache/xdg UV_CACHE_DIR=/cache/uv \
    CROSS_COMPILE=riscv64-unknown-elf- CROSS_COMPILE_LINUX=riscv64-linux-gnu- \
    USP_JOBS=2
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/socsim-entrypoint"]
CMD ["bash"]
