// InkSight — ESP32-S3-RLCD-4.2 (ST7305 reflective LCD) firmware
// Always-on display build: no deep sleep, no wakeup logic, boot screen first.
// https://github.com/datascale-ai/inksight

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <driver/gpio.h>
#include <new>
#include <WiFi.h>
#include "esp_log.h"

#include "config.h"
#if PIN_RGB_LED >= 0
#include <esp32-hal-rgb-led.h>
#endif
#include "network.h"
#include "storage.h"
#include "portal.h"
#include "epd_driver.h"
#include "display.h"
#include "offline_cache.h"
#include <time.h>       // time()/localtime_r for the live clock repaint in loop()

// ── Shared framebuffer (1bpp, black=0 / white=1, MSB-first) ──
uint8_t imgBuf[IMG_BUF_LEN];

// ── Device state machine ────────────────────────────────────
enum class DeviceState : uint8_t {
    BOOT,
    PORTAL,
    CONNECTING,
    FETCHING,
    DISPLAYING,
    REFRESHING,
    ERROR,
};

enum class PortalEntryReason : uint8_t {
    MANUAL,
    AUTO_WIFI_FAILURE,
};

struct DeviceContext {
    DeviceState state = DeviceState::BOOT;

    // Button state (BOOT = GPIO0, KEY = GPIO18; PWR is a hardware power switch)
    unsigned long bootPressStart = 0;
    unsigned long keyPressStart = 0;
    bool ignoreBootUntilRelease = false;
    bool ignoreKeyUntilRelease = false;
    bool liveMode = false;
    unsigned long temporaryOnlineUntil = 0;
    unsigned long lastLivePollAt = 0;
    unsigned long lastLiveWiFiRetryAt = 0;

    // Timing
    unsigned long setupDoneAt = 0;
    unsigned long lastClockTick = 0;
    unsigned long lastNtpSyncAt = 0;
    unsigned long portalStartedAt = 0;
    unsigned long portalTimeoutMs = 0;

    // Pending actions (set by button handler, consumed by loop)
    bool wantRefresh = false;
    String currentRenderedModeId;
};

static DeviceContext ctx;

// App view: device boots into the todo list; the KEY button switches to the
// image viewer (and cycles images). Used by the v2 backend integration.
enum class AppView : uint8_t { TODO, IMAGE, SETTINGS };
static AppView g_view = AppView::TODO;

// Settings menu (two-level): 一级=系统设置, 二级=重新配网 / 本机MAC地址 / 当前WiFi名称
static const int SETTINGS_ITEM_COUNT = 3;
static int g_settingsCursor = 0;            // 0..2, highlighted 二级 item
static int g_settingsDetail = -1;           // -1 none; 1=MAC; 2=WiFi name (info shown)
static AppView g_viewBeforeSettings = AppView::TODO;

// Todo pagination (v2): fixed 6 rows per page; KEY short-press pages through.
static const int TODO_PER_PAGE = 6;
static int g_todoPage = 0;
static int g_todoTotalPages = 1;

// Cache of the last successfully fetched todo items. Lets the clock/battery be
// repainted live (every minute) in loop() WITHOUT re-querying the backend.
// The TodoItem text/remind pointers reference static buffers in network.cpp
// (g_todoText/g_todoRem) that persist across fetches, so they stay valid.
static TodoItem g_todoItems[TODO_MAX];
static int g_todoCount = 0;

// Minute of day (hour*60 + min) shown on the last paint; drives the live tick.
static int g_lastPaintMinute = -1;

// Battery % from a divider-scaled voltage (matches board ref 03_ADC_Test: 3.0V
// empty, 4.12V full, linear). Used by both the fetch paint and the live repaint.
static int batteryPctFromVoltage(float vb) {
    int pct = (int)((vb - 3.0f) / (4.12f - 3.0f) * 100.0f);
    if (pct < 0) pct = 0; else if (pct > 100) pct = 100;
    return pct;
}

// Current Beijing minute-of-day (TZ is set to CST-8 in syncNTP()).
static int currentMinuteOfDay() {
    time_t t = time(nullptr);
    struct tm ti; localtime_r(&t, &ti);
    return ti.tm_hour * 60 + ti.tm_min;
}

// ── Activity flags (focus listening / always active) ────────
static bool focusListening = false;
static bool alwaysActive = false;

// ── Timing helpers (device is always awake; no deep sleep) ──
static unsigned long refreshIntervalMs() {
#if DEBUG_MODE
    return (unsigned long)DEBUG_REFRESH_MIN * 60000UL;
#else
    return (unsigned long)cfgSleepMin * 60000UL;
#endif
}

static bool temporaryOnlineActive(unsigned long now = millis()) {
    return ctx.temporaryOnlineUntil != 0 && (long)(now - ctx.temporaryOnlineUntil) < 0;
}
static bool temporaryOnlineExpired(unsigned long now = millis()) {
    return ctx.temporaryOnlineUntil != 0 && (long)(now - ctx.temporaryOnlineUntil) >= 0;
}
static void clearTemporaryOnlineWindow() { ctx.temporaryOnlineUntil = 0; }
static void extendTemporaryOnlineWindow(const char *reason) {
    ctx.temporaryOnlineUntil = millis() + TEMP_ONLINE_WINDOW_MS;
    ctx.liveMode = true;
    ctx.lastLivePollAt = 0;
    ctx.lastLiveWiFiRetryAt = 0;
    log_printf("[LIVE] 临时在线窗口：%lu 分钟", TEMP_ONLINE_WINDOW_MS / 60000UL);
    if (reason && reason[0]) log_printf(" (%s)", reason);
    log_printf("\n");
}

