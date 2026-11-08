# Handoff Guide for Antigravity (New System Setup)

This repository (`https://github.com/sohalartz/redfin-postmarketos`) contains the complete **Mainline Linux (`v7.3-rc7`) & postmarketOS port** for the **Google Pixel 5 (`google-redfin`, Qualcomm Snapdragon 765G / `SM7250`)**, modeled after [`lazybrar/avicii-mainline`](https://github.com/lazybrar/avicii-mainline).

---

## 1. User Constraints & Rules
1. **ALL Linux compilation, firmware packaging, and initramfs/boot.img creation MUST happen on GitHub Actions** (`.github/workflows/build.yml`). Do **NOT** use local WSL2.
2. **Target Working Subsystems:**
   - **CPU:** 8x Kryo 475 (`SM7250`) + `qcom-cpufreq-hw`
   - **GPU:** Adreno 620 (`/dev/dri/card0` + `a620_zap.mdt`)
   - **Display:** Samsung `S6E3HC2` `1080x2340@90Hz` DSC 1.1 command-mode AMOLED panel (`DSI-1`)
   - **Storage:** UFS 2.1 internal storage (`/dev/sda`..`/dev/sdf`, `/dev/disk/by-partlabel/*`)
   - **Wi-Fi:** WCN3990 (`ath10k_snoc` + `pd-mapper` + `rmtfs -r -P -s` + `mpss` remoteproc -> `wlan0`)

---

## 2. Target Device Details (Connected via USB / ADB)
- **Device:** Google Pixel 5 (`redfin`), Serial `15171FDD40000K`
- **Hardware Revision:** `MP1.0` (PVT), 8 GB Micron LPDDR4X RAM, 128 GB Micron UFS
- **Bootloader:** Unlocked (`ro.boot.flash.locked = 0`, `r3-0.6-10489834`)
- **Active Slot:** `_a` (running Android 17 `CP2A.260605.016` `userdebug` with native **`adb root` (`uid=0`)** access)
- **Baseband / Radio on `modem_a`:** `g7250-00264-230619-B-10346159`
- **Stock Panel Confirmed via `dmesg`:** `qcom,mdss_dsi_s6e3hc2_dvt_dsc_1080p_cmd`

---

## 3. Repository Structure & Progress Completed
- `kernel/sm7250-google-redfin.dts` — Mainline Device Tree for Google Pixel 5 (`google,redfin`, `qcom,sm7250`). Includes reserved memory map matching Redfin's stock DTB, `gpio-reserved-ranges = <0 4>` for TZ-locked `qupv3_se1_spi`, PM8150/PM8150L/PM7250B regulators (`vddio`/`vci`/`vddd`), USB peripheral mode, UFS, Adreno 620 GPU, WCN3990 `wifi` node, and `mpss` remoteproc.
- `kernel/panel-samsung-s6e3hc2.c` — Mainline DRM panel driver for the Samsung `S6E3HC2` `1080x2340` 60/90Hz DSC 1.1 command-mode AMOLED panel.
- `config/redfin.config` — Kernel config fragment enabling `CONFIG_DRM_PANEL_SAMSUNG_S6E3HC2=y`, USB gadget mode, filesystems (`VFAT`, `F2FS`, `EXT4`), and disabling non-Qualcomm SoC families (`ARCH_*`, `PCI`, `ACPI`, `KVM`) for fast CI builds.
- `firmware/` — Exact live device firmware pulled via `adb root` from the user's Pixel 5 (`wlanmdsp.mbn`, `modemuw.jsn`, `bdwlan-redfin.bin`, `bdwlan-u-redfin.bin`, `a620_zap.*`, `a650_sqe.fw`, `a650_gmu.bin`, `ipa_fws.*`, `adspr.jsn`, `adsps.jsn`, `adspua.jsn`, `cdspr.jsn`).
- `scripts/extract_fw_ci.py` — Packages the firmware and generates `ath10k/WCN3990/hw1.0/board-2.bin` (covering `qmi-board-id` `0x00..0xff` from `bdwlan-redfin.bin`) + `board.bin`.
- `initramfs/init` — Initramfs init script: brings up USB NCM (`172.16.42.1`) + `udhcpd` + `telnetd` passwordless root shell, populates `/dev/disk/by-partlabel/*`, mounts `modem_a`/`modem_b` read-only at `/mnt/modem`, starts `pd-mapper` + `rmtfs -r -P -s` + `mpss` remoteproc for `wlan0`, and renders live status on the OLED panel via `drmtext`.
- `scripts/build_all_ci.sh` & `.github/workflows/build.yml` — Full GitHub Actions build pipeline producing `boot-redfin.img`, `Image.gz`, `sm7250-google-redfin.dtb`, `initramfs.cpio.gz`, and `redfin-firmware.tar.gz`.
- `check_ci.py` — Helper script using `git credential fill` to check GitHub Actions status, view logs/errors, and download artifacts into `./artifacts/`.

---

## 4. What to Do Next on the New System (Phase 3)

1. **Check GitHub Actions Run #4 Status & Download `boot-redfin.img`:**
   ```powershell
   python check_ci.py status
   python check_ci.py download
   ```
   *(If Run #4 has any error, run `python check_ci.py errors` or `python check_ci.py logs 120`, fix it, and `git push`.)*

2. **Back Up `dtbo_a` from the Connected Pixel 5 (Safety Backup):**
   ```powershell
   adb root
   adb pull /dev/block/by-name/dtbo_a dtbo_a_backup.img
   ```

3. **Boot `boot-redfin.img` Non-Destructively via Fastboot:**
   ```powershell
   adb reboot bootloader
   fastboot boot artifacts\boot-redfin.img
   ```
   *(Note: If Pixel 5's `abl` bootloader attempts to apply stock Android's `dtbo_a` overlay onto our mainline DTB and fails with an FDT overlay error during `fastboot boot`, temporarily zero `dtbo_a` with `fastboot erase dtbo_a`, run `fastboot boot artifacts\boot-redfin.img`, and restore `fastboot flash dtbo_a dtbo_a_backup.img` when returning to Android.)*

4. **Verify All 5 Subsystems Over USB NCM (`172.16.42.1`):**
   Once booted, the OLED panel displays the `drmtext` hardware status screen, and USB NCM brings up `172.16.42.1`. Connect via telnet/nc to `172.16.42.1`:
   - **CPU:** `nproc` (8 cores) and `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
   - **GPU:** `ls -l /dev/dri/card0` and `dmesg | grep -i adreno`
   - **Display:** `cat /sys/class/drm/card0-DSI-1/status` & `modes` (`1080x2340`)
   - **Storage:** `ls -l /dev/sd* /dev/disk/by-partlabel/`
   - **Wi-Fi:** `ip link show wlan0` and `iw dev wlan0 scan | grep SSID`
