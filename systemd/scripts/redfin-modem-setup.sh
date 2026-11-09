#!/bin/sh
set -e

FW_DIR=/lib/firmware/qcom/sm7250/google/redfin
ATH_DIR=/lib/firmware/ath10k/WCN3990/hw1.0
mkdir -p "$FW_DIR" "$ATH_DIR" /mnt/modem

# 1. Mount stock modem partition
for slot in modem_a modem_b; do
    if [ -b "/dev/disk/by-partlabel/$slot" ]; then
        if mountpoint -q /mnt/modem || mount -o ro "/dev/disk/by-partlabel/$slot" /mnt/modem 2>/dev/null; then
            echo "redfin-modem: mounted /dev/disk/by-partlabel/$slot at /mnt/modem" > /dev/kmsg
            break
        fi
    fi
done

# Fallback: check /dev/sda12 or /dev/sda13
if ! mountpoint -q /mnt/modem; then
    for dev in /dev/sda12 /dev/sda13; do
        if [ -b "$dev" ] && mount -o ro "$dev" /mnt/modem 2>/dev/null; then
            echo "redfin-modem: mounted $dev at /mnt/modem" > /dev/kmsg
            break
        fi
    done
fi

# 2. Link firmware files
if [ -d /mnt/modem/image ]; then
    for f in /mnt/modem/image/modem* /mnt/modem/image/mba* /mnt/modem/image/adsp* /mnt/modem/image/cdsp* /mnt/modem/image/slpi*; do
        [ -e "$f" ] && ln -sf "$f" "$FW_DIR/" 2>/dev/null || true
    done
    for f in /mnt/modem/image/bdwlan* /mnt/modem/image/wlan*; do
        [ -e "$f" ] && ln -sf "$f" "$ATH_DIR/" 2>/dev/null || true
    done
    [ -e /mnt/modem/image/bdwlan.b31 ] && ln -sf /mnt/modem/image/bdwlan.b31 "$ATH_DIR/board.bin" 2>/dev/null || true
fi

# 3. Boot MPSS remoteproc
for r in /sys/class/remoteproc/remoteproc*; do
    [ -e "$r/name" ] || continue
    name=$(cat "$r/name")
    case "$name" in
        *mpss*|*modem*)
            if [ -e "$r/state" ] && [ "$(cat "$r/state")" = "offline" ]; then
                echo "redfin-modem: starting MPSS remoteproc ($name)" > /dev/kmsg
                echo start > "$r/state" 2>/dev/null || true
            fi
            ;;
    esac
done

sleep 1

# 4. Start Qualcomm userspace services
command -v tqftpserv >/dev/null 2>&1 && (pgrep -x tqftpserv >/dev/null || tqftpserv -d >/run/tqftpserv.log 2>&1 &)
command -v pd-mapper >/dev/null 2>&1 && (pgrep -x pd-mapper >/dev/null || pd-mapper >/run/pdmapper.log 2>&1 &)
command -v rmtfs >/dev/null 2>&1 && (pgrep -x rmtfs >/dev/null || rmtfs -r -P -s >/run/rmtfs.log 2>&1 &)

# 5. Wait for wlan0
w=0
while [ $w -lt 10 ] && [ ! -d /sys/class/net/wlan0 ]; do
    sleep 1
    w=$((w + 1))
done

if [ ! -d /sys/class/net/wlan0 ] && command -v pd-mapper >/dev/null 2>&1; then
    killall pd-mapper 2>/dev/null || true
    sleep 1
    pd-mapper &
    sleep 2
fi

if [ -d /sys/class/net/wlan0 ]; then
    ifconfig wlan0 up 2>/dev/null || true
    echo "redfin-modem: wlan0 is UP" > /dev/kmsg
fi