// ── Activity flags from backend ─────────────────────────────
static bool refreshActivityFlags() {
    bool enabled = false, always = false;
    if (!fetchFocusListeningFlag(&enabled, &always)) {
        log_printf("[FOCUS] 获取失败，沿用上次标志\n");
        return false;
    }
    focusListening = enabled;
    alwaysActive = always;
    log_printf("[FOCUS] 监听=%d 常驻活跃=%d\n", enabled, always);
    return true;
}

// ── Content checksum (skip redundant repaints) ──────────────
static uint32_t lastContentChecksum = 0;
static int lastRenderedPeriod = -1;
static uint32_t computeChecksum(const uint8_t *buf, int len) {
    uint32_t sum = 0;
    for (int i = 0; i < len; i++) sum = sum * 31 + buf[i];
    return sum;
}

// ── Initialization / boot screen ────────────────────────────
// Shown immediately on power-on, before any WiFi / network work.
static void showBootScreen() {
    memset(imgBuf, 0xFF, IMG_BUF_LEN);  // white background
    drawText("InkSight", W * 28 / 100, H * 36 / 100, 3);
    drawText("Initializing...", W * 16 / 100, H * 54 / 100, 2);
    smartDisplay(imgBuf);
    delay(800);  // give the user a moment to see it
}

// ── Forward declarations ────────────────────────────────────
static void checkConfigButton();
static void refreshTodoView(bool forceRepaint);
static void triggerImmediateRefresh(bool nextMode = false, bool keepWiFi = false, bool skipNtp = false);
static void handleLiveMode();
static bool waitForContentReady();
static void handleFailure(const char *reason);
static void handleWiFiFailure();
static void enterPortalMode(PortalEntryReason reason = PortalEntryReason::MANUAL);
static void checkPortalTimeout();

// ── LED feedback ────────────────────────────────────────────
static void ledInit() {
#if PIN_LED >= 0
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);
#endif
#if PIN_RGB_LED >= 0
    neopixelWrite(PIN_RGB_LED, 0, 0, 0);
#endif
}

static void ledFeedback(const char *pattern) {
#if PIN_LED < 0
    (void)pattern;
#if PIN_RGB_LED >= 0
    neopixelWrite(PIN_RGB_LED, 0, 0, 0);
#endif
    return;
#else
    if (strcmp(pattern, "ack") == 0) {
        for (int i = 0; i < 2; i++) {
            digitalWrite(PIN_LED, HIGH); delay(80);
            digitalWrite(PIN_LED, LOW);  delay(80);
        }
    } else if (strcmp(pattern, "connecting") == 0) {
        digitalWrite(PIN_LED, HIGH); delay(200);
        digitalWrite(PIN_LED, LOW);  delay(200);
    } else if (strcmp(pattern, "downloading") == 0) {
        for (int i = 0; i < 3; i++) {
            digitalWrite(PIN_LED, HIGH); delay(150);
            digitalWrite(PIN_LED, LOW);  delay(150);
        }
    } else if (strcmp(pattern, "success") == 0) {
        digitalWrite(PIN_LED, HIGH); delay(1000);
        digitalWrite(PIN_LED, LOW);
    } else if (strcmp(pattern, "fail") == 0) {
        for (int i = 0; i < 5; i++) {
            digitalWrite(PIN_LED, HIGH); delay(60);
            digitalWrite(PIN_LED, LOW);  delay(60);
        }
    } else if (strcmp(pattern, "favorite") == 0) {
        digitalWrite(PIN_LED, HIGH); delay(2000);
        digitalWrite(PIN_LED, LOW);
    } else if (strcmp(pattern, "portal") == 0) {
        digitalWrite(PIN_LED, HIGH);
    } else if (strcmp(pattern, "off") == 0) {
        digitalWrite(PIN_LED, LOW);
    }
#endif
}

static void enterPortalMode(PortalEntryReason reason) {
    g_userAborted = false;
    String mac = WiFi.macAddress();
    String apName = "InkSight-" + mac.substring(mac.length() - 5);
    apName.replace(":", "");

    ctx.liveMode = false;
    clearTemporaryOnlineWindow();
    ctx.wantRefresh = false;
    ctx.bootPressStart = 0;
    ctx.keyPressStart = 0;

    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);

    ledFeedback("portal");
    startCaptivePortal();
    ctx.state = DeviceState::PORTAL;
    ctx.portalStartedAt = millis();
    ctx.portalTimeoutMs = (reason == PortalEntryReason::MANUAL)
        ? PORTAL_MANUAL_TIMEOUT_MS : PORTAL_AUTO_TIMEOUT_MS;
    log_printf("[PORTAL] 热点：%s（超时 %lums）\n", apName.c_str(), ctx.portalTimeoutMs);
}

