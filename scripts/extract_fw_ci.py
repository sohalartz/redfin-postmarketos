#!/usr/bin/env python3
"""
Extract Google Pixel 5 (redfin) firmware blobs needed for Mainline Linux / postmarketOS
directly from Google's official RQ3A.211001.001 factory image using HTTP Range requests:
  1. radio.img -> modem.img (FAT16) -> bdwlan.* -> ath10k/WCN3990/hw1.0/redfin/board-2.bin
  2. vendor.img -> firmware/a620_zap.* -> qcom/sm7250/google/redfin/a620_zap.*
"""
import os
import subprocess
import struct
import sys
import urllib.request
import zlib

URL = "https://dl.google.com/dl/android/aosp/redfin-rq3a.211001.001-factory-23f4cec2.zip"


def get_range(start, end):
    req = urllib.request.Request(URL)
    req.add_header("Range", f"bytes={start}-{end}")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def get_size():
    req = urllib.request.Request(URL, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.headers["Content-Length"])


def list_zip(offset_base, size):
    tail_size = min(65536, size)
    tail = get_range(offset_base + size - tail_size, offset_base + size - 1)
    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos < 0:
        raise RuntimeError("EOCD not found")
    _, _, _, _, total_entries, cd_size, cd_offset, _ = struct.unpack(
        "<4sHHHHIIH", tail[eocd_pos : eocd_pos + 22]
    )
    cd = get_range(offset_base + cd_offset, offset_base + cd_offset + cd_size - 1)
    pos = 0
    entries = []
    for _ in range(total_entries):
        (
            sig,
            _,
            _,
            _,
            method,
            _,
            _,
            crc,
            comp_size,
            uncomp_size,
            name_len,
            extra_len,
            comment_len,
            _,
            _,
            _,
            lfh_offset,
        ) = struct.unpack("<4sHHHHHHIIIHHHHHII", cd[pos : pos + 46])
        name = cd[pos + 46 : pos + 46 + name_len].decode("utf-8", errors="replace")
        entries.append((name, method, comp_size, uncomp_size, lfh_offset))
        pos += 46 + name_len + extra_len + comment_len
    return entries


def data_offset(offset_base, lfh_offset):
    lfh = get_range(offset_base + lfh_offset, offset_base + lfh_offset + 29)
    _, _, _, _, _, _, _, _, _, name_len, extra_len = struct.unpack("<4sHHHHHIIIHH", lfh)
    return offset_base + lfh_offset + 30 + name_len + extra_len


def extract_entry_streaming(offset_base, entry, out_path):
    name, method, csize, usize, lfh_off = entry
    start = data_offset(offset_base, lfh_off)
    print(f"Downloading {name} ({csize / (1024*1024):.1f} MB -> {usize / (1024*1024):.1f} MB)...")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    chunk_size = 16 * 1024 * 1024
    decomp = zlib.decompressobj(-zlib.MAX_WBITS) if method == 8 else None
    with open(out_path, "wb") as f:
        pos = 0
        while pos < csize:
            end = min(pos + chunk_size - 1, csize - 1)
            chunk = get_range(start + pos, start + end)
            if method == 0:
                f.write(chunk)
            elif method == 8:
                f.write(decomp.decompress(chunk))
            else:
                raise RuntimeError(f"Unsupported zip compression method {method}")
            pos += len(chunk)
        if decomp:
            f.write(decomp.flush())
    print(f"Saved {out_path} ({os.path.getsize(out_path)} bytes)")


