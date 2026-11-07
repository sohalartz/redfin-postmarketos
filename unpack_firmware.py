import os
import struct

RADIO_PATH = r"C:\Users\Prabh\.gemini\antigravity\scratch\redfin-postmarketos\stock-rq3a\radio.img"
OUT_FW_DIR = r"C:\Users\Prabh\.gemini\antigravity\scratch\redfin-postmarketos\firmware"

with open(RADIO_PATH, "rb") as f:
    data = f.read()

print(f"radio.img size: {len(data)} bytes")
print("First 256 bytes:", data[:256])

# Search for FAT16 ("FAT16   ") or ELF or partition names inside radio.img
for needle in [b"FAT16", b"MSDOS5.0", b"modem", b"wlanmdsp.mbn", b"BDWLAN", b"ADSP", b"CDSP"]:
    pos = 0
    found = []
    while len(found) < 5:
        idx = data.find(needle, pos)
        if idx < 0:
            break
        found.append(hex(idx))
        pos = idx + 1
    print(f"Needle {needle}: {found}")
