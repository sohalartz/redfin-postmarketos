#!/bin/sh
set -e
mkdir -p /var/run/wpa_supplicant

for i in $(seq 1 15); do
    [ -d /sys/class/net/wlan0 ] && break
    sleep 1
done

if [ -d /sys/class/net/wlan0 ]; then
    ifconfig wlan0 up 2>/dev/null || true
    if [ -f /etc/wpa_supplicant.conf ]; then
        killall wpa_supplicant 2>/dev/null || true
        wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant.conf
        sleep 2
        killall udhcpc 2>/dev/null || true
        udhcpc -i wlan0 -b -n -s /usr/share/udhcpc/default.script
    fi
fi
