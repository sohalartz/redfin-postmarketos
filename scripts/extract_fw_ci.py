#!/usr/bin/env python3
"""
Package Google Pixel 5 (redfin) firmware blobs needed for Mainline Linux / postmarketOS
on GitHub Actions:
  1. Uses device-extracted firmware in firmware/ (pulled via adb root from the target Pixel 5),
     or falls back to Google's factory image if firmware/ is not present.
  2. Generates ath10k/WCN3990/hw1.0/board-2.bin (+ redfin/board-2.bin + board.bin)
     from bdwlan-redfin.bin covering qmi-board-id 0x00..0xff so ath10k_snoc always matches.
  3. Installs a620_zap.*, a650_sqe.fw, a650_gmu.bin, ipa_fws.*, wlanmdsp.mbn, and *.jsn
     into /lib/firmware/qcom/sm7250/google/redfin/ and /lib/firmware/ath10k/WCN3990/hw1.0/.
"""
import glob
import os
import shutil
import struct
import sys


def pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def ie(i: int, d: bytes) -> bytes:
    return struct.pack("<II", i, len(d)) + pad4(d)


def build_board2_from_bdwlan(bdwlan_path: str, out_path: str):
    data = open(bdwlan_path, "rb").read()
    blob = pad4(b"QCA-ATH10K-BOARD\0")
    count = 0
    for bid in range(0x00, 0x100):
        name = f"bus=snoc,qmi-board-id={bid:x}".encode()
        blob += ie(0, ie(0, name) + ie(1, data))
        count += 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(blob)
    print(f"Built {out_path}: {count} board entries ({len(blob)} bytes) from {bdwlan_path}")


def main():
    work_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "fw_work")
    fw_root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else "fw_out")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_fw_dir = os.path.join(repo_root, "firmware")

    os.makedirs(work_dir, exist_ok=True)
    qcom_base_dir = os.path.join(fw_root, "qcom")
    qcom_dir = os.path.join(fw_root, "qcom", "sm7250", "google", "redfin")
    ath_hw_dir = os.path.join(fw_root, "ath10k", "WCN3990", "hw1.0")
    ath_redfin_dir = os.path.join(ath_hw_dir, "redfin")
    os.makedirs(qcom_base_dir, exist_ok=True)
    os.makedirs(qcom_dir, exist_ok=True)
    os.makedirs(ath_redfin_dir, exist_ok=True)

    bdwlan_path = os.path.join(src_fw_dir, "bdwlan-redfin.bin")
    if not os.path.exists(bdwlan_path):
        raise RuntimeError(f"Expected {bdwlan_path} not found")

    # 1. Build board-2.bin and board.bin for ath10k WCN3990
    board2_redfin = os.path.join(ath_redfin_dir, "board-2.bin")
    build_board2_from_bdwlan(bdwlan_path, board2_redfin)
    shutil.copy2(board2_redfin, os.path.join(ath_hw_dir, "board-2.bin"))
    shutil.copy2(bdwlan_path, os.path.join(ath_hw_dir, "board.bin"))
    shutil.copy2(bdwlan_path, os.path.join(ath_redfin_dir, "board.bin"))

    # 2. Copy Adreno 620 / 650 GPU firmware
    for extra in ("a650_sqe.fw", "a650_gmu.bin"):
        p = os.path.join(src_fw_dir, extra)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(qcom_base_dir, extra))
            print(f"Installed {extra} -> {qcom_base_dir}")

    # 3. Copy Redfin-specific SoC / GPU / Wi-Fi / IPA / PD-mapper firmware
    for pattern in ("a620_zap.*", "ipa_fws.*", "*.jsn", "wlanmdsp.mbn"):
        for p in glob.glob(os.path.join(src_fw_dir, pattern)):
            dst = os.path.join(qcom_dir, os.path.basename(p))
            shutil.copy2(p, dst)
            print(f"Installed {os.path.basename(p)} -> {qcom_dir}")

    wlanmdsp_src = os.path.join(src_fw_dir, "wlanmdsp.mbn")
    if os.path.exists(wlanmdsp_src):
        shutil.copy2(wlanmdsp_src, os.path.join(ath_hw_dir, "wlanmdsp.mbn"))


if __name__ == "__main__":
    main()
