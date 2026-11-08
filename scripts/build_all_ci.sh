#!/usr/bin/env bash
set -euxo pipefail
export PATH="/usr/sbin:/sbin:$PATH"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK_DIR="${WORK_DIR:-/tmp/redfin-build}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$REPO_ROOT/artifacts}"
LINUX_TAG="${LINUX_TAG:-v7.3-rc7}"

mkdir -p "$WORK_DIR" "$ARTIFACTS_DIR"

# ---------------------------------------------------------------------------
# 1. Extract Google Pixel 5 (redfin) signed GPU & Wi-Fi board firmware
#    and fetch upstream linux-firmware blobs (a650_sqe.fw, a650_gmu.bin, WCN3990 firmware-5.bin)
# ---------------------------------------------------------------------------
FW_OUT="$WORK_DIR/fw_out"
mkdir -p "$FW_OUT/qcom" "$FW_OUT/ath10k/WCN3990/hw1.0"

python3 "$REPO_ROOT/scripts/extract_fw_ci.py" "$WORK_DIR/fw_work" "$FW_OUT"

LFW_GITLAB="https://gitlab.com/kernel-firmware/linux-firmware/-/raw/main"
[ -f "$FW_OUT/qcom/a650_sqe.fw" ] || curl -fsSL "$LFW_GITLAB/qcom/a650_sqe.fw" -o "$FW_OUT/qcom/a650_sqe.fw"
[ -f "$FW_OUT/qcom/a650_gmu.bin" ] || curl -fsSL "$LFW_GITLAB/qcom/a650_gmu.bin" -o "$FW_OUT/qcom/a650_gmu.bin"
curl -fsSL "$LFW_GITLAB/ath10k/WCN3990/hw1.0/firmware-5.bin" -o "$FW_OUT/ath10k/WCN3990/hw1.0/firmware-5.bin"

tar -C "$FW_OUT" -czf "$ARTIFACTS_DIR/redfin-firmware.tar.gz" .

# ---------------------------------------------------------------------------
# 2. Clone Linux kernel & apply SM7250 + Redfin patches
# ---------------------------------------------------------------------------
LINUX_SRC="$WORK_DIR/linux"
if [ ! -d "$LINUX_SRC" ]; then
    git clone --depth 1 --branch "$LINUX_TAG" https://github.com/torvalds/linux.git "$LINUX_SRC"
fi