static void checkPortalTimeout() {
    if (ctx.state != DeviceState::PORTAL || ctx.portalStartedAt == 0 || ctx.portalTimeoutMs == 0) {
        return;
    }
    unsigned long now = millis();
    if (now - ctx.portalStartedAt < ctx.portalTimeoutMs) {
        return;
    }
    log_printf("[PORTAL] 超时 %lus——重启重试\n", ctx.portalTimeoutMs / 1000UL);
    ctx.portalStartedAt = 0;
    ctx.portalTimeoutMs = 0;
    delay(500);
    ESP.restart();
}

// ── Failure handler ─────────────────────────────────────────
static void showFailureDiagnostic(const char *reason) {
    char l2[64], l3[64];
    snprintf(l2, sizeof(l2), "SSID: %.40s", cfgSSID.c_str());
    snprintf(l3, sizeof(l3), "URL: %.44s", cfgServer.c_str());
    showDiagnostic(reason, l2, l3, "Hold KEY to reconfigure");
}

static void handleFailure(const char *reason) {
    log_printf("[DIAG] %s | SSID=%s | 服务器=%s\n",
                  reason, cfgSSID.c_str(), cfgServer.c_str());
    showFailureDiagnostic(reason);

    if (cacheLoad(imgBuf, IMG_BUF_LEN)) {
        log_printf("显示缓存内容（离线模式）\n");
        const int offlineScale = 2;
        const int offlineLen = 7;
        const int offlineWidth = offlineLen * (5 * offlineScale + offlineScale) - offlineScale;
        const int offlineX = W - offlineWidth - 4;
        const int offlineY = (H * 12 / 100) + 2;
        drawText("OFFLINE", offlineX, offlineY, offlineScale);
        syncNTP();
        smartDisplay(imgBuf);
        ledFeedback("success");
        updateTimeDisplay();
        lastRenderedPeriod = currentPeriodIndex();
        ctx.lastClockTick = millis();
        ctx.state = DeviceState::DISPLAYING;
        ctx.setupDoneAt = millis();
        return;
    }

    // No cache and server down: stay in DISPLAYING so loop() retries on the
    // next refresh interval (device is always awake, no deep sleep).
    log_printf("无缓存内容；下个刷新周期重试\n");
    ctx.state = DeviceState::ERROR;
    ctx.setupDoneAt = millis();
}

// ── WiFi failure handler ────────────────────────────────────
// All saved WiFi networks failed to associate. Do a few quick in-place retry
// sweeps to ride out a brief blip (e.g. router rebooting), then fall back to
// the captive portal so the user can fix or add credentials.
static void handleWiFiFailure() {
    for (int i = 0; i < WIFI_PORTAL_RETRY_SWEEPS; i++) {
        log_printf("[DIAG] WiFi 不可达，快速重试 %d/%d（%lus 后）\n",
                      i + 1, WIFI_PORTAL_RETRY_SWEEPS, WIFI_PORTAL_RETRY_DELAY_MS / 1000);
        delay(WIFI_PORTAL_RETRY_DELAY_MS);
        if (connectWiFi()) {
            log_printf("[DIAG] WiFi 已恢复\n");
            return;
        }
        if (g_userAborted) break;
    }
    log_printf("[DIAG] WiFi 仍不可达 -> 配网门户\n");
    enterPortalMode(PortalEntryReason::AUTO_WIFI_FAILURE);
}

// ── Todo view (v2 backend) ──────────────────────────────────
// Fetches the latest todos, slices to the current page (TODO_PER_PAGE rows),
// and repaints only when the content actually changed (unless forceRepaint).
static void refreshTodoView(bool forceRepaint) {
    TodoItem todoItems[TODO_MAX];
    int todoCount = 0;
    if (!fetchTodos(todoItems, todoCount, TODO_MAX) || todoCount == 0) {
        log_printf("[TODO] 拉取为空/失败；保持当前屏幕\n");
        return;
    }
    // Cache items so the live clock/battery repaint (loop) can reuse them
    // without hitting the backend again.
    g_todoCount = todoCount;
    for (int i = 0; i < todoCount; i++) g_todoItems[i] = todoItems[i];

    int totalPages = (todoCount + TODO_PER_PAGE - 1) / TODO_PER_PAGE;
    if (totalPages < 1) totalPages = 1;
    if (g_todoPage >= totalPages) g_todoPage = 0;
    g_todoTotalPages = totalPages;

    int start = g_todoPage * TODO_PER_PAGE;
    int n = todoCount - start;
    if (n > TODO_PER_PAGE) n = TODO_PER_PAGE;

    int pct = batteryPctFromVoltage(readBatteryVoltage());
    bool wifi = (WiFi.status() == WL_CONNECTED);
    renderTodoScreen(g_todoItems + start, n, g_todoPage, totalPages, pct, wifi, false);
    uint32_t cs = computeChecksum(imgBuf, IMG_BUF_LEN);
    if (forceRepaint || cs != lastContentChecksum) {
        smartDisplay(imgBuf);
        lastContentChecksum = cs;
        cacheSave(imgBuf, IMG_BUF_LEN);
        g_lastPaintMinute = currentMinuteOfDay();
    }
    log_printf("[TODO] 已渲染第 %d/%d 页（%d 项）\n", g_todoPage + 1, totalPages, n);
}

