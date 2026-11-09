#!/usr/bin/env python3
import os
import sys
import gzip
import shutil
import urllib.request
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = "/tmp/ubuntu-build"
ROOTFS = os.path.join(WORK_DIR, "rootfs")
DEBS_DIR = os.path.join(WORK_DIR, "debs")
ARTIFACTS_DIR = os.path.join(REPO_ROOT, "artifacts")
BASE_URL = "http://ports.ubuntu.com/ubuntu-ports/"

os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(DEBS_DIR, exist_ok=True)
os.makedirs(ARTIFACTS_DIR, exist_ok=True)

os.environ["PATH"] = f"/home/prabh/.local/root_pkgs/wrappers:/home/prabh/.local/root_pkgs/sysroot/usr/bin:{os.environ.get('PATH', '')}"
os.environ["LD_LIBRARY_PATH"] = f"/home/prabh/.local/root_pkgs/sysroot/usr/lib/x86_64-linux-gnu:{os.environ.get('LD_LIBRARY_PATH', '')}"

# 1. Download Ubuntu 24.04 Base ARM64
UBUNTU_TAR = os.path.join(WORK_DIR, "ubuntu-base.tar.gz")
if not os.path.exists(UBUNTU_TAR):
    print("Downloading Ubuntu 24.04 Base ARM64...")
    url = "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-arm64.tar.gz"
    urllib.request.urlretrieve(url, UBUNTU_TAR)

# 2. Index packages and resolve dependencies
print("Fetching package indices from Ubuntu Noble...")
PACKAGES_INDEX = {}
for comp in ['main', 'universe']:
    url = f"{BASE_URL}dists/noble/{comp}/binary-arm64/Packages.gz"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as resp:
        data = gzip.decompress(resp.read()).decode('utf-8', errors='ignore')
    for block in data.split('\n\n'):
        pkg, fn, deps = None, None, []
        for line in block.split('\n'):
            if line.startswith('Package: '):
                pkg = line.split(': ')[1].strip()
            elif line.startswith('Filename: '):
                fn = line.split(': ')[1].strip()
            elif line.startswith('Depends: '):
                for d in line.split(': ')[1].split(','):
                    d_name = d.strip().split(' ')[0].split(':')[0]
                    if d_name:
                        deps.append(d_name)
        if pkg and fn:
            PACKAGES_INDEX[pkg] = (fn, deps)

target_pkgs = [
    'wpasupplicant',
    'iw',
    'dropbear-bin',
    'busybox-static',
    'rmtfs',
    'tqftpserv',
    'qrtr-tools',
    'iproute2',
    'net-tools',
    'libqmi-glib5',
]

resolved = set()
queue = list(target_pkgs)
while queue:
    p = queue.pop(0)
    if p in resolved:
        continue
    if p in PACKAGES_INDEX:
        resolved.add(p)
        fn, deps = PACKAGES_INDEX[p]
        for d in deps:
            if d not in resolved and d in PACKAGES_INDEX:
                queue.append(d)

print(f"Resolved {len(resolved)} packages to download.")

# 3. Download .deb packages
for pkg in sorted(list(resolved)):
    fn, _ = PACKAGES_INDEX[pkg]
    deb_name = os.path.basename(fn)
    dest = os.path.join(DEBS_DIR, deb_name)
    if not os.path.exists(dest):
        print(f"Downloading {deb_name}...")
        url = BASE_URL + fn
        urllib.request.urlretrieve(url, dest)

# 4. Extract rootfs and packages
print(f"Extracting Ubuntu base to {ROOTFS}...")
subprocess.run(f"chmod -R 777 {ROOTFS} 2>/dev/null || true; rm -rf {ROOTFS}", shell=True)
os.makedirs(ROOTFS, exist_ok=True)
subprocess.run(["tar", "-xzf", UBUNTU_TAR, "-C", ROOTFS], check=True)

print("Extracting downloaded .deb packages into rootfs...")
for deb in os.listdir(DEBS_DIR):
    if deb.endswith('.deb'):
        deb_path = os.path.join(DEBS_DIR, deb)
        subprocess.run(["dpkg", "-x", deb_path, ROOTFS], check=True)

# Dynamic Linker & Libraries Configuration
print("Configuring dynamic linker and ldconfig...")
os.makedirs(os.path.join(ROOTFS, "lib"), exist_ok=True)
ld_link = os.path.join(ROOTFS, "lib/ld-linux-aarch64.so.1")
if not os.path.exists(ld_link):
    os.symlink("/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1", ld_link)
