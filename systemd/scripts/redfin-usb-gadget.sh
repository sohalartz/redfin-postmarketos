#!/bin/sh
set -e

# Mount configfs if not already mounted
mountpoint -q /sys/kernel/config || mount -t configfs configfs /sys/kernel/config 2>/dev/null || true

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
    ln -sf $G/functions/ncm.usb0 $G/configs/c.1/
    mkdir -p $G/functions/acm.GS0 && ln -sf $G/functions/acm.GS0 $G/configs/c.1/
    UDC=$(ls /sys/class/udc 2>/dev/null | head -n1)
    [ -n "$UDC" ] && echo "$UDC" > $G/UDC
fi

# Configure usb0 IP
ifconfig lo 127.0.0.1 up || true
ifconfig usb0 172.16.42.1 netmask 255.255.255.0 up 2>/dev/null || true

# Setup udhcpd for host USB connection
cat > /etc/udhcpd.conf << "ENDCONF"
start 172.16.42.2
end 172.16.42.2
interface usb0
option subnet 255.255.255.0
option lease 86400
lease_file /run/udhcpd.leases
ENDCONF
touch /run/udhcpd.leases
killall udhcpd 2>/dev/null || true
udhcpd /etc/udhcpd.conf 2>/dev/null || true

# Telnet fallback on USB
busybox telnetd -l /bin/sh 2>/dev/null || true

echo "redfin-usb-gadget: USB gadget active at 172.16.42.1" > /dev/kmsg
