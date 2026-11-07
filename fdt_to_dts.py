import os
import struct
import sys

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

def is_printable_str_list(val):
    if not val or val[-1] != 0:
        return False
    # Check if all non-zero bytes are printable ASCII and no empty strings except possibly single ""
    if len(val) == 1:
        return False
    parts = val[:-1].split(b"\0")
    if any(len(p) == 0 for p in parts):
        return False
    return all(all(32 <= b < 127 for b in p) for p in parts)

def format_prop_val(name, val):
    if len(val) == 0:
        return ""
    if is_printable_str_list(val) and name not in ("reg", "ranges", "interrupts", "clocks", "gpios"):
        parts = [p.decode("ascii") for p in val[:-1].split(b"\0")]
        return " = " + ", ".join(f'"{p}"' for p in parts)
    if len(val) % 4 == 0:
        ints = struct.unpack(f">{len(val)//4}I", val)
        return " = <" + " ".join(f"0x{x:x}" for x in ints) + ">"
    return " = [" + " ".join(f"{b:02x}" for b in val) + "]"

def fdt_to_dts(fdt_bytes):
    magic, totalsize, off_struct, off_strings, off_mem_rsvmap, version, last_comp_version, boot_cpuid_phys, size_strings, size_struct = struct.unpack(">10I", fdt_bytes[:40])
    assert magic == 0xD00DFEED, f"Invalid FDT magic: {hex(magic)}"
    lines = ["/dts-v1/;"]
    p = off_struct
    indent = 0
    while p < off_struct + size_struct:
        tok = struct.unpack(">I", fdt_bytes[p:p+4])[0]
        p += 4
        if tok == FDT_BEGIN_NODE:
            end = fdt_bytes.index(b"\0", p)
            name = fdt_bytes[p:end].decode("utf-8", errors="replace")
            p = (end + 4) & ~3
            if name == "" and indent == 0:
                name = "/"
            lines.append("\t" * indent + f"{name} {{")
            indent += 1
        elif tok == FDT_END_NODE:
            indent -= 1
            lines.append("\t" * indent + "};")
        elif tok == FDT_PROP:
            plen, nameoff = struct.unpack(">II", fdt_bytes[p:p+8])
            p += 8
            val = fdt_bytes[p:p+plen]
            p = (p + plen + 3) & ~3
            nend = fdt_bytes.index(b"\0", off_strings + nameoff)
            pname = fdt_bytes[off_strings + nameoff:nend].decode("utf-8", errors="replace")
            lines.append("\t" * indent + f"{pname}{format_prop_val(pname, val)};")
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            break
        else:
            raise RuntimeError(f"Unknown token {tok} at {p}")
    return "\n".join(lines) + "\n"

if __name__ == "__main__":
    unpack_dir = r"C:\Users\Prabh\.gemini\antigravity\scratch\redfin-postmarketos\stock-rq3a\unpacked"
    for fname in ("base_0.dtb", "dtbo_1.dtb"):
        in_p = os.path.join(unpack_dir, fname)
        out_p = os.path.join(unpack_dir, fname.replace(".dtb", ".dts"))
        with open(in_p, "rb") as f:
            dts = fdt_to_dts(f.read())
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(dts)
        print(f"Decompiled {fname} -> {out_p} ({len(dts.splitlines())} lines)")