lib_arch_link = os.path.join(ROOTFS, "lib/aarch64-linux-gnu")
if not os.path.exists(lib_arch_link):
    os.symlink("/usr/lib/aarch64-linux-gnu", lib_arch_link)

proot_bin = "/home/prabh/.local/root_pkgs/sysroot/usr/bin/proot"
qemu_bin = "/home/prabh/.local/root_pkgs/sysroot/usr/bin/qemu-aarch64-static"
subprocess.run([proot_bin, "-0", "-r", ROOTFS, "-q", qemu_bin, "/usr/sbin/ldconfig.real"], check=True)

# 5. Copy firmware and utilities
print("Installing firmware, utilities, and configs...")
os.makedirs(os.path.join(ROOTFS, "lib/firmware"), exist_ok=True)
fw_tar = os.path.join(ARTIFACTS_DIR, "redfin-firmware.tar.gz")
if os.path.exists(fw_tar):
    subprocess.run(["tar", "-xzf", fw_tar, "-C", os.path.join(ROOTFS, "lib/firmware")], check=True)

# Static binaries
for bin_name in ["serverui", "pd-mapper", "powerbuttond", "rebootto"]:
    bin_path = f"/tmp/redfin-build/{bin_name}"
    if os.path.exists(bin_path):
        shutil.copy2(bin_path, os.path.join(ROOTFS, "usr/bin", bin_name))
        os.chmod(os.path.join(ROOTFS, "usr/bin", bin_name), 0o755)

# Busybox symlinks
busybox_target = "/usr/bin/busybox"
for link in ["udhcpd", "udhcpc", "telnetd", "killall"]:
    target = os.path.join(ROOTFS, "usr/bin", link)
    if not os.path.exists(target):
        os.symlink(busybox_target, target)

# Wi-Fi config
os.makedirs(os.path.join(ROOTFS, "etc/wpa_supplicant"), exist_ok=True)
with open(os.path.join(ROOTFS, "etc/wpa_supplicant.conf"), "w") as f:
    f.write('''ctrl_interface=/var/run/wpa_supplicant
update_config=1
network={
    ssid="PUADHIYE"
    psk="kinni vari dasa?"
}
''')

# SSH keys
os.makedirs(os.path.join(ROOTFS, "etc/dropbear"), exist_ok=True)
os.makedirs(os.path.join(ROOTFS, "root/.ssh"), mode=0o700, exist_ok=True)
shutil.copy2(os.path.join(REPO_ROOT, "initramfs/dropbear_ed25519_host_key"),
             os.path.join(ROOTFS, "etc/dropbear/dropbear_ed25519_host_key"))
os.chmod(os.path.join(ROOTFS, "etc/dropbear/dropbear_ed25519_host_key"), 0o600)

shutil.copy2(os.path.join(REPO_ROOT, "initramfs/authorized_keys"),
             os.path.join(ROOTFS, "root/.ssh/authorized_keys"))
os.chmod(os.path.join(ROOTFS, "root/.ssh/authorized_keys"), 0o600)

