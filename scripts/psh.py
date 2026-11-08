#!/usr/bin/env python3
# Run a shell command on the phone's initramfs through busybox telnetd.
import os, socket, sys, time

HOST = os.environ.get("PHONE", "172.16.42.1")
TMO = float(os.environ.get("PSH_TIMEOUT", "15"))
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
END = "__PSH_END_%d__" % os.getpid()
BEG = "__PSH_BEG_%d__" % os.getpid()

def strip_iac(sock, data):
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == IAC and i + 1 < len(data):
            c = data[i + 1]
            if c in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                reply = WONT if c in (DO, DONT) else DONT
                sock.sendall(bytes([IAC, reply, data[i + 2]]))
                i += 3
                continue
            if c == SB:
                j = data.find(bytes([IAC, SE]), i)
                i = len(data) if j < 0 else j + 2
                continue
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)

def main():
    cmd = " ".join(sys.argv[1:]).strip()
    s = socket.create_connection((HOST, 23), timeout=10)
    s.settimeout(0.5)
    time.sleep(0.3)
    try:
        strip_iac(s, s.recv(4096))
    except socket.timeout:
        pass

    # Send command enclosed in curly braces or subshell
    payload = f"echo {BEG[:6]}''{BEG[6:]}\n{{ {cmd}\n}}\necho {END[:6]}''{END[6:]}\n"
    s.sendall(payload.encode())

    buf = b""
    deadline = time.time() + TMO
    while time.time() < deadline and END.encode() not in buf:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += strip_iac(s, chunk)
        except socket.timeout:
            continue

    text = buf.decode(errors="replace")
    if BEG in text and END in text:
        content = text.split(BEG, 1)[1].split(END, 1)[0]
        # Clean leading newline and prompt artifacts
        lines = [line for line in content.splitlines() if not line.strip().startswith("~ #")]
        print("\n".join(lines).strip())
    else:
        print(text)

if __name__ == "__main__":
    main()