// Repaint the current todo page with the LIVE clock (the status bar reads the
// system RTC via time()/localtime_r) and a FRESH battery reading, WITHOUT
// re-querying the backend. Driven from loop() whenever the displayed minute
// changes, so the clock ticks and the battery % stays current. (RLCD has no
// partial refresh, so this is a full-screen repaint — the same idea as the
// board example 04_I2C_PCF85063, which re-reads the time every loop() tick.)
static void repaintTodoView() {
    if (g_todoCount <= 0) return;
    int totalPages = g_todoTotalPages < 1 ? 1 : g_todoTotalPages;
    int start = g_todoPage * TODO_PER_PAGE;
    int n = g_todoCount - start;
    if (n > TODO_PER_PAGE) n = TODO_PER_PAGE;
    if (n < 0) n = 0;
    int pct = batteryPctFromVoltage(readBatteryVoltage());
    bool wifi = (WiFi.status() == WL_CONNECTED);
    renderTodoScreen(g_todoItems + start, n, g_todoPage, totalPages, pct, wifi, true);
    lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
    g_lastPaintMinute = currentMinuteOfDay();
    // Note: we intentionally do NOT cacheSave() here (content is unchanged);
    // only the status-bar battery text differs, which is irrelevant offline.
}

// ── Settings view (two-level menu) ─────────────────────────
static int liveBatteryPct() { return batteryPctFromVoltage(readBatteryVoltage()); }
static bool liveWifi() { return (WiFi.status() == WL_CONNECTED); }

// BOOT short click (non-settings): cycle to the next top-level page.
// BOOT long press  (non-settings): cycle to the previous top-level page.
// (With only TODO and IMAGE as page views these two are equivalent toggles,
//  but the semantics are kept distinct for clarity / future pages.)
static void nextPage() {
    g_view = (g_view == AppView::IMAGE) ? AppView::TODO : AppView::IMAGE;
    if (g_view == AppView::IMAGE) triggerImmediateRefresh(false);
    else refreshTodoView(true);
    log_printf("[VIEW] 下一页 -> %s\n", g_view == AppView::TODO ? "TODO" : "IMAGE");
}
static void prevPage() {
    g_view = (g_view == AppView::IMAGE) ? AppView::TODO : AppView::IMAGE;
    if (g_view == AppView::IMAGE) triggerImmediateRefresh(false);
    else refreshTodoView(true);
    log_printf("[VIEW] 上一页 -> %s\n", g_view == AppView::TODO ? "TODO" : "IMAGE");
}

static void enterSettings() {
    if (g_view != AppView::SETTINGS) g_viewBeforeSettings = g_view;
    g_view = AppView::SETTINGS;
    g_settingsCursor = 0;
    g_settingsDetail = -1;
    renderSettingsScreen(g_settingsCursor, g_settingsDetail, liveBatteryPct(), liveWifi());
    lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
    g_lastPaintMinute = currentMinuteOfDay();
    log_printf("[SETTINGS] 已进入\n");
}
static void exitSettings() {
    g_view = g_viewBeforeSettings;
    g_settingsDetail = -1;
    if (g_view == AppView::IMAGE) triggerImmediateRefresh(false);
    else refreshTodoView(true);
    log_printf("[SETTINGS] 已退出\n");
}
// BOOT short click inside settings: move cursor to the next (down) 二级 item.
static void settingsCursorNext() {
    g_settingsCursor = (g_settingsCursor + 1) % SETTINGS_ITEM_COUNT;
    g_settingsDetail = -1;
    renderSettingsScreen(g_settingsCursor, g_settingsDetail, liveBatteryPct(), liveWifi());
    lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
    g_lastPaintMinute = currentMinuteOfDay();
    log_printf("[SETTINGS] 光标=%d\n", g_settingsCursor);
}
// KEY short click inside settings: confirm the highlighted item.
static void settingsConfirm() {
    int item = g_settingsCursor;
    if (item == 0) {                       // 重新配网 -> captive portal
        log_printf("[SETTINGS] 重新配网 -> portal\n");
        enterPortalMode(PortalEntryReason::MANUAL);
        return;
    }
    g_settingsDetail = item;               // 1=MAC, 2=WiFi name (read-only info)
    renderSettingsScreen(g_settingsCursor, g_settingsDetail, liveBatteryPct(), liveWifi());
    lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
    g_lastPaintMinute = currentMinuteOfDay();
    log_printf("[SETTINGS] 确认第 %d 项（详情）\n", item);
}
static void repaintSettingsView() {
    renderSettingsScreen(g_settingsCursor, g_settingsDetail, liveBatteryPct(), liveWifi());
    lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
    g_lastPaintMinute = currentMinuteOfDay();
}

