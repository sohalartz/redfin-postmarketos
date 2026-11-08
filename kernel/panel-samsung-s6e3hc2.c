// SPDX-License-Identifier: GPL-2.0-only
/*
 * Samsung S6E3HC2 AMOLED DSI command mode panel driver (DSC 1.1)
 *
 * 1080x2340, 5.96", 4 lanes, DSC 1.1 (8 bpc -> 8 bpp, 540x65 slice, 1 slice/pkt),
 * 60 Hz and 90 Hz. Found in the Google Pixel 5 (redfin).
 *
 * Initialization sequences and timings extracted from Google Redfin stock DTBO
 * (dtbo_1.dts fragment@95: "qcom,mdss_dsi_s6e3hc2_dsc_1080p_cmd").
 */

#include <linux/backlight.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>

#include <drm/display/drm_dsc.h>
#include <drm/display/drm_dsc_helper.h>
#include <drm/drm_connector.h>
#include <drm/drm_crtc.h>
#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

#define S6E3HC2_WIDTH			1080
#define S6E3HC2_HEIGHT			2340

#define S6E3HC2_MAX_BRIGHTNESS		0x3ff
#define S6E3HC2_DEFAULT_BRIGHTNESS	0x1ff

enum {
	S6E3HC2_SUPPLY_VDDIO,
	S6E3HC2_SUPPLY_VDD,
	S6E3HC2_NUM_SUPPLIES,
};

static const struct s6e3hc2_supply_info {
	const char *name;
	unsigned int enable_load_ua;
	unsigned int disable_load_ua;
	unsigned int post_on_ms;
} s6e3hc2_supply_info[S6E3HC2_NUM_SUPPLIES] = {
	[S6E3HC2_SUPPLY_VDDIO] = { "vddio", 100000, 100, 2 },
	[S6E3HC2_SUPPLY_VDD]   = { "vdd",   100000, 100, 2 },
};

struct s6e3hc2 {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	struct drm_connector *connector;
	struct drm_dsc_config dsc;
	struct gpio_desc *reset_gpio;
	struct regulator *supplies[S6E3HC2_NUM_SUPPLIES];
	bool prepared;
};

static inline struct s6e3hc2 *to_s6e3hc2(struct drm_panel *panel)
{
	return container_of(panel, struct s6e3hc2, panel);
}

static void s6e3hc2_send_on(struct s6e3hc2 *ctx,
			    struct mipi_dsi_multi_context *dsi_ctx,
			    bool hz90)
{
	struct drm_dsc_picture_parameter_set pps;

	/* Send DSC 1.1 Picture Parameter Set and enable compression mode (0x07 0x01) */
	drm_dsc_pps_payload_pack(&pps, &ctx->dsc);
	mipi_dsi_picture_parameter_set_multi(dsi_ctx, &pps);
	mipi_dsi_compression_mode_multi(dsi_ctx, true);

	/* Downstream qcom,mdss-dsi-on-command from fragment@95 */
	mipi_dsi_dcs_exit_sleep_mode_multi(dsi_ctx);
	mipi_dsi_msleep(dsi_ctx, 120);

	if (hz90)
		mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0x53, 0x38);
	else
		mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0x53, 0x28);

	mipi_dsi_dcs_set_tear_on_multi(dsi_ctx, MIPI_DSI_DCS_TEAR_MODE_VBLANK);

	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xf0, 0x5a, 0x5a);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xf1, 0x5a, 0x5a);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xfc, 0x5a, 0x5a);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xb0, 0x05, 0xb1);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xb1, 0x08);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xb0, 0x86, 0xeb);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xeb, 0x01);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xb0, 0x07, 0xb9);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xb9, 0x11, 0x03);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0x97, 0x02, 0x60);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xf0, 0xa5, 0xa5);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xf1, 0xa5, 0xa5);
	mipi_dsi_dcs_write_seq_multi(dsi_ctx, 0xfc, 0xa5, 0xa5);

	mipi_dsi_dcs_set_display_on_multi(dsi_ctx);
}

