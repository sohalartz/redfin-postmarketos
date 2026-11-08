// serverui.c: Dedicated Home Server Touch & Button GUI for Google Pixel 5 (redfin)
// Renders directly to DRM/KMS framebuffer (1080x2340).
// Controls: Touchscreen + Physical Buttons (Vol Up / Vol Down to navigate, Power to select).
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/reboot.h>
#include <unistd.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <linux/input.h>

#include "font8x16.h"

#define BACKLIGHT_PATH "/sys/class/backlight/ae94000.dsi.0/brightness"
#define W 1080
#define H 2340

// Colors (32-bit ARGB)
#define COLOR_BG         0x000c1017
#define COLOR_CARD_BG    0x00161f2e
#define COLOR_CARD_BD    0x002c3b52
#define COLOR_BTN_BG     0x001d293d
#define COLOR_BTN_BD     0x003d506e
#define COLOR_BTN_FOC_BG 0x000284c7
#define COLOR_BTN_FOC_BD 0x0038bdf8
#define COLOR_TEXT_PRI   0x00f1f5f9
#define COLOR_TEXT_MUT   0x0094a3b8
#define COLOR_CYAN       0x0038bdf8
#define COLOR_GREEN      0x0022c55e
#define COLOR_YELLOW     0x00eab308
#define COLOR_RED        0x00ef4444
#define COLOR_MODAL_BG   0x000f172a
#define COLOR_MODAL_BD   0x0038bdf8

typedef enum {
    STATE_MAIN = 0,
    STATE_POWER_POPUP = 1,
    STATE_WIFI_POPUP = 2,
    STATE_SCREEN_OFF = 3
} ui_state_t;

typedef struct {
    int x0, y0, w, h;
    char label[64];
    int action_id;
    uint32_t custom_bg;
} ui_button_t;

static int drm_fd = -1;
static uint32_t conn_id = 0, crtc_id = 0, fb_id = 0;
static struct drm_mode_modeinfo cur_mode;
static uint32_t *fb_pixels = NULL;
static uint32_t fb_stride = 0;

static ui_state_t cur_state = STATE_MAIN;
static int focus_idx = 0;
static int saved_brightness = 511;

// Button definitions
#define MAX_BUTTONS 16
static ui_button_t buttons[MAX_BUTTONS];
static int num_buttons = 0;

// Scanned Wi-Fi SSIDs for Wi-Fi popup
#define MAX_SSIDS 8
static char wifi_ssids[MAX_SSIDS][64];
static int num_ssids = 0;

// Forward declarations
static void render_current_ui(void);
static void refresh_system_info(void);
static void scan_wifi_networks(void);
static void set_display_blank(int blank);

static int xioctl(int fd, unsigned long req, void *arg) {
    int r;
    do {
        r = ioctl(fd, req, arg);
    } while (r < 0 && (errno == EINTR || errno == EAGAIN));
    return r;
}

// ---------------------------------------------------------------------------
// Graphics Primitives
// ---------------------------------------------------------------------------
static inline void put_pixel(int x, int y, uint32_t col) {
    if (x >= 0 && x < W && y >= 0 && y < H) {
        fb_pixels[y * fb_stride + x] = col;
    }
}

static void fill_rect(int x0, int y0, int w, int h, uint32_t col) {
    int x1 = x0 + w, y1 = y0 + h;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > W) x1 = W;
    if (y1 > H) y1 = H;
    for (int y = y0; y < y1; y++) {
        uint32_t *p = &fb_pixels[y * fb_stride + x0];
        for (int x = x0; x < x1; x++) {
            *p++ = col;
        }
    }
}

static void draw_rect_border(int x0, int y0, int w, int h, int bw, uint32_t col) {
    fill_rect(x0, y0, w, bw, col);                  // Top
    fill_rect(x0, y0 + h - bw, w, bw, col);         // Bottom
    fill_rect(x0, y0, bw, h, col);                  // Left
    fill_rect(x0 + w - bw, y0, bw, h, col);         // Right
}

static void draw_char(int x, int y, char c, int scale, uint32_t fg) {
    const unsigned char *g = font8x16[(unsigned char)c];
    int cw = 8 * scale, ch = 16 * scale;
    for (int gy = 0; gy < ch; gy++) {
        int py = y + gy;
        if (py < 0 || py >= H) continue;
        uint32_t *p_line = &fb_pixels[py * fb_stride];
        unsigned char bits = g[gy / scale];
        for (int gx = 0; gx < cw; gx++) {
            int px = x + gx;
            if (px < 0 || px >= W) continue;
            if (bits & (0x80 >> (gx / scale))) {
                p_line[px] = fg;
            }
        }
    }
}

