# postmarketOS for Google Pixel 5 (`google-redfin` / `sm7250`)

Mainline Linux & postmarketOS bring-up project for the Google Pixel 5 (`redfin`), leveraging Qualcomm Snapdragon 765G (`sm7250` / `lito`) SoC infrastructure from [`lazybrar/avicii-mainline`](https://github.com/lazybrar/avicii-mainline) and adapting board-specific peripherals from Google's `redbull` downstream tree.

## Target Hardware Subsystems
- **CPU:** Qualcomm Kryo 475 (`sm7250.dtsi` SMP, PSCI, `cpufreq-qcom-hw`, GCC/RPMh clocks & power domains)
- **Storage:** Internal UFS (`ufshcd-qcom`) + USB Type-C gadget (`dwc3-qcom`)
- **Display:** 6.0" FHD+ (1080x2340) 90Hz OLED (`simple-framebuffer` -> MDSS/DPU1 + DSI panel driver)
- **GPU:** Qualcomm Adreno 620 (`msm` DRM / `adreno` + `a6xx_gmu` + Mesa Turnip/Freedreno)
- **Wi-Fi:** Qualcomm WCN3990 (`ath10k_snoc` + `qcom_q6v5_pas` Remoteproc WPSS/MSS + board firmware)