# 6. Install /init script
init_script = """#!/bin/sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev
mkdir -p /dev/pts /dev/shm /run /tmp
mount -t devpts devpts /dev/pts
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /tmp

echo "=============================================" > /dev/kmsg
echo "Google Pixel 5 - Ubuntu 24.04 LTS (Mainline)" > /dev/kmsg
echo "=============================================" > /dev/kmsg

# --- USB Gadget Network (CDC ECM + RNDIS for telnet 172.16.42.1) ---
setup_usb() {
    G=/sys/kernel/config/usb_gadget/g1
    if [ -d /sys/kernel/config ] && [ ! -d $G ]; then
        mkdir -p $G
        echo 0x1d6b > $G/idVendor
        echo 0x0104 > $G/idProduct
        mkdir -p $G/strings/0x409
        echo "15171FDD40000K" > $G/strings/0x409/serialnumber
        echo "Google" > $G/strings/0x409/manufacturer
        echo "Pixel 5 Ubuntu Server" > $G/strings/0x409/product
        mkdir -p $G/configs/c.1/strings/0x409
        echo "CDC ECM + RNDIS" > $G/configs/c.1/strings/0x409/configuration
        mkdir -p $G/functions/ecm.usb0
        ln -sf $G/functions/ecm.usb0 $G/configs/c.1/
        mkdir -p $G/functions/rndis.usb0
        ln -sf $G/functions/rndis.usb0 $G/configs/c.1/
        UDC=$(ls /sys/class/udc 2>/dev/null | head -n 1)
        [ -n "$UDC" ] && echo "$UDC" > $G/UDC
    fi
    ifconfig lo 127.0.0.1 up
    i=0
    while [ $i -lt 10 ] && ! ifconfig usb0 >/dev/null 2>&1; do sleep 1; i=$((i + 1)); done
    ifconfig usb0 172.16.42.1 netmask 255.255.255.0 up 2>/dev/null

    cat > /etc/udhcpd.conf <<EOF
start 172.16.42.2
end 172.16.42.2
interface usb0
option subnet 255.255.255.0
option lease 86400
lease_file /run/udhcpd.leases
EOF
    touch /run/udhcpd.leases
    busybox udhcpd /etc/udhcpd.conf
    busybox telnetd -l /bin/sh
}
setup_usb &

# --- Start Dropbear SSH Daemon ---
if [ -x /usr/sbin/dropbear ] || [ -x /usr/bin/dropbear ]; then
    mkdir -p /etc/dropbear /root/.ssh
    chmod 700 /root/.ssh
    dropbear -r /etc/dropbear/dropbear_ed25519_host_key -B -p 22 2>/dev/null
    echo "redfin-init: dropbear SSH daemon active on port 22" > /dev/kmsg
fi

# --- Hardware: Populate partition labels ---
populate_partlabels() {
    i=0
    while [ $i -lt 15 ]; do
        ls /sys/class/block/sd* >/dev/null 2>&1 && break
        sleep 1; i=$((i + 1))
    done
    mkdir -p /dev/disk/by-partlabel
    for u in /sys/class/block/sd*/uevent; do
        [ -e "$u" ] || continue
        dev=$(basename "$(dirname "$u")")
        part=$(awk -F= '/^PARTNAME=/{print $2}' "$u")
        [ -n "$part" ] && ln -sf "/dev/$dev" "/dev/disk/by-partlabel/$part"
    done
}
populate_partlabels

# --- Hardware: Qualcomm MPSS & WCN3990 Wi-Fi ---
setup_wifi() {
    mkdir -p /mnt/modem /lib/firmware/qcom/sm7250/google/redfin /lib/firmware/ath10k/WCN3990/hw1.0
    for slot in modem_a modem_b; do
        if [ -b "/dev/disk/by-partlabel/$slot" ]; then
            if mount -t vfat -o ro "/dev/disk/by-partlabel/$slot" /mnt/modem 2>/dev/null; then
                echo "redfin-init: mounted $slot at /mnt/modem" > /dev/kmsg
                break
            fi
        fi
    done
    if [ -d /mnt/modem/image ]; then
        for f in /mnt/modem/image/*; do
            [ -e "$f" ] || continue
            base=$(basename "$f")
            [ -e "/lib/firmware/qcom/sm7250/google/redfin/$base" ] || ln -sf "$f" "/lib/firmware/qcom/sm7250/google/redfin/$base"
        done
        [ -e /mnt/modem/image/wlanmdsp.mbn ] && ln -sf /mnt/modem/image/wlanmdsp.mbn /lib/firmware/ath10k/WCN3990/hw1.0/wlanmdsp.mbn
    fi

    mkdir -p /readonly/vendor /readonly /var/lib/tqftpserv /run
    ln -sf /mnt/modem /readonly/vendor/firmware_mnt 2>/dev/null
    ln -sf /mnt/modem /readonly/firmware 2>/dev/null

    # Boot MPSS remoteproc
    for r in /sys/class/remoteproc/remoteproc*; do
        [ -e "$r/name" ] || continue
        rname=$(cat "$r/name" 2>/dev/null)
        case "$rname" in
            *mpss*|*modem*)
                state=$(cat "$r/state" 2>/dev/null)
                if [ "$state" != "running" ]; then
                    echo "redfin-init: starting remoteproc $rname" > /dev/kmsg
                    echo start > "$r/state" 2>/dev/null
                fi
                ;;
        esac
    done
    sleep 1

    command -v tqftpserv >/dev/null 2>&1 && tqftpserv -d >/run/tqftpserv.log 2>&1 &
    command -v pd-mapper >/dev/null 2>&1 && pd-mapper >/run/pdmapper.log 2>&1 &
    command -v rmtfs >/dev/null 2>&1 && rmtfs -r -P -s >/run/rmtfs.log 2>&1 &

    w=0
    while [ $w -lt 8 ] && [ ! -d /sys/class/net/wlan0 ]; do
        sleep 1; w=$((w + 1))
    done
    if [ ! -d /sys/class/net/wlan0 ] && command -v pd-mapper >/dev/null 2>&1; then
        killall pd-mapper 2>/dev/null
        sleep 1
        pd-mapper &
    fi
    w=0
    while [ $w -lt 10 ] && [ ! -d /sys/class/net/wlan0 ]; do
        sleep 1; w=$((w + 1))
    done

    if [ -d /sys/class/net/wlan0 ]; then
        ifconfig wlan0 up 2>/dev/null
        echo "redfin-init: wlan0 is UP" > /dev/kmsg
        mkdir -p /var/run/wpa_supplicant
        wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant.conf 2>/dev/null
        busybox udhcpc -i wlan0 -b -n 2>/dev/null &
    fi
}
setup_wifi &

# --- OLED Panel GUI ---
start_display() {
    i=0
    while [ $i -lt 15 ] && [ ! -e /dev/dri/card0 ]; do
        sleep 1; i=$((i + 1))
    done
    # Turn on backlight
    for b in /sys/class/backlight/*; do
        [ -e "$b/max_brightness" ] && cat "$b/max_brightness" > "$b/brightness" 2>/dev/null
    done
    if [ -x /usr/bin/serverui ]; then
        echo "redfin-init: starting Ubuntu serverui GUI on OLED panel" > /dev/kmsg
        /usr/bin/serverui &
    fi
}
start_display &

# Keep an interactive root shell on serial/console
exec /bin/sh
"""