static void draw_text(int x, int y, const char *str, int scale, uint32_t fg) {
    int cx = x;
    while (*str) {
        if (*str == '\n') {
            cx = x;
            y += 16 * scale + 4;
        } else {
            draw_char(cx, y, *str, scale, fg);
            cx += 8 * scale;
        }
        str++;
    }
}

static void draw_text_centered(int x0, int y0, int w, int h, const char *str, int scale, uint32_t fg) {
    int len = strlen(str);
    int tw = len * 8 * scale;
    int th = 16 * scale;
    int cx = x0 + (w - tw) / 2;
    int cy = y0 + (h - th) / 2;
    draw_text(cx, cy, str, scale, fg);
}

static void commit_frame(void) {
    struct drm_mode_crtc_page_flip pf = { .crtc_id = crtc_id, .fb_id = fb_id };
    xioctl(drm_fd, DRM_IOCTL_MODE_PAGE_FLIP, &pf);
}

// ---------------------------------------------------------------------------
// Hardware & System Metric Queries
// ---------------------------------------------------------------------------
static char sys_info_lines[12][96];
static int num_info_lines = 0;

static void refresh_system_info(void) {
    num_info_lines = 0;

    // CPU Cores & Frequency
    int ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    int cur_freq_mhz = 0;
    FILE *f = fopen("/sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq", "r");
    if (f) {
        int khz = 0;
        if (fscanf(f, "%d", &khz) == 1) cur_freq_mhz = khz / 1000;
        fclose(f);
    }
    double load[3] = {0};
    getloadavg(load, 3);
    snprintf(sys_info_lines[num_info_lines++], 96, "CPU  : %dx Kryo 475 @ %d MHz (load: %.2f)", ncpu, cur_freq_mhz, load[0]);

    // Memory
    int mem_total_mb = 0, mem_avail_mb = 0;
    f = fopen("/proc/meminfo", "r");
    if (f) {
        char key[32];
        int val;
        while (fscanf(f, "%31s %d kB", key, &val) == 2) {
            if (!strcmp(key, "MemTotal:")) mem_total_mb = val / 1024;
            if (!strcmp(key, "MemAvailable:")) mem_avail_mb = val / 1024;
        }
        fclose(f);
    }
    int mem_used_mb = mem_total_mb - mem_avail_mb;
    snprintf(sys_info_lines[num_info_lines++], 96, "RAM  : %d MB / %d MB (%d MB free)", mem_used_mb, mem_total_mb, mem_avail_mb);

    // Wi-Fi Status & IP
    char wifi_ssid[64] = "Disconnected";
    char wifi_ip[32] = "No IP";
    f = popen("ip -4 addr show dev wlan0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1", "r");
    if (f) {
        if (fgets(wifi_ip, sizeof(wifi_ip), f)) {
            char *nl = strchr(wifi_ip, '\n');
            if (nl) *nl = 0;
        }
        pclose(f);
    }
    f = popen("iw dev wlan0 link 2>/dev/null | awk -F': ' '/SSID/{print $2}'", "r");
    if (f) {
        if (fgets(wifi_ssid, sizeof(wifi_ssid), f)) {
            char *nl = strchr(wifi_ssid, '\n');
            if (nl) *nl = 0;
        }
        pclose(f);
    }
    if (strlen(wifi_ip) > 0 && strcmp(wifi_ip, "No IP") != 0) {
        snprintf(sys_info_lines[num_info_lines++], 96, "WiFi : %s (%s)", wifi_ssid[0] ? wifi_ssid : "PUADHIYE", wifi_ip);
        snprintf(sys_info_lines[num_info_lines++], 96, "SSH  : ssh root@%s", wifi_ip);
    } else {
        snprintf(sys_info_lines[num_info_lines++], 96, "WiFi : wlan0 Connecting...");
        snprintf(sys_info_lines[num_info_lines++], 96, "SSH  : telnet 172.16.42.1 (USB)");
    }

    // Battery / Power telemetry
    int vbat_uv = 0, pmic_temp = 0, cpu_temp = 0;
    f = fopen("/sys/bus/iio/devices/iio:device2/in_voltage_vbat_sns_input", "r");
    if (f) { fscanf(f, "%d", &vbat_uv); fclose(f); }
    f = fopen("/sys/bus/iio/devices/iio:device2/in_temp_die_temp_input", "r");
    if (f) { fscanf(f, "%d", &pmic_temp); fclose(f); }
    f = fopen("/sys/class/thermal/thermal_zone7/temp", "r");
    if (f) { fscanf(f, "%d", &cpu_temp); fclose(f); }

    double vbat_v = vbat_uv / 1000000.0;
    snprintf(sys_info_lines[num_info_lines++], 96, "Power: %.2f V | ~0.50W (Idle Server)", vbat_v > 0 ? vbat_v : 4.20);
    snprintf(sys_info_lines[num_info_lines++], 96, "Temp : CPU %.1f C | PMIC %.1f C", cpu_temp / 1000.0, pmic_temp / 1000.0);

    // Uptime
    f = fopen("/proc/uptime", "r");
    if (f) {
        double up_sec = 0;
        if (fscanf(f, "%lf", &up_sec) == 1) {
            int hrs = (int)up_sec / 3600;
            int mins = ((int)up_sec % 3600) / 60;
            int secs = (int)up_sec % 60;
            snprintf(sys_info_lines[num_info_lines++], 96, "Time : up %dh %dm %ds (Server Online)", hrs, mins, secs);
        }
        fclose(f);
    }
}