def carve_modem_fat(radio_path, modem_out_path):
    data = open(radio_path, "rb").read()
    # Scan 512-byte aligned offsets for FAT16 boot sector ("FAT16   " at +54 and 0x55AA at +510)
    pos = 0
    while True:
        idx = data.find(b"FAT16   ", pos)
        if idx < 0:
            raise RuntimeError("FAT16 modem image not found inside radio.img")
        boot_off = idx - 54
        if boot_off >= 0 and data[boot_off + 510 : boot_off + 512] == b"\x55\xaa":
            bps = struct.unpack_from("<H", data, boot_off + 11)[0]
            tot16 = struct.unpack_from("<H", data, boot_off + 19)[0]
            tot32 = struct.unpack_from("<I", data, boot_off + 32)[0]
            total_sectors = tot16 if tot16 != 0 else tot32
            fat_size = total_sectors * bps
            if bps in (512, 4096) and fat_size > 10 * 1024 * 1024:
                print(f"Found FAT16 modem.img at offset 0x{boot_off:x}, size {fat_size} bytes")
                with open(modem_out_path, "wb") as f:
                    f.write(data[boot_off : boot_off + fat_size])
                return
        pos = idx + 8


def main():
    work_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "fw_work")
    fw_root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "fw_out")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mkboard2 = os.path.join(repo_root, "avicii-mainline", "tools", "mkboard2.py")

    os.makedirs(work_dir, exist_ok=True)
    qcom_dir = os.path.join(fw_root, "qcom", "sm7250", "google", "redfin")
    ath_redfin_dir = os.path.join(fw_root, "ath10k", "WCN3990", "hw1.0", "redfin")
    os.makedirs(qcom_dir, exist_ok=True)
    os.makedirs(ath_redfin_dir, exist_ok=True)

    total = get_size()
    top_entries = list_zip(0, total)

    radio_path = os.path.join(work_dir, "radio.img")
    vendor_sparse_path = os.path.join(work_dir, "vendor.img")

    for name, method, csize, usize, off in top_entries:
        if "radio-redfin" in name and not os.path.exists(radio_path):
            extract_entry_streaming(0, (name, method, csize, usize, off), radio_path)
        elif name.endswith("image-redfin-rq3a.211001.001.zip") and method == 0:
            inner_base = data_offset(0, off)
            inner_entries = list_zip(inner_base, usize)
            for iname, imethod, icsize, iusize, ioff in inner_entries:
                if iname == "vendor.img" and not os.path.exists(vendor_sparse_path):
                    extract_entry_streaming(
                        inner_base,
                        (iname, imethod, icsize, iusize, ioff),
                        vendor_sparse_path,
                    )

    # 1. Extract bdwlan.* from modem.img and generate board-2.bin
    modem_path = os.path.join(work_dir, "modem.img")
    carve_modem_fat(radio_path, modem_path)
    bdwlan_dir = os.path.join(work_dir, "bdwlan")
    os.makedirs(bdwlan_dir, exist_ok=True)
    subprocess.run(
        ["7z", "e", "-y", f"-o{bdwlan_dir}", modem_path, "image/bdwlan.*"],
        check=False,
    )
    board2_out = os.path.join(ath_redfin_dir, "board-2.bin")
    subprocess.run([sys.executable, mkboard2, board2_out, bdwlan_dir], check=True)

    # 2. Extract a620_zap.* from vendor.img
    vendor_raw_path = os.path.join(work_dir, "vendor.raw.img")
    subprocess.run(["simg2img", vendor_sparse_path, vendor_raw_path], check=True)
    subprocess.run(
        ["debugfs", "-R", f"rdump /firmware {work_dir}", vendor_raw_path],
        check=True,
    )
    fw_extracted = os.path.join(work_dir, "firmware")
    zap_count = 0
    if os.path.isdir(fw_extracted):
        for fn in sorted(os.listdir(fw_extracted)):
            if fn.startswith("a620_zap."):
                src_f = os.path.join(fw_extracted, fn)
                dst_f = os.path.join(qcom_dir, fn)
                with open(src_f, "rb") as sf, open(dst_f, "wb") as df:
                    df.write(sf.read())
                zap_count += 1
                print(f"Extracted GPU zap shader firmware: {dst_f}")
    if zap_count == 0:
        raise RuntimeError("Failed to extract a620_zap.* from vendor.img")


if __name__ == "__main__":
    main()