// ── Live mode (temporary online window) ─────────────────────
static void handleLiveMode() {
    if (!ctx.liveMode) return;

    unsigned long now = millis();
    if (ctx.temporaryOnlineUntil != 0 && alwaysActive) {
        clearTemporaryOnlineWindow();
    }
    if (temporaryOnlineExpired(now) && !focusListening && !alwaysActive) {
        ctx.liveMode = false;
        clearTemporaryOnlineWindow();
        if (WiFi.status() == WL_CONNECTED) {
            postRuntimeMode("interval");
        }
        log_printf("[LIVE] 临时在线窗口已过期，返回间隔模式\n");
        return;
    }
    if (WiFi.status() != WL_CONNECTED) {
        if (now - ctx.lastLiveWiFiRetryAt >= (unsigned long)LIVE_WIFI_RETRY_MS) {
            ctx.lastLiveWiFiRetryAt = now;
            ledFeedback("connecting");
            if (connectWiFi()) {
                log_printf("[LIVE] WiFi 已连接\n");
            } else {
                log_printf("[LIVE] WiFi 重连失败\n");
            }
        }
        return;
    }

    if (ctx.lastLivePollAt != 0 &&
        now - ctx.lastLivePollAt < (unsigned long)LIVE_POLL_MS) {
        return;
    }
    ctx.lastLivePollAt = now;

    bool shouldExitLive = false;
    if (hasPendingRemoteAction(&shouldExitLive)) {
        log_printf("[LIVE] 检测到待处理操作，立即刷新\n");
        refreshActivityFlags();
        triggerImmediateRefresh(false, true);
        ctx.setupDoneAt = millis();
        if (!focusListening && !alwaysActive) {
            ctx.liveMode = false;
            postRuntimeMode("interval");
        }
        return;
    }
    if (shouldExitLive && ctx.temporaryOnlineUntil == 0) {
        ctx.liveMode = false;
        postRuntimeMode("interval");
        WiFi.disconnect(true);
        WiFi.mode(WIFI_OFF);
        log_printf("[LIVE] 后端要求间隔模式\n");
        return;
    }

    if (millis() - ctx.setupDoneAt >= refreshIntervalMs()) {
        log_printf("[LIVE] 备用 %d 分钟已过，刷新内容...\n", cfgSleepMin);
        triggerImmediateRefresh(false, true);
        ctx.setupDoneAt = millis();
        if (!focusListening && !alwaysActive) {
            ctx.liveMode = false;
            postRuntimeMode("interval");
        }
    }
}

// ── Immediate refresh (fetch + display) ─────────────────────
static void triggerImmediateRefresh(bool nextMode, bool keepWiFi, bool skipNtp) {
    log_printf("[REFRESH] 触发立即刷新...\n");
    ledFeedback("ack");
    uint8_t *previousImage = nullptr;
    if (nextMode) {
        previousImage = (uint8_t *)malloc(IMG_BUF_LEN);
        if (previousImage) {
            memcpy(previousImage, imgBuf, IMG_BUF_LEN);
        }
        showModePreview("NEXT");
    }
    auto restorePreviousImage = [&]() {
        if (nextMode && previousImage) {
            memcpy(imgBuf, previousImage, IMG_BUF_LEN);
            log_printf("[REFRESH] 下模式刷新失败，恢复上一张图片\n");
            smartDisplay(imgBuf);
        }
    };
    bool connected = (WiFi.status() == WL_CONNECTED);
    if (!connected) {
        ledFeedback("connecting");
        connected = connectWiFi();
    }
    if (connected) {
        ledFeedback("downloading");
        String renderedModeId;
        bool fetched = fetchBMP(nextMode, nullptr, &renderedModeId);
        if (fetched && renderedModeId.length() > 0) {
            ctx.currentRenderedModeId = renderedModeId;
        }
        bool keepWiFiEffective = keepWiFi;
        if (fetched) {
            cacheSave(imgBuf, IMG_BUF_LEN);

            uint32_t newChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
            if (!skipNtp) {
                syncNTP();
            }
            if (newChecksum == lastContentChecksum && !nextMode) {
                log_printf("内容未变，跳过屏幕刷新\n");
                ledFeedback("success");
            } else {
                log_printf("正在显示新内容...\n");
                smartDisplay(imgBuf);
                lastContentChecksum = newChecksum;
                ledFeedback("success");
                log_printf("显示完成\n");
            }

            lastRenderedPeriod = currentPeriodIndex();
            ctx.lastClockTick = millis();
        } else {
            bool retryReady = false;
            if (keepWiFiEffective && WiFi.status() == WL_CONNECTED) {
                log_printf("拉取失败，在当前 WiFi 上重试...\n");
                retryReady = true;
            } else {
                log_printf("拉取失败，重连后重试...\n");
                WiFi.disconnect(true);
                delay(300);
                retryReady = connectWiFi();
            }
            if (retryReady) {
                fetched = fetchBMP(nextMode, nullptr, &renderedModeId);
                if (fetched) {
                    if (renderedModeId.length() > 0) {
                        ctx.currentRenderedModeId = renderedModeId;
                    }
                    cacheSave(imgBuf, IMG_BUF_LEN);
                    uint32_t retryChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
                    syncNTP();
                    smartDisplay(imgBuf);
                    lastContentChecksum = retryChecksum;
                    lastRenderedPeriod = currentPeriodIndex();
                    ctx.lastClockTick = millis();
                    ledFeedback("success");
                    log_printf("重试成功\n");
                } else {
                    ledFeedback("fail");
                    log_printf("重试也失败，保留旧内容\n");
                    restorePreviousImage();
                }
            } else {
                ledFeedback("fail");
                log_printf("WiFi 重连失败，保留旧内容\n");
                restorePreviousImage();
            }
        }
        if (!keepWiFiEffective) {
            WiFi.disconnect(true);
            WiFi.mode(WIFI_OFF);
        }
    } else {
        ledFeedback("fail");
        log_printf("WiFi 重连失败\n");
        restorePreviousImage();
    }
    if (previousImage) free(previousImage);
}