cd "$LINUX_SRC"
for p in "$REPO_ROOT"/avicii-mainline/patches/*.patch; do
    git apply --index "$p"
done

# Add Google Pixel 5 (sm7250-google-redfin) Device Tree
cp "$REPO_ROOT/kernel/sm7250-google-redfin.dts" arch/arm64/boot/dts/qcom/sm7250-google-redfin.dts
if ! grep -q "sm7250-google-redfin.dtb" arch/arm64/boot/dts/qcom/Makefile; then
    echo "dtb-\$(CONFIG_ARCH_QCOM) += sm7250-google-redfin.dtb" >> arch/arm64/boot/dts/qcom/Makefile
fi

# Add Samsung S6E3HC2 DSC AMOLED panel driver
cp "$REPO_ROOT/kernel/panel-samsung-s6e3hc2.c" drivers/gpu/drm/panel/panel-samsung-s6e3hc2.c
if ! grep -q "DRM_PANEL_SAMSUNG_S6E3HC2" drivers/gpu/drm/panel/Kconfig; then
    python3 - <<'PY'
p = "drivers/gpu/drm/panel/Kconfig"
txt = open(p).read()
entry = """config DRM_PANEL_SAMSUNG_S6E3HC2
\ttristate "Samsung S6E3HC2 DSI command mode panel"
\tdepends on OF
\tdepends on DRM_MIPI_DSI
\tdepends on BACKLIGHT_CLASS_DEVICE
\tselect DRM_DISPLAY_DSC_HELPER
\tselect DRM_DISPLAY_HELPER
\thelp
\t  Say Y here if you want to enable support for the Samsung S6E3HC2
\t  1080x2340@90Hz DSC AMOLED panel found in the Google Pixel 5 (redfin).

"""
idx = txt.rfind("endmenu")
open(p, "w").write(txt[:idx] + entry + txt[idx:])
PY
fi
if ! grep -q "panel-samsung-s6e3hc2.o" drivers/gpu/drm/panel/Makefile; then
    echo 'obj-$(CONFIG_DRM_PANEL_SAMSUNG_S6E3HC2) += panel-samsung-s6e3hc2.o' >> drivers/gpu/drm/panel/Makefile
fi

# ---------------------------------------------------------------------------
# 3. Configure and build Kernel + DTB
# ---------------------------------------------------------------------------
OUT_DIR="$WORK_DIR/out"
make O="$OUT_DIR" ARCH=arm64 CROSS_COMPILE="ccache aarch64-linux-gnu-" defconfig
ARCH=arm64 ./scripts/kconfig/merge_config.sh -m -O "$OUT_DIR" "$OUT_DIR/.config" \
    "$REPO_ROOT"/avicii-mainline/config/avicii*.config \
    "$REPO_ROOT/config/redfin.config"
make O="$OUT_DIR" ARCH=arm64 CROSS_COMPILE="ccache aarch64-linux-gnu-" olddefconfig
make O="$OUT_DIR" ARCH=arm64 CROSS_COMPILE="ccache aarch64-linux-gnu-" -j"$(nproc)" \
    Image.gz qcom/sm7250-google-redfin.dtb

cp "$OUT_DIR/arch/arm64/boot/Image.gz" "$ARTIFACTS_DIR/Image.gz"
cp "$OUT_DIR/arch/arm64/boot/dts/qcom/sm7250-google-redfin.dtb" "$ARTIFACTS_DIR/sm7250-google-redfin.dtb"

# ---------------------------------------------------------------------------
# 4. Build static helper tools & Alpine aarch64 initramfs with firmware
# ---------------------------------------------------------------------------
aarch64-linux-gnu-gcc -O2 -static -I"$LINUX_SRC/include/uapi" -I/usr/include/libdrm \
    -o "$WORK_DIR/drmtext" "$REPO_ROOT/avicii-mainline/tools/drmtext.c"
aarch64-linux-gnu-gcc -O2 -static \
    -o "$WORK_DIR/rebootto" "$REPO_ROOT/avicii-mainline/tools/rebootto.c"

INIT_ROOT="$WORK_DIR/initramfs_root"
sudo rm -rf "$INIT_ROOT" && mkdir -p "$INIT_ROOT"

ALPINE_VER="3.21.3"
ALPINE_TAR="$WORK_DIR/alpine-minirootfs-${ALPINE_VER}-aarch64.tar.gz"
curl -fsSL "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/aarch64/alpine-minirootfs-${ALPINE_VER}-aarch64.tar.gz" \
    -o "$ALPINE_TAR"
sudo tar -xzf "$ALPINE_TAR" -C "$INIT_ROOT"

sudo cp /etc/resolv.conf "$INIT_ROOT/etc/resolv.conf"
sudo tee "$INIT_ROOT/etc/apk/repositories" >/dev/null <<EOF
http://dl-cdn.alpinelinux.org/alpine/v3.21/main
http://dl-cdn.alpinelinux.org/alpine/v3.21/community
http://dl-cdn.alpinelinux.org/alpine/edge/main
http://dl-cdn.alpinelinux.org/alpine/edge/community
http://dl-cdn.alpinelinux.org/alpine/edge/testing
EOF

sudo chroot "$INIT_ROOT" /sbin/apk update
sudo chroot "$INIT_ROOT" /sbin/apk add --no-cache \
    busybox-extras \
    rmtfs \
    pd-mapper \
    qrtr \
    iw \
    wpa_supplicant \
    iproute2 \
    util-linux-misc || true

sudo rm -rf "$INIT_ROOT/var/cache/apk"/*

# Install Redfin GPU & Wi-Fi firmware into initramfs
sudo mkdir -p "$INIT_ROOT/lib/firmware"
sudo cp -a "$FW_OUT"/* "$INIT_ROOT/lib/firmware/"

# Install init and helper tools
sudo install -m 755 "$REPO_ROOT/initramfs/init" "$INIT_ROOT/init"
sudo install -m 755 "$WORK_DIR/drmtext" "$WORK_DIR/rebootto" "$REPO_ROOT/avicii-mainline/initramfs/flashpart" "$INIT_ROOT/usr/bin/"

(cd "$INIT_ROOT" && sudo find . | sudo cpio -o -H newc -R 0:0 | gzip -9) > "$ARTIFACTS_DIR/initramfs.cpio.gz"

# ---------------------------------------------------------------------------
# 5. Pack boot-redfin.img (Android Boot Image Header v2)
# ---------------------------------------------------------------------------
CMDLINE="clk_ignore_unused pd_ignore_unused regulator_ignore_unused watchdog.handle_boot_enabled=0 redfin.boot=pmos console=tty0 fbcon=font:TER16x32 loglevel=8 ignore_loglevel printk.time=1 printk.devkmsg=on rdinit=/init panic=5"

mkbootimg \
    --header_version 2 \
    --kernel "$ARTIFACTS_DIR/Image.gz" \
    --ramdisk "$ARTIFACTS_DIR/initramfs.cpio.gz" \
    --dtb "$ARTIFACTS_DIR/sm7250-google-redfin.dtb" \
    --pagesize 4096 \
    --base 0x0 \
    --kernel_offset 0x8000 \
    --ramdisk_offset 0x1000000 \
    --tags_offset 0x100 \
    --dtb_offset 0x1f00000 \
    --cmdline "$CMDLINE" \
    -o "$ARTIFACTS_DIR/boot-redfin.img"

ls -lh "$ARTIFACTS_DIR"
