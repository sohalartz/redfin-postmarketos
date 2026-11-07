import os
import struct
import urllib.request
import zlib

URL = "https://dl.google.com/dl/android/aosp/redfin-rq3a.211001.001-factory-23f4cec2.zip"
OUT_DIR = r"C:\Users\Prabh\.gemini\antigravity\scratch\redfin-postmarketos\stock-rq3a"

def get_range(start, end):
    req = urllib.request.Request(URL)
    req.add_header("Range", f"bytes={start}-{end}")
    with urllib.request.urlopen(req, timeout=120) as resp:
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
    _, _, _, _, total_entries, cd_size, cd_offset, _ = struct.unpack("<4sHHHHIIH", tail[eocd_pos:eocd_pos+22])
    cd = get_range(offset_base + cd_offset, offset_base + cd_offset + cd_size - 1)
    pos = 0
    entries = []
    for _ in range(total_entries):
        sig, _, _, _, method, _, _, crc, comp_size, uncomp_size, name_len, extra_len, comment_len, _, _, _, lfh_offset = struct.unpack(
            "<4sHHHHHHIIIHHHHHII", cd[pos:pos+46]
        )
        name = cd[pos+46:pos+46+name_len].decode("utf-8", errors="replace")
        entries.append((name, method, comp_size, uncomp_size, lfh_offset))
        pos += 46 + name_len + extra_len + comment_len
    return entries

def data_offset(offset_base, lfh_offset):
    lfh = get_range(offset_base + lfh_offset, offset_base + lfh_offset + 29)
    _, _, _, _, _, _, _, _, _, name_len, extra_len = struct.unpack("<4sHHHHHIIIHH", lfh)
    return offset_base + lfh_offset + 30 + name_len + extra_len

def extract_entry(offset_base, entry, out_path):
    name, method, csize, usize, lfh_off = entry
    start = data_offset(offset_base, lfh_off)
    print(f"Downloading {name} ({csize / (1024*1024):.2f} MB compressed -> {usize / (1024*1024):.2f} MB)...")
    raw = get_range(start, start + csize - 1)
    if method == 0:
        data = raw
    elif method == 8:
        data = zlib.decompress(raw, -zlib.MAX_WBITS)
    else:
        raise RuntimeError(f"Unsupported zip method {method}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"Saved {out_path} ({len(data)} bytes)")

if __name__ == "__main__":
    total = get_size()
    top_entries = list_zip(0, total)
    for name, method, csize, usize, off in top_entries:
        print(f"[top] {name}: method={method} comp={csize} uncomp={usize}")
        if "radio-redfin" in name:
            radio_path = os.path.join(OUT_DIR, "radio.img")
            if not os.path.exists(radio_path):
                extract_entry(0, (name, method, csize, usize, off), radio_path)
        elif name.endswith("image-redfin-rq3a.211001.001.zip") and method == 0:
            inner_base = data_offset(0, off)
            inner_entries = list_zip(inner_base, usize)
            for iname, imethod, icsize, iusize, ioff in inner_entries:
                print(f"  [inner] {iname}: method={imethod} comp={icsize} uncomp={iusize}")
