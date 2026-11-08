#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <linux/input.h>
#include <sys/time.h>
#include <sys/stat.h>

#define BACKLIGHT_PATH "/sys/class/backlight/ae94000.dsi.0/brightness"
#define SCREEN_OFF_FLAG "/run/screen_off"
#define SAVED_BRIGHTNESS_FILE "/run/saved_brightness"

static int screen_on = 1;
static int saved_brightness = 511;

static int find_pwrkey_device(char *out_path, size_t max_len) {
    char path[256];
    char name[128];
    for (int i = 0; i < 32; i++) {
        snprintf(path, sizeof(path), "/sys/class/input/event%d/device/name", i);
        FILE *f = fopen(path, "r");
        if (f) {
            if (fgets(name, sizeof(name), f)) {
                if (strstr(name, "pwrkey") || strstr(name, "Power")) {
                    fclose(f);
                    snprintf(out_path, max_len, "/dev/input/event%d", i);
                    return 0;
                }
            }
            fclose(f);
        }
    }
    // Fallback
    snprintf(out_path, max_len, "/dev/input/event0");
    return 0;
}

static void set_screen(int on) {
    screen_on = on;
    if (!screen_on) {
        // Turning OFF
        FILE *fb = fopen(BACKLIGHT_PATH, "r");
        if (fb) {
            int b = 0;
            if (fscanf(fb, "%d", &b) == 1 && b > 0) {
                saved_brightness = b;
            }
            fclose(fb);
        }
        // Save brightness
        FILE *fs = fopen(SAVED_BRIGHTNESS_FILE, "w");
        if (fs) {
            fprintf(fs, "%d\n", saved_brightness);
            fclose(fs);
        }
        // Blank backlight
        fb = fopen(BACKLIGHT_PATH, "w");
        if (fb) {
            fprintf(fb, "0\n");
            fclose(fb);
        }
        // Flag for status loop to sleep
        int fd = open(SCREEN_OFF_FLAG, O_CREAT | O_WRONLY | O_TRUNC, 0644);
        if (fd >= 0) close(fd);
    } else {
        // Turning ON
        unlink(SCREEN_OFF_FLAG);
        // Restore brightness
        if (saved_brightness <= 0) {
            FILE *fs = fopen(SAVED_BRIGHTNESS_FILE, "r");
            if (fs) {
                fscanf(fs, "%d", &saved_brightness);
                fclose(fs);
            }
            if (saved_brightness <= 0) saved_brightness = 511;
        }
        FILE *fb = fopen(BACKLIGHT_PATH, "w");
        if (fb) {
            fprintf(fb, "%d\n", saved_brightness);
            fclose(fb);
        }
    }
}

int main(int argc, char **argv) {
    char dev_path[128] = {0};
    int fd = -1;

    // Retry opening power key device
    for (int retry = 0; retry < 15; retry++) {
        find_pwrkey_device(dev_path, sizeof(dev_path));
        fd = open(dev_path, O_RDONLY);
        if (fd >= 0) break;
        usleep(500000);
    }

    if (fd < 0) {
        perror("powerbuttond: failed to open power key event device");
        return 1;
    }

    struct input_event ev;
    long long last_press_ms = 0;

    while (read(fd, &ev, sizeof(ev)) == sizeof(ev)) {
        if (ev.type == EV_KEY && ev.code == KEY_POWER && ev.value == 1) {
            // Key pressed
            struct timeval tv;
            gettimeofday(&tv, NULL);
            long long now_ms = (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
            if (now_ms - last_press_ms < 350) {
                // Debounce
                continue;
            }
            last_press_ms = now_ms;

            // Toggle screen
            set_screen(!screen_on);
        }
    }

    close(fd);
    return 0;
}