with open(os.path.join(ROOTFS, "init"), "w") as f:
    f.write(init_script)
os.chmod(os.path.join(ROOTFS, "init"), 0o755)

# 7. Strip docs/locales to optimize image size
print("Optimizing rootfs size...")
for d in ["usr/share/man", "usr/share/doc", "usr/share/locale", "var/cache/apt"]:
    shutil.rmtree(os.path.join(ROOTFS, d), ignore_errors=True)

# 8. Repack initramfs.cpio.gz
print("Packaging Ubuntu initramfs...")
cpio_out = os.path.join(ARTIFACTS_DIR, "initramfs-ubuntu2404.cpio.gz")
cmd = f"(cd {ROOTFS} && find . | cpio -o -H newc -R 0:0 | gzip -9) > {cpio_out}"
subprocess.run(cmd, shell=True, check=True)
print(f"Created {cpio_out} ({os.path.getsize(cpio_out) // (1024*1024)} MB)")

# 9. mkbootimg
print("Building Ubuntu 24.04 boot image...")
boot_out = os.path.join(ARTIFACTS_DIR, "boot-ubuntu2404.img")
kernel_gz = os.path.join(ARTIFACTS_DIR, "Image.gz")
dtb_file = os.path.join(ARTIFACTS_DIR, "sm7250-google-redfin.dtb")

cmdline = "clk_ignore_unused pd_ignore_unused regulator_ignore_unused watchdog.handle_boot_enabled=0 redfin.boot=ubuntu console=tty0 fbcon=font:TER16x32 loglevel=8 ignore_loglevel printk.time=1 printk.devkmsg=on rdinit=/init panic=5"

mkboot_cmd = [
    "mkbootimg",
    "--header_version", "2",
    "--os_version", "11.0.0",
    "--os_patch_level", "2026-06",
    "--kernel", kernel_gz,
    "--ramdisk", cpio_out,
    "--dtb", dtb_file,
    "--pagesize", "4096",
    "--base", "0x0",
    "--kernel_offset", "0x8000",
    "--ramdisk_offset", "0x1000000",
    "--tags_offset", "0x100",
    "--dtb_offset", "0x1f00000",
    "--cmdline", cmdline,
    "-o", boot_out
]
subprocess.run(mkboot_cmd, check=True)
print(f"SUCCESS! Built Ubuntu 24.04 boot image: {boot_out} ({os.path.getsize(boot_out) // (1024*1024)} MB)")