static bool waitForContentReady() {
    const int maxRetries = 4;
    const int waitMs = 15000;
    for (int i = 0; i < maxRetries; i++) {
        log_printf("[BOOT] 内容未就绪，重试 %d/%d\n", i + 1, maxRetries);
        showError("Generating...");
        unsigned long t0 = millis();
        while (millis() - t0 < (unsigned long)waitMs) {
            if (digitalRead(PIN_KEY_BTN) == LOW) {
                delay(400);
                if (digitalRead(PIN_KEY_BTN) == LOW) {
                    log_printf("[BOOT] 等待期间配置键长按 -> 配网门户\n");
                    enterPortalMode();
                    return false;
                }
            }
            delay(50);
        }
        if (WiFi.status() != WL_CONNECTED) {
            if (!connectWiFi()) {
                if (g_userAborted) {
                    enterPortalMode();
                    return false;
                }
                continue;
            }
        }
        ledFeedback("downloading");
        bool gotFallback = false;
        if (fetchBMP(false, &gotFallback) && !gotFallback) {
            log_printf("[BOOT] 内容已就绪\n");
            return true;
        }
        if (g_userAborted) {
            enterPortalMode();
            return false;
        }
    }
    return false;
}

// ── Button handler (BOOT=GPIO0, KEY=GPIO18) ────────────────
// Mapping (custom key map):
//   BOOT short click : non-settings → next page;  settings → cursor down (next item)
//   BOOT long  press : previous page (or exit settings)
//   KEY  short click : confirm option (settings only)
//   KEY  long  press : enter / exit settings
//   PWR  is a hardware power switch (long=off, click=on) — not readable by firmware.
// Download mode: holding BOOT at power-on is handled by the ROM (strapping),
// so the firmware only sees BOOT as a runtime input.
static void checkConfigButton() {
    if (ctx.state == DeviceState::PORTAL) return;   // portal is phone-driven

    // ── BOOT (GPIO0) ──
    bool bootPressed = (digitalRead(PIN_BOOT_BTN) == LOW);
    if (ctx.ignoreBootUntilRelease) {
        if (!bootPressed) ctx.ignoreBootUntilRelease = false;
        ctx.bootPressStart = 0;
    } else if (bootPressed) {
        if (ctx.bootPressStart == 0) {
            ctx.bootPressStart = millis();
        } else {
            unsigned long hold = millis() - ctx.bootPressStart;
            if (hold >= (unsigned long)CFG_BTN_HOLD_MS) {
                ctx.bootPressStart = 0;
                // long press: previous page / exit settings
                if (g_view == AppView::SETTINGS) exitSettings();
                else prevPage();
                ctx.ignoreBootUntilRelease = true;   // debounce until released
            }
        }
    } else {
        if (ctx.bootPressStart != 0) {
            unsigned long dur = millis() - ctx.bootPressStart;
            ctx.bootPressStart = 0;
            if (dur >= (unsigned long)SHORT_PRESS_MIN_MS && dur < (unsigned long)CFG_BTN_HOLD_MS) {
                if (g_view == AppView::SETTINGS) settingsCursorNext();
                else nextPage();
                ctx.ignoreBootUntilRelease = true;
            }
        }
    }

    // ── KEY (GPIO18) ──
    bool keyPressed = (digitalRead(PIN_KEY_BTN) == LOW);
    if (ctx.ignoreKeyUntilRelease) {
        if (!keyPressed) ctx.ignoreKeyUntilRelease = false;
        ctx.keyPressStart = 0;
    } else if (keyPressed) {
        if (ctx.keyPressStart == 0) {
            ctx.keyPressStart = millis();
        } else {
            unsigned long hold = millis() - ctx.keyPressStart;
            if (hold >= (unsigned long)CFG_BTN_HOLD_MS) {
                ctx.keyPressStart = 0;
                // long press: enter / exit settings
                if (g_view == AppView::SETTINGS) exitSettings();
                else enterSettings();
                ctx.ignoreKeyUntilRelease = true;    // debounce until released
            }
        }
    } else {
        if (ctx.keyPressStart != 0) {
            unsigned long dur = millis() - ctx.keyPressStart;
            ctx.keyPressStart = 0;
            if (dur >= (unsigned long)SHORT_PRESS_MIN_MS && dur < (unsigned long)CFG_BTN_HOLD_MS) {
                // short click: confirm (settings only); no-op elsewhere
                if (g_view == AppView::SETTINGS) settingsConfirm();
                ctx.ignoreKeyUntilRelease = true;
            }
        }
    }
}

// ── 开机横幅（立即打印 + 延时后重打 + 前30秒每5秒重报）──
static const char *FW_DEBUG_TAG = "INKSIGHT_FW_DEBUG_20260714";
static unsigned long g_announceUntil = 0;
static void printBootBanner() {
    log_printf("\n=== InkSight RLCD ===\n");
    log_printf("[BOOT] 固件指纹=%s 构建=%s %s\n",
              FW_DEBUG_TAG, __DATE__, __TIME__);
    log_printf("[BOOT] 按键映射：BOOT(GPIO0) 短按=下一页/下移 长按=上一页/退出 | KEY(GPIO18) 短按=确认 长按=进/出设置\n");
}