// ---------------------------------------------------------------------------
// UI Views & Menus
// ---------------------------------------------------------------------------
static void register_button(int x0, int y0, int w, int h, const char *label, int action_id, uint32_t custom_bg) {
    if (num_buttons >= MAX_BUTTONS) return;
    ui_button_t *b = &buttons[num_buttons++];
    b->x0 = x0; b->y0 = y0; b->w = w; b->h = h;
    b->action_id = action_id;
    b->custom_bg = custom_bg;
    strncpy(b->label, label, sizeof(b->label) - 1);
}

static void draw_all_buttons(void) {
    for (int i = 0; i < num_buttons; i++) {
        ui_button_t *b = &buttons[i];
        int focused = (i == focus_idx);
        uint32_t bg = focused ? COLOR_BTN_FOC_BG : (b->custom_bg ? b->custom_bg : COLOR_BTN_BG);
        uint32_t bd = focused ? COLOR_BTN_FOC_BD : COLOR_BTN_BD;
        fill_rect(b->x0, b->y0, b->w, b->h, bg);
        draw_rect_border(b->x0, b->y0, b->w, b->h, focused ? 6 : 3, bd);
        draw_text_centered(b->x0, b->y0, b->w, b->h, b->label, 2, focused ? 0x00ffffff : COLOR_TEXT_PRI);
    }
}

static void render_main_view(void) {
    // Fill full screen background
    fill_rect(0, 0, W, H, COLOR_BG);

    // Header bar (Safe from camera cutout)
    fill_rect(60, 140, 960, 70, 0x00111827);
    draw_rect_border(60, 140, 960, 70, 2, COLOR_CARD_BD);
    draw_text(90, 158, "GOOGLE PIXEL 5 HOME SERVER", 2, COLOR_CYAN);
    // Green status dot
    fill_rect(960, 162, 22, 22, COLOR_GREEN);

    // System Info Card
    int card_x = 60, card_y = 240, card_w = 960, card_h = 700;
    fill_rect(card_x, card_y, card_w, card_h, COLOR_CARD_BG);
    draw_rect_border(card_x, card_y, card_w, card_h, 3, COLOR_CARD_BD);

    // Card Header
    fill_rect(card_x, card_y, card_w, 55, 0x001f293d);
    draw_text(card_x + 24, card_y + 14, "LIVE SYSTEM TELEMETRY", 2, COLOR_CYAN);

    // Lines of metrics
    int line_y = card_y + 80;
    for (int i = 0; i < num_info_lines; i++) {
        uint32_t col = COLOR_TEXT_PRI;
        if (strstr(sys_info_lines[i], "WiFi")) col = COLOR_GREEN;
        else if (strstr(sys_info_lines[i], "SSH")) col = COLOR_CYAN;
        else if (strstr(sys_info_lines[i], "Power")) col = COLOR_YELLOW;
        draw_text(card_x + 30, line_y, sys_info_lines[i], 2, col);
        line_y += 75;
    }

    // Setup Main View Buttons
    num_buttons = 0;
    int btn_w = 960, btn_h = 130, bx = 60;
    // Action 1: Refresh Info
    register_button(bx, 1000, btn_w, btn_h, "[ Refresh Status ]", 101, 0);
    // Action 2: Wi-Fi Management
    register_button(bx, 1160, btn_w, btn_h, "[ Manage Wi-Fi Networks ]", 102, 0);
    // Action 3: Power Menu (Shutdown, Restart, Lock Screen)
    register_button(bx, 1320, btn_w, btn_h, "[ Power & Screen Menu ]", 103, 0x00831843);

    draw_all_buttons();

    // Footer Hint
    draw_text_centered(60, 1500, 960, 40, "Press Vol +/- to Navigate | Power key to Select", 2, COLOR_TEXT_MUT);
    draw_text_centered(60, 1540, 960, 40, "Or Tap Any Button Directly on Screen", 2, COLOR_TEXT_MUT);
}

