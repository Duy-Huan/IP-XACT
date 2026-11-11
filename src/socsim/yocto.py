"""Generated scarthgap BSP metadata; image acceptance remains separate from parsing."""

import shutil

POKY_SHA = "bab0f9f62af9af580744948dd3240f648a99879a"
LINUX_SHA = "adc218676eef25575469234709c2d87185ca223a"


def generate_layer(out):
    layer = out / "yocto/meta-usp"
    files = {
        "conf/layer.conf": """BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb"
BBFILE_COLLECTIONS += "usp"
BBFILE_PATTERN_usp = "^${LAYERDIR}/"
BBFILE_PRIORITY_usp = "7"
LAYERSERIES_COMPAT_usp = "scarthgap"
""",
        "conf/machine/usp-rv64.conf": """# Generated; functional QEMU usp-rv64 contract, not virt.
DEFAULTTUNE = "riscv64nf"
require conf/machine/include/riscv/tune-riscv.inc
PREFERRED_PROVIDER_virtual/kernel = "linux-usp"
KERNEL_IMAGETYPE = "Image"
KERNEL_DEVICETREE = "usp.dtb"
SERIAL_CONSOLES = "115200;ttyS0"
MACHINE_FEATURES = "serial"
IMAGE_FSTYPES = "cpio.gz"
""",
        "recipes-kernel/linux/linux-usp_6.12.bb": '''SUMMARY = "USP IR-derived Linux board contract"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://COPYING;md5=6bc538ed5bd9a7fc9398086aedcd7e46"
inherit kernel
SRC_URI = "git://github.com/torvalds/linux.git;protocol=https;nobranch=1"
SRC_URI += "file://defconfig file://usp.dts"
SRCREV = "'''
        + LINUX_SHA
        + """"
S = "${WORKDIR}/git"
COMPATIBLE_MACHINE = "usp-rv64"
do_configure:prepend() {
    install -m 0644 ${WORKDIR}/usp.dts ${S}/arch/riscv/boot/dts/usp.dts
}
""",
        "recipes-kernel/linux/files/defconfig": """CONFIG_64BIT=y
CONFIG_MMU=y
CONFIG_RISCV_SBI=y
CONFIG_SIFIVE_PLIC=y
CONFIG_PRINTK=y
CONFIG_TTY=y
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_CONSOLE=y
CONFIG_SERIAL_OF_PLATFORM=y
CONFIG_BLK_DEV_INITRD=y
CONFIG_RD_GZIP=y
CONFIG_BINFMT_ELF=y
CONFIG_DEVTMPFS=y
CONFIG_DEVTMPFS_MOUNT=y
CONFIG_TMPFS=y
CONFIG_PROC_FS=y
CONFIG_SYSFS=y
CONFIG_UNIX=y
CONFIG_NET=y
CONFIG_MULTIUSER=y
CONFIG_FUTEX=y
CONFIG_POSIX_TIMERS=y
CONFIG_POWER_RESET=y
CONFIG_POWER_RESET_SYSCON=y
""",
        "recipes-core/images/usp-image.bb": """SUMMARY = "USP minimal userspace image"
LICENSE = "MIT"
inherit core-image
IMAGE_INSTALL = "packagegroup-core-boot usp-smoke"
IMAGE_FSTYPES = "cpio.gz"
""",
        "recipes-core/usp-smoke/usp-smoke.bb": """SUMMARY = "Original USP console acceptance marker"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://usp-smoke"
S = "${WORKDIR}"
inherit update-rc.d
INITSCRIPT_NAME = "usp-smoke"
INITSCRIPT_PARAMS = "start 99 2 3 4 5 ."
do_install() {
    install -d ${D}${sysconfdir}/init.d
    install -m 0755 ${WORKDIR}/usp-smoke ${D}${sysconfdir}/init.d/usp-smoke
}
""",
        "recipes-core/usp-smoke/files/usp-smoke": (
            "#!/bin/sh\necho USP_YOCTO_USERSPACE_OK > /dev/console\n"
        ),
    }
    for name, content in files.items():
        path = layer / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    shutil.copyfile(out / "linux.dts", layer / "recipes-kernel/linux/files/usp.dts")
    return layer
