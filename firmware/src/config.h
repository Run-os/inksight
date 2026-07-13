#ifndef INKSIGHT_CONFIG_H
#define INKSIGHT_CONFIG_H

#include <Arduino.h>

// ── Single target board: ESP32-S3-RLCD-4.2 (ST7305 reflective LCD) ──
// 4.2" ST7305 reflective LCD (400x300), 16MB flash / 8MB PSRAM.
// Display pins match the vendor example (MOSI=12, SCL=11, DC=5, CS=40, RST=41).
#ifndef BOARD_PROFILE_RLCD_S3
#error "This firmware build targets BOARD_PROFILE_RLCD_S3 only"
#endif
#define PIN_EPD_MOSI   12
#define PIN_EPD_SCK    11
#define PIN_EPD_CS     40
#define PIN_EPD_DC     5
#define PIN_EPD_RST    41
#define PIN_EPD_BUSY   -1     // RLCD has no BUSY line
#define PIN_BAT_ADC    4      // battery sense: GPIO4 (ADC1_CH3), 3x divider (Waveshare RLCD-4.2)
#define PIN_CFG_BTN    18     // legacy alias: KEY button (active low, GPIO18)
#define PIN_KEY_BTN     18     // KEY button (active low, GPIO18): confirm / enter-exit settings
#define PIN_BOOT_BTN    0      // BOOT button (active low, GPIO0): next page / cursor / prev page;
                               // also hardware download-mode when held during power-on (ROM, not firmware)
#define PIN_LED        -1
#define PIN_RGB_LED    48

// ── Display constants ────────────────────────────────────────
// 4.2" reflective LCD is fixed at 400x300, 1-bit monochrome.
#define EPD_WIDTH  400
#define EPD_HEIGHT 300

static const int W = EPD_WIDTH;
static const int H = EPD_HEIGHT;
static const int ROW_BYTES   = W / 8;
static const int ROW_STRIDE  = (ROW_BYTES + 3) & ~3;  // BMP row stride (4-byte aligned)
static const int IMG_BUF_LEN = ROW_BYTES * H;

// Monochrome 1bpp only (RLCD has no color buffer).
#define EPD_BPP 1

// Shared framebuffer (defined in main.cpp)
extern uint8_t imgBuf[];

// ── Refresh strategy ─────────────────────────────────────────
static const int FULL_REFRESH_INTERVAL = 10;  // RLCD ignores: every refresh is an equal full repaint

// ── Config defaults ─────────────────────────────────────────
// Default backend host. The old inksight backend is retired; the device now
// talks to inksight-server (FastAPI) hosted at this domain.
static const char *DEFAULT_SERVER  = "https://esp32.122050.xyz";
// Skip TLS certificate validation for the backend domain. The server sits
// behind a reverse proxy (e.g. Caddy/Let's Encrypt) whose root CA won't match
// the hardcoded ROOT_CA. Set to 0 and update certs.h ROOT_CA for strict TLS.
#define BACKEND_TLS_INSECURE 1
// Use inksight-server endpoints (/api/todos, /api/images/*). When set, the
// legacy device-management endpoints (heartbeat, claim-token, runtime, state,
// ota progress, focus-alert) are disabled and /api/render is replaced by the
// image manifest + per-file fetch.
#define INKSIGHT_BACKEND_V2 1
// Max todo items pulled from /api/todos and rendered on the todo screen.
#define TODO_MAX 24
static const int   WIFI_TIMEOUT    = 15000;   // ms
static const int   MAX_WIFI_NETWORKS = 5;     // Max saved WiFi credentials (tried in order on boot)
static const int   HTTP_TIMEOUT    = 30000;   // ms
static const int   CFG_BTN_HOLD_MS = 2000;    // Long press duration to trigger config mode
static const int   SHORT_PRESS_MIN_MS = 50;   // Minimum short press duration (debounce)
static const int   LIVE_POLL_MS = 5000;       // Poll interval for pending remote actions
static const int   LIVE_WIFI_RETRY_MS = 5000; // Retry interval when WiFi is disconnected
static const unsigned long TEMP_ONLINE_WINDOW_MS = 10UL * 60UL * 1000UL;
static const unsigned long PORTAL_AUTO_TIMEOUT_MS = 3UL * 60UL * 1000UL;
static const unsigned long PORTAL_MANUAL_TIMEOUT_MS = 10UL * 60UL * 1000UL;
static const unsigned long HEARTBEAT_INTERVAL_MS = 10UL * 60UL * 1000UL;
static const int   MAX_RETRY_COUNT = 5;       // Max retries before falling back to cached content
// WiFi -> captive portal fallback: when ALL saved networks fail to connect,
// do this many quick in-place retry sweeps (no reboot) before opening the AP.
static const int           WIFI_PORTAL_RETRY_SWEEPS   = 1;
static const unsigned long WIFI_PORTAL_RETRY_DELAY_MS = 3000;
// Progressive retry delays in seconds: 5s, 15s, 30s, 60s, 120s
static const int   RETRY_DELAYS[] = {5, 15, 30, 60, 120};

// ── Time zone ───────────────────────────────────────────────
#define NTP_UTC_OFFSET  (8 * 3600)  // UTC+8 (China Standard Time), adjust for your region

// ── Debug mode ──────────────────────────────────────────────
#define DEBUG_MODE 0  // Set to 1 for fast refresh (1 min), 0 for user config
#if DEBUG_MODE
static const int DEBUG_REFRESH_MIN = 1;  // 1 minute for debugging
#endif

// ── Native todo demo (UI chrome validation) ─────────────────
// Renders a mock todo list on boot so the native UI can be verified on-device
// before the backend (inksight-content) is wired up. Set to 0 once fetchTodos
// is integrated.
#define INKSIGHT_TODO_DEMO 1

// ── Time display region (partial refresh area) ──────────────
// Proportional to screen size (scales across 2.9"/4.2"/7.5")
#define TIME_RGN_X0   (0)
#define TIME_RGN_X1   ((W * 14 / 100) & ~7)
#define TIME_RGN_Y0   (H * 2 / 100)
#define TIME_RGN_Y1   (H * 8 / 100)

#define TIME_TEXT_X   (W * 1 / 100)
#define TIME_TEXT_Y   (H * 4 / 100)

#endif // INKSIGHT_CONFIG_H