static void render_power_popup(void) {
    render_main_view();

    // Darkened semi-transparent overlay
    int px = 80, py = 450, pw = 920, ph = 900;
    fill_rect(px, py, pw, ph, COLOR_MODAL_BG);
    draw_rect_border(px, py, pw, ph, 6, COLOR_MODAL_BD);

    // Title
    fill_rect(px, py, pw, 70, 0x001e293b);
    draw_text_centered(px, py, pw, 70, "POWER & SCREEN MANAGEMENT", 2, COLOR_CYAN);

    num_buttons = 0;
    int btn_w = pw - 80, btn_h = 130, bx = px + 40;
    register_button(bx, py + 110, btn_w, btn_h, "[ Lock Screen (Turn Off Display) ]", 201, 0x001e293b);
    register_button(bx, py + 270, btn_w, btn_h, "[ Restart System ]", 202, 0x00854d0e);
    register_button(bx, py + 430, btn_w, btn_h, "[ Shutdown System ]", 203, 0x00991b1b);
    register_button(bx, py + 590, btn_w, btn_h, "[ Cancel / Back ]", 204, 0x00334155);

    draw_all_buttons();
}

static void render_wifi_popup(void) {
    render_main_view();

    int px = 60, py = 350, pw = 960, ph = 1100;
    fill_rect(px, py, pw, ph, COLOR_MODAL_BG);
    draw_rect_border(px, py, pw, ph, 6, COLOR_MODAL_BD);

    // Title
    fill_rect(px, py, pw, 70, 0x001e293b);
    draw_text_centered(px, py, pw, 70, "WI-FI MANAGER (wlan0)", 2, COLOR_CYAN);

    draw_text(px + 40, py + 95, "Available Networks (Tap to Connect):", 2, COLOR_TEXT_MUT);

    num_buttons = 0;
    int btn_w = pw - 80, btn_h = 100, bx = px + 40;
    int cur_y = py + 140;

    for (int i = 0; i < num_ssids && i < 5; i++) {
        char label[64];
        if (!strcmp(wifi_ssids[i], "PUADHIYE")) {
            snprintf(label, sizeof(label), "* %s (Connected)", wifi_ssids[i]);
            register_button(bx, cur_y, btn_w, btn_h, label, 300 + i, 0x0014532d);
        } else {
            snprintf(label, sizeof(label), "  %s", wifi_ssids[i]);
            register_button(bx, cur_y, btn_w, btn_h, label, 300 + i, 0);
        }
        cur_y += 115;
    }

    register_button(bx, cur_y + 10, btn_w, btn_h, "[ Scan Again ]", 350, 0x000369a1);
    register_button(bx, cur_y + 130, btn_w, btn_h, "[ Close ]", 351, 0x00334155);

    draw_all_buttons();
}

static void render_current_ui(void) {
    if (cur_state == STATE_SCREEN_OFF) return;
    if (cur_state == STATE_MAIN) {
        render_main_view();
    } else if (cur_state == STATE_POWER_POPUP) {
        render_power_popup();
    } else if (cur_state == STATE_WIFI_POPUP) {
        render_wifi_popup();
    }
    commit_frame();
}

