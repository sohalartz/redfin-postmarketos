#!/bin/sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 1. Mount essential filesystems
[ -d /proc/1 ] || mount -t proc proc /proc
[ -d /sys/kernel ] || mount -t sysfs sysfs /sys
[ -e /dev/null ] || mount -t devtmpfs dev /dev
mkdir -p /dev/pts /dev/shm /run /tmp /mnt /mnt/modem /var/run /var/lock
chmod 1777 /tmp
mountpoint -q /dev/pts || mount -t devpts -o gid=5,mode=620 devpts /dev/pts 2>/dev/null || mount -t devpts devpts /dev/pts
mountpoint -q /dev/shm || mount -t tmpfs tmpfs /dev/shm
mountpoint -q /run || mount -t tmpfs tmpfs /run
mountpoint -q /sys/kernel/config || mount -t configfs configfs /sys/kernel/config 2>/dev/null

# 2. Hardware watchdog (keeps device alive)
[ -c /dev/watchdog0 ] && watchdog -t 5 -T 30 /dev/watchdog0

hostname redfin
echo 1 > /proc/sys/kernel/sysrq 2>/dev/null

# 3. Mount persistent storage (/dev/sda19 -> /mnt)
storagedev=$(findfs LABEL=storage 2>/dev/null || echo /dev/sda19)
if [ -b "$storagedev" ] && ! mountpoint -q /mnt; then
    mount -o noatime "$storagedev" /mnt 2>/dev/null && echo "init_ubuntu: mounted 111.7GB storage at /mnt" > /dev/kmsg
fi

# 4. Populate /dev/disk/by-partlabel
mkdir -p /dev/disk/by-partlabel
for u in /sys/class/block/sd*/uevent; do
    [ -e "$u" ] || continue
    dev=$(basename "$(dirname "$u")")
    part=$(awk -F= '/^PARTNAME=/{print $2}' "$u")
    [ -n "$part" ] && ln -sf "/dev/$dev" "/dev/disk/by-partlabel/$part"
done

# 5. Configure USB gadget (NCM network + ACM serial)
G=/sys/kernel/config/usb_gadget/g1
if [ ! -d "$G" ]; then
    mkdir -p $G
    echo 0x1d6b > $G/idVendor
    echo 0x0104 > $G/idProduct
    mkdir -p $G/strings/0x409
    echo redfin-linux > $G/strings/0x409/serialnumber
    echo Google > $G/strings/0x409/manufacturer
    echo "Pixel 5 Ubuntu Server" > $G/strings/0x409/product
    mkdir -p $G/configs/c.1/strings/0x409
    echo "ncm+acm" > $G/configs/c.1/strings/0x409/configuration
    mkdir -p $G/functions/ncm.usb0
    echo 02:72:65:64:66:01 > $G/functions/ncm.usb0/host_addr
    echo 02:72:65:64:66:02 > $G/functions/ncm.usb0/dev_addr
    ln -s $G/functions/ncm.usb0 $G/configs/c.1/
    mkdir -p $G/functions/acm.GS0 && ln -s $G/functions/acm.GS0 $G/configs/c.1/
    UDC=$(ls /sys/class/udc 2>/dev/null | head -n1)
    [ -n "$UDC" ] && echo "$UDC" > $G/UDC
fi

ifconfig lo 127.0.0.1 up
ifconfig usb0 172.16.42.1 netmask 255.255.255.0 up 2>/dev/null

cat > /etc/udhcpd.conf << 'ENDCONF'
start 172.16.42.2
end 172.16.42.2
interface usb0
option subnet 255.255.255.0
option lease 86400
lease_file /run/udhcpd.leases
ENDCONF
touch /run/udhcpd.leases
udhcpd /etc/udhcpd.conf 2>/dev/null
busybox telnetd -l /bin/sh 2>/dev/null

# 6. Dropbear SSH daemon
mkdir -p /etc/dropbear /root/.ssh
chmod 700 /root /root/.ssh
chmod 600 /root/.ssh/authorized_keys 2>/dev/null
chown -R 0:0 /root
[ -e /etc/dropbear/dropbear_ed25519_host_key ] || dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key 2>/dev/null
dropbear -r /etc/dropbear/dropbear_ed25519_host_key -B -E -p 22 2>/dev/null

[ -c /dev/ttyGS0 ] && (while :; do setsid sh -c 'exec sh </dev/ttyGS0 >/dev/ttyGS0 2>&1'; sleep 1; done) &