static void s6e3hc2_send_off(struct mipi_dsi_multi_context *dsi_ctx)
{
	mipi_dsi_dcs_set_display_off_multi(dsi_ctx);
	mipi_dsi_msleep(dsi_ctx, 10);
	mipi_dsi_dcs_enter_sleep_mode_multi(dsi_ctx);
	mipi_dsi_msleep(dsi_ctx, 120);
}

static void s6e3hc2_reset(struct s6e3hc2 *ctx)
{
	/* Downstream: <1 10>, <0 1>, <1 5> with 1 = out of reset (reset-gpios is GPIO_ACTIVE_LOW) */
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	usleep_range(10000, 11000);
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	usleep_range(2000, 3000);
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	usleep_range(6000, 7000);
}

static void s6e3hc2_supplies_disable(struct s6e3hc2 *ctx, int first)
{
	int i;

	for (i = first; i >= 0; i--) {
		if (!ctx->supplies[i])
			continue;

		regulator_set_load(ctx->supplies[i],
				   s6e3hc2_supply_info[i].disable_load_ua);
		regulator_disable(ctx->supplies[i]);
	}
}

static int s6e3hc2_supplies_enable(struct s6e3hc2 *ctx)
{
	struct device *dev = &ctx->dsi->dev;
	int i, ret;

	for (i = 0; i < S6E3HC2_NUM_SUPPLIES; i++) {
		if (!ctx->supplies[i])
			continue;

		regulator_set_load(ctx->supplies[i],
				   s6e3hc2_supply_info[i].enable_load_ua);

		ret = regulator_enable(ctx->supplies[i]);
		if (ret) {
			dev_err(dev, "Failed to enable %s: %d\n",
				s6e3hc2_supply_info[i].name, ret);
			s6e3hc2_supplies_disable(ctx, i - 1);
			return ret;
		}

		if (s6e3hc2_supply_info[i].post_on_ms)
			msleep(s6e3hc2_supply_info[i].post_on_ms);
	}

	return 0;
}

static bool s6e3hc2_want_90hz(struct s6e3hc2 *ctx)
{
	struct drm_connector *connector = ctx->connector;

	if (!connector || !connector->state || !connector->state->crtc ||
	    !connector->state->crtc->state)
		return false;

	return drm_mode_vrefresh(&connector->state->crtc->state->mode) > 75;
}

static int s6e3hc2_prepare(struct drm_panel *panel)
{
	struct s6e3hc2 *ctx = to_s6e3hc2(panel);
	struct device *dev = &ctx->dsi->dev;
	struct mipi_dsi_multi_context dsi_ctx = { .dsi = ctx->dsi };
	bool hz90 = s6e3hc2_want_90hz(ctx);
	int ret;

	dev_info(dev, "prepare: start (%s mode)\n", hz90 ? "90 Hz" : "60 Hz");

	ret = s6e3hc2_supplies_enable(ctx);
	if (ret)
		return ret;

	s6e3hc2_reset(ctx);

	s6e3hc2_send_on(ctx, &dsi_ctx, hz90);
	if (dsi_ctx.accum_err) {
		ret = dsi_ctx.accum_err;
		dev_err(dev, "Failed to send on sequence: %d\n", ret);
		goto err_power_down;
	}

	ctx->prepared = true;

	if (ctx->panel.backlight)
		mipi_dsi_dcs_set_display_brightness_large(
			ctx->dsi, backlight_get_brightness(ctx->panel.backlight));

	dev_info(dev, "prepare: done\n");

	return 0;

err_power_down:
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	s6e3hc2_supplies_disable(ctx, S6E3HC2_NUM_SUPPLIES - 1);

	return ret;
}