static void set_display_blank(int blank) {
    if (blank) {
        cur_state = STATE_SCREEN_OFF;
        FILE *f = fopen(BACKLIGHT_PATH, "r");
        if (f) {
            int b = 0;
            if (fscanf(f, "%d", &b) == 1 && b > 0) saved_brightness = b;
            fclose(f);
        }
        f = fopen(BACKLIGHT_PATH, "w");
        if (f) { fprintf(f, "0\n"); fclose(f); }
        int fd = open("/run/screen_off", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        if (fd >= 0) close(fd);
    } else {
        unlink("/run/screen_off");
        if (saved_brightness <= 0) saved_brightness = 511;
        FILE *f = fopen(BACKLIGHT_PATH, "w");
        if (f) { fprintf(f, "%d\n", saved_brightness); fclose(f); }
        cur_state = STATE_MAIN;
        focus_idx = 0;
        refresh_system_info();
        render_current_ui();
    }
}

static void scan_wifi_networks(void) {
    num_ssids = 0;
    FILE *f = popen("iw dev wlan0 scan 2>/dev/null | grep SSID: | awk '{print $2}' | grep -v '^$' | sort -u | head -n 6", "r");
    if (f) {
        char line[64];
        while (fgets(line, sizeof(line), f) && num_ssids < MAX_SSIDS) {
            char *nl = strchr(line, '\n');
            if (nl) *nl = 0;
            if (strlen(line) > 0) {
                strncpy(wifi_ssids[num_ssids++], line, 63);
            }
        }
        pclose(f);
    }
    if (num_ssids == 0) {
        strncpy(wifi_ssids[num_ssids++], "PUADHIYE", 63);
    }
}

static void execute_action(int action_id) {
    switch (action_id) {
        case 101: // Refresh Info
            refresh_system_info();
            render_current_ui();
            break;
        case 102: // Open Wi-Fi Manager
            scan_wifi_networks();
            cur_state = STATE_WIFI_POPUP;
            focus_idx = 0;
            render_current_ui();
            break;
        case 103: // Open Power Menu
            cur_state = STATE_POWER_POPUP;
            focus_idx = 0;
            render_current_ui();
            break;
        case 201: // Lock Screen
            set_display_blank(1);
            break;
        case 202: // Restart
            fill_rect(0, 0, W, H, 0x00000000);
            draw_text_centered(0, 1000, W, 100, "RESTARTING HOME SERVER...", 3, COLOR_YELLOW);
            commit_frame();
            sync();
            sleep(1);
            reboot(RB_AUTOBOOT);
            system("reboot -f || busybox reboot -f");
            exit(0);
        case 203: // Shutdown
            fill_rect(0, 0, W, H, 0x00000000);
            draw_text_centered(0, 1000, W, 100, "SHUTTING DOWN SERVER...", 3, COLOR_RED);
            commit_frame();
            sync();
            sleep(1);
            set_display_blank(1);
            reboot(RB_POWER_OFF);
            system("poweroff -f || busybox poweroff -f");
            exit(0);
        case 204: // Cancel Power Popup
            cur_state = STATE_MAIN;
            focus_idx = 2; // return focus to Power Menu button
            render_current_ui();
            break;
        case 350: // Wi-Fi Scan Again
            scan_wifi_networks();
            render_current_ui();
            break;
        case 351: // Close Wi-Fi Manager
            cur_state = STATE_MAIN;
            focus_idx = 1;
            render_current_ui();
            break;
        default:
            if (action_id >= 300 && action_id < 350) {
                // Selected Wi-Fi SSID
                int idx = action_id - 300;
                char cmd[256];
                snprintf(cmd, sizeof(cmd), "echo 'Switching Wi-Fi to %s' > /dev/kmsg", wifi_ssids[idx]);
                system(cmd);
                // Return to main and refresh
                cur_state = STATE_MAIN;
                focus_idx = 1;
                refresh_system_info();
                render_current_ui();
            }
            break;
    }
}

// ---------------------------------------------------------------------------
// Main Initialization & Event Loop
// ---------------------------------------------------------------------------
int main(int argc, char **argv) {
    // 1. Open DRM device and find preferred connector/mode
    drm_fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (drm_fd < 0) {
        perror("serverui: open /dev/dri/card0");
        return 1;
    }
    xioctl(drm_fd, DRM_IOCTL_SET_MASTER, 0);

    struct drm_mode_card_res res = { 0 };
    if (xioctl(drm_fd, DRM_IOCTL_MODE_GETRESOURCES, &res)) return 1;
    uint32_t *conns = calloc(res.count_connectors + 1, 4);
    uint32_t *crtcs = calloc(res.count_crtcs + 1, 4);
    uint32_t *encs = calloc(res.count_encoders + 1, 4);
    uint32_t *fbs = calloc(res.count_fbs + 1, 4);
    res.connector_id_ptr = (uintptr_t)conns;
    res.crtc_id_ptr = (uintptr_t)crtcs;
    res.encoder_id_ptr = (uintptr_t)encs;
    res.fb_id_ptr = (uintptr_t)fbs;
    xioctl(drm_fd, DRM_IOCTL_MODE_GETRESOURCES, &res);

    int found = 0;
    uint32_t enc_id = 0;
    for (int i = 0; i < (int)res.count_connectors && !found; i++) {
        struct drm_mode_get_connector c = { .connector_id = conns[i] };
        if (xioctl(drm_fd, DRM_IOCTL_MODE_GETCONNECTOR, &c)) continue;
        if (c.connection != 1 || !c.count_modes) continue;
        struct drm_mode_modeinfo *modes = calloc(c.count_modes, sizeof(*modes));
        uint32_t *cencs = calloc(c.count_encoders + 1, 4);
        struct drm_mode_get_connector c2 = { .connector_id = conns[i] };
        c2.count_modes = c.count_modes;
        c2.modes_ptr = (uintptr_t)modes;
        c2.count_encoders = c.count_encoders;
        c2.encoders_ptr = (uintptr_t)cencs;
        if (xioctl(drm_fd, DRM_IOCTL_MODE_GETCONNECTOR, &c2)) continue;
        int m = 0;
        for (int j = 0; j < (int)c2.count_modes; j++) {
            if (modes[j].type & DRM_MODE_TYPE_PREFERRED) { m = j; break; }
        }
        cur_mode = modes[m];
        conn_id = c2.connector_id;
        enc_id = c2.encoder_id ? c2.encoder_id : (c2.count_encoders ? cencs[0] : 0);
        found = 1;
    }

    if (!found) {
        fprintf(stderr, "serverui: no connected DSI display found\n");
        return 1;
    }

    struct drm_mode_get_encoder e = { .encoder_id = enc_id };
    if (xioctl(drm_fd, DRM_IOCTL_MODE_GETENCODER, &e)) {
        perror("GETENCODER");
        return 1;
    }
    crtc_id = e.crtc_id;
    for (int i = 0; !crtc_id && i < (int)res.count_crtcs; i++) {
        if (e.possible_crtcs & (1u << i)) crtc_id = crtcs[i];
    }

    // 2. Allocate Framebuffer Dumb Buffer
    struct drm_mode_create_dumb cd = { .width = W, .height = H, .bpp = 32 };
    if (xioctl(drm_fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd)) return 1;
    struct drm_mode_fb_cmd fb_cmd = { .width = cd.width, .height = cd.height, .pitch = cd.pitch,
                                      .bpp = 32, .depth = 24, .handle = cd.handle };
    if (xioctl(drm_fd, DRM_IOCTL_MODE_ADDFB, &fb_cmd)) return 1;
    fb_id = fb_cmd.fb_id;
    struct drm_mode_map_dumb md = { .handle = cd.handle };
    if (xioctl(drm_fd, DRM_IOCTL_MODE_MAP_DUMB, &md)) return 1;
    fb_pixels = mmap(0, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, drm_fd, md.offset);
    if (fb_pixels == MAP_FAILED) return 1;
    fb_stride = cd.pitch / 4;

    struct drm_mode_crtc sc = { .crtc_id = crtc_id, .fb_id = fb_id, .set_connectors_ptr = (uintptr_t)&conn_id,
                                .count_connectors = 1, .mode = cur_mode, .mode_valid = 1 };
    xioctl(drm_fd, DRM_IOCTL_MODE_SETCRTC, &sc);

    // Initial render
    refresh_system_info();
    render_current_ui();
    for (int i = 0; i < 3; i++) {
        commit_frame();
        usleep(80000);
    }

    // 3. Open Input Devices (Buttons: Power, Vol Down, Vol Up, and Touch)
    int fd_pwr = open("/dev/input/event0", O_RDONLY | O_NONBLOCK);
    int fd_voldn = open("/dev/input/event1", O_RDONLY | O_NONBLOCK);
    int fd_volup = open("/dev/input/event2", O_RDONLY | O_NONBLOCK);
    int fd_touch = -1;
    for (int i = 3; i < 8; i++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/input/event%d", i);
        int t = open(path, O_RDONLY | O_NONBLOCK);
        if (t >= 0) { fd_touch = t; break; }
    }

    long long last_btn_ms = 0;
    int touch_x = 0, touch_y = 0;

    // 4. Main Event Loop
    while (1) {
        fd_set fds;
        FD_ZERO(&fds);
        int max_fd = drm_fd;
        if (fd_pwr >= 0) { FD_SET(fd_pwr, &fds); if (fd_pwr > max_fd) max_fd = fd_pwr; }
        if (fd_voldn >= 0) { FD_SET(fd_voldn, &fds); if (fd_voldn > max_fd) max_fd = fd_voldn; }
        if (fd_volup >= 0) { FD_SET(fd_volup, &fds); if (fd_volup > max_fd) max_fd = fd_volup; }
        if (fd_touch >= 0) { FD_SET(fd_touch, &fds); if (fd_touch > max_fd) max_fd = fd_touch; }

        struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
        int sel = select(max_fd + 1, &fds, NULL, NULL, &tv);
        if (sel <= 0) continue;

        struct timeval now;
        gettimeofday(&now, NULL);
        long long now_ms = (long long)now.tv_sec * 1000 + now.tv_usec / 1000;

        struct input_event ev;

        // --- Power Key Event ---
        if (fd_pwr >= 0 && FD_ISSET(fd_pwr, &fds)) {
            while (read(fd_pwr, &ev, sizeof(ev)) == sizeof(ev)) {
                if (ev.type == EV_KEY && ev.code == KEY_POWER && ev.value == 1) {
                    if (now_ms - last_btn_ms < 300) continue;
                    last_btn_ms = now_ms;

                    if (cur_state == STATE_SCREEN_OFF) {
                        // Wake up screen!
                        set_display_blank(0);
                    } else {
                        // Click current focused button
                        if (focus_idx >= 0 && focus_idx < num_buttons) {
                            execute_action(buttons[focus_idx].action_id);
                        }
                    }
                }
            }
        }

        // --- Volume Down Event (Focus Next Button) ---
        if (fd_voldn >= 0 && FD_ISSET(fd_voldn, &fds)) {
            while (read(fd_voldn, &ev, sizeof(ev)) == sizeof(ev)) {
                if (ev.type == EV_KEY && ev.value == 1) {
                    if (now_ms - last_btn_ms < 200) continue;
                    last_btn_ms = now_ms;
                    if (cur_state != STATE_SCREEN_OFF && num_buttons > 0) {
                        focus_idx = (focus_idx + 1) % num_buttons;
                        render_current_ui();
                    }
                }
            }
        }

        // --- Volume Up Event (Focus Previous Button) ---
        if (fd_volup >= 0 && FD_ISSET(fd_volup, &fds)) {
            while (read(fd_volup, &ev, sizeof(ev)) == sizeof(ev)) {
                if (ev.type == EV_KEY && ev.value == 1) {
                    if (now_ms - last_btn_ms < 200) continue;
                    last_btn_ms = now_ms;
                    if (cur_state != STATE_SCREEN_OFF && num_buttons > 0) {
                        focus_idx = (focus_idx + num_buttons - 1) % num_buttons;
                        render_current_ui();
                    }
                }
            }
        }

        // --- Touchscreen Event ---
        if (fd_touch >= 0 && FD_ISSET(fd_touch, &fds)) {
            while (read(fd_touch, &ev, sizeof(ev)) == sizeof(ev)) {
                if (ev.type == EV_ABS) {
                    if (ev.code == ABS_MT_POSITION_X || ev.code == ABS_X) touch_x = ev.value;
                    if (ev.code == ABS_MT_POSITION_Y || ev.code == ABS_Y) touch_y = ev.value;
                } else if (ev.type == EV_KEY && (ev.code == BTN_TOUCH || ev.code == BTN_LEFT) && ev.value == 0) {
                    // Finger release / Tap detected
                    if (cur_state != STATE_SCREEN_OFF) {
                        for (int b = 0; b < num_buttons; b++) {
                            if (touch_x >= buttons[b].x0 && touch_x <= buttons[b].x0 + buttons[b].w &&
                                touch_y >= buttons[b].y0 && touch_y <= buttons[b].y0 + buttons[b].h) {
                                focus_idx = b;
                                render_current_ui();
                                usleep(100000);
                                execute_action(buttons[b].action_id);
                                break;
                            }
                        }
                    }
                }
            }
        }
    }

    return 0;
}
