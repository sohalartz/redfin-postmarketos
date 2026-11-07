import os
import struct

STOCK_DIR = r"C:\Users\Prabh\.gemini\antigravity\scratch\redfin-postmarketos\stock-rq3a"
UNPACK_DIR = os.path.join(STOCK_DIR, "unpacked")
os.makedirs(UNPACK_DIR, exist_ok=True)

def align(val, page_size):
    return ((val + page_size - 1) // page_size) * page_size

def parse_boot_img(path):
    with open(path, "rb") as f:
        hdr = f.read(4096)
    magic = hdr[:8]
    assert magic == b"ANDROID!", f"Bad magic: {magic}"
    # Check header_version at offset 40 (for v3) or standard v0/v1/v2
    # In boot_img_hdr_v3:
    # magic[8], kernel_size(I), ramdisk_size(I), os_version(I), header_size(I), reserved[4](4I), header_version(I), cmdline[1536]
    k_sz, r_sz, os_ver, hdr_sz, r0, r1, r2, r3, hdr_ver = struct.unpack("<9I", hdr[8:44])
    cmdline = hdr[44:44+1536].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    print(f"=== boot.img ===")
    print(f"  header_version : {hdr_ver}")
    print(f"  kernel_size    : {k_sz} bytes")
    print(f"  ramdisk_size   : {r_sz} bytes")
    print(f"  os_version     : {hex(os_ver)}")
    print(f"  header_size    : {hdr_sz}")
    print(f"  cmdline        : {cmdline}")
    page_size = 4096
    with open(path, "rb") as f:
        f.seek(page_size)
        kernel = f.read(k_sz)
        f.seek(page_size + align(k_sz, page_size))
        ramdisk = f.read(r_sz)
    with open(os.path.join(UNPACK_DIR, "boot_kernel"), "wb") as f:
        f.write(kernel)
    with open(os.path.join(UNPACK_DIR, "boot_ramdisk"), "wb") as f:
        f.write(ramdisk)
    print(f"  kernel magic   : {kernel[:16].hex()}")
    print(f"  ramdisk magic  : {ramdisk[:16].hex()}")

def parse_vendor_boot_img(path):
    with open(path, "rb") as f:
        hdr = f.read(4096)
    magic = hdr[:8]
    assert magic == b"VNDRBOOT", f"Bad vendor_boot magic: {magic}"
    # vendor_boot_img_hdr_v3:
    # magic[8], header_version(I), page_size(I), kernel_addr(I), ramdisk_addr(I),
    # vendor_ramdisk_size(I), cmdline[2048], tags_addr(I), name[16], header_size(I),
    # dtb_size(I), dtb_addr(Q)
    hdr_ver, page_sz, k_addr, r_addr, vr_sz = struct.unpack("<5I", hdr[8:28])
    cmdline = hdr[28:28+2048].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    tags_addr = struct.unpack("<I", hdr[2076:2080])[0]
    name = hdr[2080:2096].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    hdr_sz, dtb_sz, dtb_addr = struct.unpack("<IIQ", hdr[2096:2112])
    print(f"\n=== vendor_boot.img ===")
    print(f"  header_version      : {hdr_ver}")
    print(f"  page_size           : {page_sz}")
    print(f"  kernel_addr         : {hex(k_addr)}")
    print(f"  ramdisk_addr        : {hex(r_addr)}")
    print(f"  vendor_ramdisk_size : {vr_sz} bytes")
    print(f"  tags_addr           : {hex(tags_addr)}")
    print(f"  name                : {name}")
    print(f"  header_size         : {hdr_sz}")
    print(f"  dtb_size            : {dtb_sz} bytes")
    print(f"  dtb_addr            : {hex(dtb_addr)}")
    print(f"  cmdline             : {cmdline}")

    hdr_pages = align(hdr_sz, page_sz)
    vr_pages = align(vr_sz, page_sz)
    with open(path, "rb") as f:
        f.seek(hdr_pages)
        vramdisk = f.read(vr_sz)
        f.seek(hdr_pages + vr_pages)
        dtb = f.read(dtb_sz)
    with open(os.path.join(UNPACK_DIR, "vendor_ramdisk"), "wb") as f:
        f.write(vramdisk)
    with open(os.path.join(UNPACK_DIR, "vendor_dtb.bin"), "wb") as f:
        f.write(dtb)
    print(f"  vramdisk magic      : {vramdisk[:16].hex()}")
    # Split concatenated DTBs in vendor_dtb.bin
    pos = 0
    idx = 0
    while pos < len(dtb):
        if dtb[pos:pos+4] != b"\xd0\x0d\xfe\xed":
            pos += 4
            continue
        totalsize = struct.unpack(">I", dtb[pos+4:pos+8])[0]
        one_dtb = dtb[pos:pos+totalsize]
        out_f = os.path.join(UNPACK_DIR, f"base_{idx}.dtb")
        with open(out_f, "wb") as f:
            f.write(one_dtb)
        print(f"  Extracted base_{idx}.dtb ({totalsize} bytes)")
        pos += totalsize
        idx += 1

def parse_dtbo_img(path):
    with open(path, "rb") as f:
        d = f.read()
    magic, total, hsz, esz, cnt, eoff = struct.unpack(">6I", d[:24])
    assert magic == 0xD7B7AB1E
    print(f"\n=== dtbo.img ({cnt} entries) ===")
    for i in range(cnt):
        size, off, dt_id, rev, flags = struct.unpack(">5I", d[eoff + i * esz: eoff + i * esz + 20])
        fdt = d[off:off+size]
        out_f = os.path.join(UNPACK_DIR, f"dtbo_{i}.dtb")
        with open(out_f, "wb") as f:
            f.write(fdt)
        # Extract printable strings near start of FDT for board identification
        print(f"  dtbo_{i}.dtb: size={size} id={hex(dt_id)} rev={hex(rev)} flags={hex(flags)}")

if __name__ == "__main__":
    parse_boot_img(os.path.join(STOCK_DIR, "boot.img"))
    parse_vendor_boot_img(os.path.join(STOCK_DIR, "vendor_boot.img"))
    parse_dtbo_img(os.path.join(STOCK_DIR, "dtbo.img"))