static int s6e3hc2_unprepare(struct drm_panel *panel)
{
	struct s6e3hc2 *ctx = to_s6e3hc2(panel);
	struct device *dev = &ctx->dsi->dev;
	struct mipi_dsi_multi_context dsi_ctx = { .dsi = ctx->dsi };

	ctx->prepared = false;

	s6e3hc2_send_off(&dsi_ctx);
	if (dsi_ctx.accum_err)
		dev_err(dev, "Failed to send off sequence: %d\n", dsi_ctx.accum_err);

	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	s6e3hc2_supplies_disable(ctx, S6E3HC2_NUM_SUPPLIES - 1);

	return 0;
}

/*
 * Timings from Google Redfin dtbo_1.dts fragment@95:
 * hdisplay=1080, hfp=32, hpw=12, hbp=16 (htotal=1140)
 * vdisplay=2340, vfp=12, vpw=4,  vbp=16 (vtotal=2372)
 * width_mm=63 (0x3f), height_mm=137 (0x89)
 */
static const struct drm_display_mode s6e3hc2_modes[] = {
	{	/* 60 Hz */
		.clock = (1080 + 32 + 12 + 16) * (2340 + 12 + 4 + 16) * 60 / 1000,
		.hdisplay = 1080,
		.hsync_start = 1080 + 32,
		.hsync_end = 1080 + 32 + 12,
		.htotal = 1080 + 32 + 12 + 16,
		.vdisplay = 2340,
		.vsync_start = 2340 + 12,
		.vsync_end = 2340 + 12 + 4,
		.vtotal = 2340 + 12 + 4 + 16,
		.width_mm = 63,
		.height_mm = 137,
		.type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
	},
	{	/* 90 Hz */
		.clock = (1080 + 32 + 12 + 16) * (2340 + 12 + 4 + 16) * 90 / 1000,
		.hdisplay = 1080,
		.hsync_start = 1080 + 32,
		.hsync_end = 1080 + 32 + 12,
		.htotal = 1080 + 32 + 12 + 16,
		.vdisplay = 2340,
		.vsync_start = 2340 + 12,
		.vsync_end = 2340 + 12 + 4,
		.vtotal = 2340 + 12 + 4 + 16,
		.width_mm = 63,
		.height_mm = 137,
		.type = DRM_MODE_TYPE_DRIVER,
	},
};

static int s6e3hc2_get_modes(struct drm_panel *panel,
			     struct drm_connector *connector)
{
	struct s6e3hc2 *ctx = to_s6e3hc2(panel);
	int i, count = 0;

	for (i = 0; i < ARRAY_SIZE(s6e3hc2_modes); i++) {
		struct drm_display_mode *mode;

		mode = drm_mode_duplicate(connector->dev, &s6e3hc2_modes[i]);
		if (!mode)
			return count;

		drm_mode_probed_add(connector, mode);
		count++;
	}

	connector->display_info.width_mm = s6e3hc2_modes[0].width_mm;
	connector->display_info.height_mm = s6e3hc2_modes[0].height_mm;
	connector->display_info.bpc = 8;
	ctx->connector = connector;

	return count;
}

static const struct drm_panel_funcs s6e3hc2_panel_funcs = {
	.prepare = s6e3hc2_prepare,
	.unprepare = s6e3hc2_unprepare,
	.get_modes = s6e3hc2_get_modes,
};

static int s6e3hc2_bl_update_status(struct backlight_device *bl)
{
	struct s6e3hc2 *ctx = bl_get_data(bl);

	if (!ctx->prepared)
		return 0;

	return mipi_dsi_dcs_set_display_brightness_large(ctx->dsi,
							 backlight_get_brightness(bl));
}

static const struct backlight_ops s6e3hc2_bl_ops = {
	.update_status = s6e3hc2_bl_update_status,
};