# 7. Firmware & Wi-Fi setup
setup_firmware_and_wifi() {
    FW_DIR=/lib/firmware/qcom/sm7250/google/redfin
    ATH_DIR=/lib/firmware/ath10k/WCN3990/hw1.0
    mkdir -p "$FW_DIR" "$ATH_DIR"

    for slot in modem_a modem_b; do
        if [ -b "/dev/disk/by-partlabel/$slot" ] && mount -o ro "/dev/disk/by-partlabel/$slot" /mnt/modem 2>/dev/null; then
            echo "init_ubuntu: mounted /dev/disk/by-partlabel/$slot" > /dev/kmsg
            break
        fi
    done

    if [ -d /mnt/modem/image ]; then
        for f in /mnt/modem/image/modem* /mnt/modem/image/mba* /mnt/modem/image/adsp* /mnt/modem/image/cdsp* /mnt/modem/image/slpi*; do
            [ -e "$f" ] && ln -sf "$f" "$FW_DIR/" 2>/dev/null
        done
        for f in /mnt/modem/image/bdwlan* /mnt/modem/image/wlan*; do
            [ -e "$f" ] && ln -sf "$f" "$ATH_DIR/" 2>/dev/null
        done
        [ -e /mnt/modem/image/bdwlan.b31 ] && ln -sf /mnt/modem/image/bdwlan.b31 "$ATH_DIR/board.bin" 2>/dev/null
    fi

    for r in /sys/class/remoteproc/remoteproc*; do
        [ -e "$r/name" ] || continue
        name=$(cat "$r/name")
        case "$name" in
            *mpss*|*modem*)
                if [ -e "$r/state" ] && [ "$(cat "$r/state")" = "offline" ]; then
                    echo "init_ubuntu: starting MPSS remoteproc ($name)" > /dev/kmsg
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
    while [ $w -lt 8 ] && [ ! -d /sys/class/net/wlan0 ]; do sleep 1; w=$((w + 1)); done
    if [ ! -d /sys/class/net/wlan0 ] && command -v pd-mapper >/dev/null 2>&1; then
        killall pd-mapper 2>/dev/null
        sleep 1
        pd-mapper &
    fi
    w=0
    while [ $w -lt 10 ] && [ ! -d /sys/class/net/wlan0 ]; do sleep 1; w=$((w + 1)); done

    if [ -d /sys/class/net/wlan0 ]; then
        ifconfig wlan0 up 2>/dev/null
        echo "init_ubuntu: wlan0 is UP" > /dev/kmsg
        if [ -e /etc/wpa_supplicant.conf ]; then
            mkdir -p /var/run/wpa_supplicant
            wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant.conf 2>/dev/null
            udhcpc -i wlan0 -b -n -s /usr/share/udhcpc/default.script 2>/dev/null &
        fi
    fi
}
setup_firmware_and_wifi &

# 8. Power management utilities (reboot, poweroff, shutdown)
setup_power_commands() {
    cat << 'EOF' > /usr/local/bin/reboot
#!/bin/sh
echo "Rebooting home server..."
sync
echo s > /proc/sysrq-trigger 2>/dev/null
sleep 1
echo u > /proc/sysrq-trigger 2>/dev/null
exec /usr/bin/busybox reboot -f "$@"
EOF
    chmod 755 /usr/local/bin/reboot
    ln -sf /usr/local/bin/reboot /sbin/reboot
    ln -sf /usr/local/bin/reboot /usr/sbin/reboot
    ln -sf /usr/local/bin/reboot /usr/bin/reboot

    cat << 'EOF' > /usr/local/bin/poweroff
#!/bin/sh
echo "Powering off home server..."
for b in /sys/class/backlight/*/brightness; do [ -e "$b" ] && echo 0 > "$b" 2>/dev/null; done
sync
echo s > /proc/sysrq-trigger 2>/dev/null
sleep 1
echo u > /proc/sysrq-trigger 2>/dev/null
exec /usr/bin/busybox poweroff -f "$@"
EOF
    chmod 755 /usr/local/bin/poweroff
    ln -sf /usr/local/bin/poweroff /sbin/poweroff
    ln -sf /usr/local/bin/poweroff /usr/sbin/poweroff
    ln -sf /usr/local/bin/poweroff /usr/bin/poweroff

    cat << 'EOF' > /usr/local/bin/shutdown
#!/bin/sh
is_reboot=0
for arg in "$@"; do
    case "$arg" in
        -r|--reboot) is_reboot=1 ;;
        -h|-P|--poweroff|-H|--halt) is_reboot=0 ;;
    esac
done
if [ "$is_reboot" -eq 1 ]; then
    exec /usr/local/bin/reboot
else
    exec /usr/local/bin/poweroff
fi
EOF
    chmod 755 /usr/local/bin/shutdown
    ln -sf /usr/local/bin/shutdown /sbin/shutdown
    ln -sf /usr/local/bin/shutdown /usr/sbin/shutdown
    ln -sf /usr/local/bin/shutdown /usr/bin/shutdown
}
setup_power_commands

# 9. Interactive Server UI supervisor loop
launch_ui() {
    i=0
    while [ $i -lt 15 ] && [ ! -e /dev/dri/card0 ]; do sleep 1; i=$((i + 1)); done
    for b in /sys/class/backlight/*; do
        [ -e "$b/brightness" ] && echo 511 > "$b/brightness" 2>/dev/null
    done
    while :; do
        if [ -x /usr/bin/serverui ]; then
            echo "init_ubuntu: launching interactive serverui" > /dev/kmsg
            /usr/bin/serverui
        fi
        sleep 2
    done
}
launch_ui &

# Keep PID 1 running
while :; do sleep 3600; done