// ── setup() ─────────────────────────────────────────────────
void setup() {
    ledInit();
    Serial.begin(115200);
    printBootBanner();   // 第一份：延时前立即打印，最早输出
    delay(3000);         // 等待串口监视器连接
    printBootBanner();   // 第二份：延时期间连上的监视器也能捕获
    g_announceUntil = millis() + 30000UL;  // 前30秒持续重报身份
#if defined(BOARD_PROFILE_ESP32_C3_WROOM02) || defined(BOARD_PROFILE_SMT_WROOM32E) || defined(BOARD_PROFILE_YD_ESP32_S3_N16R8) || defined(BOARD_PROFILE_RLCD_S3)
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
#endif

    gpioInit();
    ctx.state = DeviceState::BOOT;

    epdInit();
    cacheInit();
    log_printf("EPD 就绪\n");

    // 1) Show the initialization / boot screen FIRST (before any network work).
    showBootScreen();

    // 1b) Native todo-list UI demo (UI chrome validation; replaced by
    //     fetchTodos() once the inksight-content backend is wired up).
#ifdef INKSIGHT_TODO_DEMO
    {
        TodoItem demo[] = {
            {"买菜：牛奶 milk 和鸡蛋", false, "09:30"},
            {"14:00 项目评审会议 review", true, "14:00"},
            {"给妈妈打电话 call mom", false, "16:00"},
            {"Git 提交 firmware 代码", false, "18:00"},
            {"阅读《三体》第 3 章", false, "20:00"},
            {"健身 run 30 分钟", false, "21:00"},
        };
        renderTodoScreen(demo, 6, 0, 1, 87, false);
        delay(2500);
    }
#endif

    loadConfig();

    bool forcePortal = false;
    if (digitalRead(PIN_KEY_BTN) == LOW) {
        delay(400);
        forcePortal = (digitalRead(PIN_KEY_BTN) == LOW);
    }

    bool hasConfig = (cfgSSID.length() > 0);

    if (forcePortal || !hasConfig) {
        Serial.println(forcePortal ? "Config button held -> portal"
                                   : "No WiFi config -> portal");
        delay(5000);
        enterPortalMode();
        return;
    }

    if (cfgServer.length() == 0) {
        log_printf("未配置服务器地址 -> 配网门户\n");
        enterPortalMode();
        return;
    }

    ledFeedback("connecting");
    if (!connectWiFi()) {
        if (g_userAborted) {
            log_printf("WiFi 连接期间用户中断 -> 配网门户\n");
            enterPortalMode();
            return;
        }
        ledFeedback("fail");
        handleWiFiFailure();
        return;
    }

    if (!refreshActivityFlags()) {
        focusListening = false;
        alwaysActive = false;
    }
    if (g_userAborted) {
        log_printf("Focus 拉取期间用户中断 -> 配网门户\n");
        enterPortalMode();
        return;
    }

    // ── Default view: todo list pulled from inksight-server ──
    log_printf("正在拉取待办...\n");
    ledFeedback("downloading");
    refreshTodoView(true);   // renders real todos; keeps demo screen if fetch fails
    ledFeedback("success");
    syncNTP();   // after first paint so the screen lights sooner
    log_printf("显示完成\n");
    lastRenderedPeriod = currentPeriodIndex();
    ctx.lastClockTick = millis();

    // ── Always-on: keep WiFi up so button image fetch / todo refresh work ──
    g_view = AppView::TODO;
    ctx.state = DeviceState::DISPLAYING;
    ctx.setupDoneAt = millis();
    log_printf("启动完成（inksight-server 模式），进入主循环（常驻在线）\n");
}