static int s6e3hc2_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	const struct backlight_properties props = {
		.type = BACKLIGHT_RAW,
		.brightness = S6E3HC2_DEFAULT_BRIGHTNESS,
		.max_brightness = S6E3HC2_MAX_BRIGHTNESS,
	};
	struct backlight_device *bl;
	struct s6e3hc2 *ctx;
	int i, ret;

	ctx = devm_drm_panel_alloc(dev, struct s6e3hc2, panel,
				   &s6e3hc2_panel_funcs,
				   DRM_MODE_CONNECTOR_DSI);
	if (IS_ERR(ctx))
		return PTR_ERR(ctx);

	for (i = 0; i < S6E3HC2_NUM_SUPPLIES; i++) {
		struct regulator *reg;

		reg = devm_regulator_get_optional(dev, s6e3hc2_supply_info[i].name);
		if (IS_ERR(reg)) {
			if (PTR_ERR(reg) != -ENODEV)
				return dev_err_probe(dev, PTR_ERR(reg),
						     "Failed to get %s supply\n",
						     s6e3hc2_supply_info[i].name);
			continue;
		}

		ctx->supplies[i] = reg;
	}

	ctx->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ctx->reset_gpio))
		return dev_err_probe(dev, PTR_ERR(ctx->reset_gpio),
				     "Failed to get reset-gpios\n");

	ctx->dsi = dsi;
	mipi_dsi_set_drvdata(dsi, ctx);

	dsi->lanes = 4;
	dsi->format = MIPI_DSI_FMT_RGB888;
	/* Note: Redfin uses qcom,mdss-dsc-slice-per-pkt = <1>, so no ALL_SLICES_IN_PKT */
	dsi->mode_flags = MIPI_DSI_MODE_LPM | MIPI_DSI_CLOCK_NON_CONTINUOUS;

	/*
	 * DSC 1.1, 8 bpc, 8 bpp (4 fractional bits), block prediction,
	 * two 540x65 slices per line, 1 slice per packet.
	 * The MSM DSI host derives the remaining RC parameters during attach.
	 */
	ctx->dsc.dsc_version_major = 1;
	ctx->dsc.dsc_version_minor = 1;
	ctx->dsc.pic_width = S6E3HC2_WIDTH;
	ctx->dsc.pic_height = S6E3HC2_HEIGHT;
	ctx->dsc.slice_width = 540;
	ctx->dsc.slice_height = 65;
	ctx->dsc.slice_count = S6E3HC2_WIDTH / ctx->dsc.slice_width;
	ctx->dsc.bits_per_component = 8;
	ctx->dsc.bits_per_pixel = 8 << 4;
	ctx->dsc.block_pred_enable = true;
	dsi->dsc = &ctx->dsc;

	ctx->panel.prepare_prev_first = true;

	bl = devm_backlight_device_register(dev, dev_name(dev), dev, ctx,
					    &s6e3hc2_bl_ops, &props);
	if (IS_ERR(bl))
		return dev_err_probe(dev, PTR_ERR(bl), "Failed to register backlight\n");
	ctx->panel.backlight = bl;

	drm_panel_add(&ctx->panel);

	ret = mipi_dsi_attach(dsi);
	if (ret < 0) {
		drm_panel_remove(&ctx->panel);
		return dev_err_probe(dev, ret, "Failed to attach to DSI host\n");
	}

	return 0;
}

static void s6e3hc2_remove(struct mipi_dsi_device *dsi)
{
	struct s6e3hc2 *ctx = mipi_dsi_get_drvdata(dsi);
	int ret;

	ret = mipi_dsi_detach(dsi);
	if (ret < 0)
		dev_err(&dsi->dev, "Failed to detach from DSI host: %d\n", ret);

	drm_panel_remove(&ctx->panel);
}

static const struct of_device_id s6e3hc2_of_match[] = {
	{ .compatible = "samsung,s6e3hc2" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, s6e3hc2_of_match);

static struct mipi_dsi_driver s6e3hc2_driver = {
	.probe = s6e3hc2_probe,
	.remove = s6e3hc2_remove,
	.driver = {
		.name = "panel-samsung-s6e3hc2",
		.of_match_table = s6e3hc2_of_match,
	},
};
module_mipi_dsi_driver(s6e3hc2_driver);

MODULE_DESCRIPTION("Panel driver for the Samsung S6E3HC2 AMOLED DSI panel");
MODULE_LICENSE("GPL");
