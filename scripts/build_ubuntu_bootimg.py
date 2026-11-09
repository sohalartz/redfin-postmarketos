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

# Fix usr-merge: ensure bin, sbin, lib are symlinks to usr/*
print("Consolidating usr-merge symlinks (bin, sbin, lib)...")
for d in ['bin', 'sbin', 'lib']:
    src = os.path.join(ROOTFS, d)
    dst = os.path.join(ROOTFS, 'usr', d)
    if os.path.exists(src) and not os.path.islink(src):
        os.makedirs(dst, exist_ok=True)
        for item in os.listdir(src):
            s = os.path.join(src, item)
            t = os.path.join(dst, item)
            if not os.path.exists(t):
                shutil.move(s, t)
        shutil.rmtree(src)
        os.symlink(f'usr/{d}', src)

# Ensure /bin/sh points to dash or bash
sh_link = os.path.join(ROOTFS, "usr/bin/sh")
if not os.path.exists(sh_link):
    os.symlink("dash", sh_link)

# Dynamic Linker & Libraries Configuration
print("Configuring dynamic linker and ldconfig...")
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

# UDHCPC default script
os.makedirs(os.path.join(ROOTFS, "usr/share/udhcpc"), exist_ok=True)
with open(os.path.join(ROOTFS, "usr/share/udhcpc/default.script"), "w") as f:
    f.write('''#!/bin/sh
[ -z "$1" ] && exit 1
case "$1" in
    deconfig)
        ip addr flush dev $interface 2>/dev/null
        ;;
    renew|bound)
        ip addr add ${ip}/${mask} dev $interface 2>/dev/null
        [ -n "$router" ] && ip route add default via ${router%% *} dev $interface 2>/dev/null
        [ -n "$dns" ] && echo "nameserver ${dns%% *}" > /etc/resolv.conf
        ;;
esac
''')
os.chmod(os.path.join(ROOTFS, "usr/share/udhcpc/default.script"), 0o755)

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

# 6. Install proven /init script and setup busybox applets
print("Installing proven /init script and busybox applets...")
shutil.copy2(os.path.join(REPO_ROOT, "initramfs/init"), os.path.join(ROOTFS, "init"))
os.chmod(os.path.join(ROOTFS, "init"), 0o755)

# Install busybox symlinks
subprocess.run([proot_bin, "-0", "-r", ROOTFS, "-q", qemu_bin, "/usr/bin/busybox", "--install", "-s", "/usr/bin"], check=True)

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