// ── loop() ──────────────────────────────────────────────────
void loop() {
    // ── 诊断心跳：前30秒每5秒（带固件指纹），之后每30秒（轻量）──
    {
        static unsigned long lastHb = 0;
        bool announcing = (millis() < g_announceUntil);
        unsigned long hbInterval = announcing ? 5000UL : 30000UL;
        if (millis() - lastHb >= hbInterval) {
            lastHb = millis();
            if (announcing) {
                log_printf("[HB] 指纹=%s 构建=%s %s 状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d\n",
                          FW_DEBUG_TAG, __DATE__, __TIME__,
                          (int)ctx.state, (int)g_view,
                          (WiFi.status() == WL_CONNECTED) ? "up" : "down",
                          rtcTimeValid() ? 1 : 0, ESP.getFreeHeap());
            } else {
                log_printf("[HB] 状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d\n",
                          (int)ctx.state, (int)g_view,
                          (WiFi.status() == WL_CONNECTED) ? "up" : "down",
                          rtcTimeValid() ? 1 : 0, ESP.getFreeHeap());
            }
        }
    }

    if (ctx.state == DeviceState::PORTAL) {
        handlePortalClients();
        checkPortalTimeout();
        checkConfigButton();
        delay(5);
        return;
    }

    checkConfigButton();

    handleLiveMode();

    unsigned long now = millis();
    bool timeChanged = false;
    while (now - ctx.lastClockTick >= 1000UL) {
        tickTime();
        ctx.lastClockTick += 1000UL;
        timeChanged = true;
    }
    if (timeChanged && cfgSleepMin > 180 && !focusListening) {
        int currentPeriod = currentPeriodIndex();
        if (currentPeriod != lastRenderedPeriod) {
            updateTimeDisplay();
            lastRenderedPeriod = currentPeriod;
        }
    }

    // ── Live clock + battery: repaint the todo view when the minute changes ──
    // The board example 04_I2C_PCF85063 reads the time every loop() tick and
    // refreshes the display. For a HH:MM status bar a per-minute full repaint
    // (RLCD's only refresh mode) is the right granularity to keep the clock
    // ticking and the battery % fresh — without re-querying the backend.
    if (ctx.state == DeviceState::DISPLAYING) {
        if (g_view == AppView::TODO && g_todoCount > 0) {
            int m = currentMinuteOfDay();
            if (m != g_lastPaintMinute) {
                g_lastPaintMinute = m;
                repaintTodoView();
            }
        } else if (g_view == AppView::SETTINGS) {
            // keep the settings clock/battery live too (no backend re-query)
            int m = currentMinuteOfDay();
            if (m != g_lastPaintMinute) {
                g_lastPaintMinute = m;
                repaintSettingsView();
            }
        }
    }

    if (millis() - ctx.setupDoneAt >= refreshIntervalMs() && g_view != AppView::SETTINGS) {
#if DEBUG_MODE
        log_printf("[DEBUG] %d 分钟已过，刷新内容...\n", DEBUG_REFRESH_MIN);
#else
        log_printf("%d 分钟已过，刷新内容...\n", cfgSleepMin);
#endif
#if INKSIGHT_BACKEND_V2
        if (g_view == AppView::TODO) {
            refreshTodoView(false);
        } else {
            triggerImmediateRefresh();
        }
#else
        triggerImmediateRefresh();
#endif
        ctx.setupDoneAt = millis();
    }

    // ── Periodic NTP resync (fixes "00:00" after a restart) ──
    // While the RTC is still at the 1970 epoch we retry aggressively (30s);
    // once a valid time is set we keep it fresh every 5 minutes. On the
    // invalid→valid transition we repaint the current view so the clock shows.
    if (WiFi.status() == WL_CONNECTED) {
        bool rtcInvalid = !rtcTimeValid();
        unsigned long ntpInterval = rtcInvalid ? 30000UL : 300000UL;
        if (millis() - ctx.lastNtpSyncAt >= ntpInterval) {
            bool wasInvalid = rtcInvalid;
            syncNTP();
            ctx.lastNtpSyncAt = millis();
            if (wasInvalid && rtcTimeValid()) {
                log_printf("[NTP] 时钟已校准；重绘当前视图\n");
                if (g_view == AppView::TODO) {
                    refreshTodoView(true);
                } else if (g_view == AppView::SETTINGS) {
                    repaintSettingsView();
                } else {
                    triggerImmediateRefresh(false);
                }
            }
        }
    }

    if (WiFi.status() == WL_CONNECTED) {
        postHeartbeat();
    }

    // Focus alerts (only when focus listening is enabled).
    static unsigned long lastAlertPollAt = 0;
    static bool alertVisible = false;
    static unsigned long alertShownAt = 0;
#if INKSIGHT_IMG_BUF_BYTES_MACRO > 20000
    static uint8_t *alertBackupBuf = nullptr;
    if (!alertBackupBuf) {
        alertBackupBuf = (uint8_t *)malloc(IMG_BUF_LEN);
        if (!alertBackupBuf) {
            log_printf("[MEM] alertBackupBuf 分配失败；Focus 告警已禁用\n");
        }
    }
#else
    static uint8_t alertBackupBufStatic[IMG_BUF_LEN];
    uint8_t *const alertBackupBuf = alertBackupBufStatic;
#endif
    static bool hasAlertBackup = false;

    if (focusListening && alertBackupBuf) {
        unsigned long nowMs = millis();
        if (!alertVisible) {
            const unsigned long ALERT_INTERVAL_MS = 10000UL;
            if (lastAlertPollAt == 0 || nowMs - lastAlertPollAt >= ALERT_INTERVAL_MS) {
                lastAlertPollAt = nowMs;
                memcpy(alertBackupBuf, imgBuf, IMG_BUF_LEN);
                hasAlertBackup = true;
                if (fetchFocusAlertBMP()) {
                    epdDisplayFast(imgBuf);
                    alertVisible = true;
                    alertShownAt = nowMs;
                } else {
                    if (hasAlertBackup) memcpy(imgBuf, alertBackupBuf, IMG_BUF_LEN);
                    hasAlertBackup = false;
                }
            }
        } else {
            const unsigned long ALERT_DISPLAY_MS = 30000UL;
            if (nowMs - alertShownAt >= ALERT_DISPLAY_MS) {
                if (hasAlertBackup) {
                    memcpy(imgBuf, alertBackupBuf, IMG_BUF_LEN);
                    epdDisplayFast(imgBuf);
                }
                hasAlertBackup = false;
                alertVisible = false;
            }
        }
    }

    delay(50);
}
