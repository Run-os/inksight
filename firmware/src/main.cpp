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

    // Button state
    unsigned long btnPressStart = 0;
    bool ignoreConfigButtonUntilRelease = false;
    bool liveMode = false;
    unsigned long temporaryOnlineUntil = 0;
    unsigned long lastLivePollAt = 0;
    unsigned long lastLiveWiFiRetryAt = 0;

    // Timing
    unsigned long setupDoneAt = 0;
    unsigned long lastClockTick = 0;
    unsigned long portalStartedAt = 0;
    unsigned long portalTimeoutMs = 0;

    // Pending actions (set by button handler, consumed by loop)
    bool wantRefresh = false;
    String currentRenderedModeId;
};

static DeviceContext ctx;

// App view: device boots into the todo list; the KEY button switches to the
// image viewer (and cycles images). Used by the v2 backend integration.
enum class AppView : uint8_t { TODO, IMAGE };
static AppView g_view = AppView::TODO;

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
    Serial.printf("[LIVE] Temporary online window: %lu min", TEMP_ONLINE_WINDOW_MS / 60000UL);
    if (reason && reason[0]) Serial.printf(" (%s)", reason);
    Serial.println();
}

// ── Activity flags from backend ─────────────────────────────
static bool refreshActivityFlags() {
    bool enabled = false, always = false;
    if (!fetchFocusListeningFlag(&enabled, &always)) {
        Serial.println("[FOCUS] fetch failed, keeping previous flags");
        return false;
    }
    focusListening = enabled;
    alwaysActive = always;
    Serial.printf("[FOCUS] listening=%d alwaysActive=%d\n", enabled, always);
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
    ctx.btnPressStart = 0;

    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);

    ledFeedback("portal");
    startCaptivePortal();
    ctx.state = DeviceState::PORTAL;
    ctx.portalStartedAt = millis();
    ctx.portalTimeoutMs = (reason == PortalEntryReason::MANUAL)
        ? PORTAL_MANUAL_TIMEOUT_MS : PORTAL_AUTO_TIMEOUT_MS;
    Serial.printf("[PORTAL] AP: %s (timeout %lums)\n", apName.c_str(), ctx.portalTimeoutMs);
}

static void checkPortalTimeout() {
    if (ctx.state != DeviceState::PORTAL || ctx.portalStartedAt == 0 || ctx.portalTimeoutMs == 0) {
        return;
    }
    unsigned long now = millis();
    if (now - ctx.portalStartedAt < ctx.portalTimeoutMs) {
        return;
    }
    Serial.printf("[PORTAL] Timeout %lus — rebooting to retry\n", ctx.portalTimeoutMs / 1000UL);
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
    Serial.printf("[DIAG] %s | SSID=%s | Server=%s\n",
                  reason, cfgSSID.c_str(), cfgServer.c_str());
    showFailureDiagnostic(reason);

    if (cacheLoad(imgBuf, IMG_BUF_LEN)) {
        Serial.println("Showing cached content (offline mode)");
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
    Serial.println("No cached content; will retry on next refresh cycle");
    ctx.state = DeviceState::ERROR;
    ctx.setupDoneAt = millis();
}

// ── WiFi failure handler ────────────────────────────────────
// All saved WiFi networks failed to associate. Do a few quick in-place retry
// sweeps to ride out a brief blip (e.g. router rebooting), then fall back to
// the captive portal so the user can fix or add credentials.
static void handleWiFiFailure() {
    for (int i = 0; i < WIFI_PORTAL_RETRY_SWEEPS; i++) {
        Serial.printf("[DIAG] WiFi unreachable, quick retry sweep %d/%d in %lus\n",
                      i + 1, WIFI_PORTAL_RETRY_SWEEPS, WIFI_PORTAL_RETRY_DELAY_MS / 1000);
        delay(WIFI_PORTAL_RETRY_DELAY_MS);
        if (connectWiFi()) {
            Serial.println("[DIAG] WiFi recovered");
            return;
        }
        if (g_userAborted) break;
    }
    Serial.println("[DIAG] WiFi still unreachable -> captive portal");
    enterPortalMode(PortalEntryReason::AUTO_WIFI_FAILURE);
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
        Serial.println("[LIVE] Temporary online window expired, returning to interval mode");
        return;
    }
    if (WiFi.status() != WL_CONNECTED) {
        if (now - ctx.lastLiveWiFiRetryAt >= (unsigned long)LIVE_WIFI_RETRY_MS) {
            ctx.lastLiveWiFiRetryAt = now;
            ledFeedback("connecting");
            if (connectWiFi()) {
                Serial.println("[LIVE] WiFi connected");
            } else {
                Serial.println("[LIVE] WiFi reconnect failed");
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
        Serial.println("[LIVE] Pending action detected, refreshing now");
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
        Serial.println("[LIVE] Backend requested interval mode");
        return;
    }

    if (millis() - ctx.setupDoneAt >= refreshIntervalMs()) {
        Serial.printf("[LIVE] Fallback %d min elapsed, refreshing content...\n", cfgSleepMin);
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
    Serial.println("[REFRESH] Triggering immediate refresh...");
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
            Serial.println("[REFRESH] Restoring previous image after failed next-mode refresh");
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
                Serial.println("Content unchanged, skipping display refresh");
                ledFeedback("success");
            } else {
                Serial.println("Displaying new content...");
                smartDisplay(imgBuf);
                lastContentChecksum = newChecksum;
                ledFeedback("success");
                Serial.println("Display done");
            }

            lastRenderedPeriod = currentPeriodIndex();
            ctx.lastClockTick = millis();
        } else {
            bool retryReady = false;
            if (keepWiFiEffective && WiFi.status() == WL_CONNECTED) {
                Serial.println("Fetch failed, retrying on existing WiFi...");
                retryReady = true;
            } else {
                Serial.println("Fetch failed, retrying after reconnect...");
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
                    Serial.println("Retry succeeded");
                } else {
                    ledFeedback("fail");
                    Serial.println("Retry also failed, keeping old content");
                    restorePreviousImage();
                }
            } else {
                ledFeedback("fail");
                Serial.println("WiFi reconnect failed, keeping old content");
                restorePreviousImage();
            }
        }
        if (!keepWiFiEffective) {
            WiFi.disconnect(true);
            WiFi.mode(WIFI_OFF);
        }
    } else {
        ledFeedback("fail");
        Serial.println("WiFi reconnect failed");
        restorePreviousImage();
    }
    if (previousImage) free(previousImage);
}

static bool waitForContentReady() {
    const int maxRetries = 4;
    const int waitMs = 15000;
    for (int i = 0; i < maxRetries; i++) {
        Serial.printf("[BOOT] Content not ready, retry %d/%d\n", i + 1, maxRetries);
        showError("Generating...");
        unsigned long t0 = millis();
        while (millis() - t0 < (unsigned long)waitMs) {
            if (digitalRead(PIN_CFG_BTN) == LOW) {
                delay(400);
                if (digitalRead(PIN_CFG_BTN) == LOW) {
                    Serial.println("[BOOT] Config button held during wait -> portal");
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
            Serial.println("[BOOT] Content is ready");
            return true;
        }
        if (g_userAborted) {
            enterPortalMode();
            return false;
        }
    }
    return false;
}

// ── Config button handler (KEY on GPIO18) ──────────────────
// Short press (50ms–2s): advance to next server mode (next page).
// Long press (>= 2s): enter config portal.
static void checkConfigButton() {
    bool isPressed = (digitalRead(PIN_CFG_BTN) == LOW);

    if (ctx.ignoreConfigButtonUntilRelease) {
        if (!isPressed) {
            ctx.ignoreConfigButtonUntilRelease = false;
        }
        ctx.btnPressStart = 0;
        return;
    }

    if (isPressed) {
        if (ctx.btnPressStart == 0) {
            ctx.btnPressStart = millis();
        } else {
            unsigned long holdTime = millis() - ctx.btnPressStart;
            if (holdTime >= (unsigned long)CFG_BTN_HOLD_MS) {
                Serial.printf("Config button held for %dms, entering portal...\n", CFG_BTN_HOLD_MS);
                ctx.btnPressStart = 0;
                enterPortalMode();
            }
        }
    } else {
        if (ctx.btnPressStart != 0) {
            unsigned long pressDuration = millis() - ctx.btnPressStart;
            ctx.btnPressStart = 0;

            if (pressDuration >= (unsigned long)SHORT_PRESS_MIN_MS &&
                pressDuration < (unsigned long)CFG_BTN_HOLD_MS) {
                if (ctx.state != DeviceState::PORTAL) {
                    Serial.printf("[BTN] Short press %lums\n", pressDuration);
#if INKSIGHT_BACKEND_V2
                    if (g_view == AppView::TODO) {
                        Serial.println("[BTN] TODO -> IMAGE view");
                        g_view = AppView::IMAGE;
                        triggerImmediateRefresh(false);  // fetch first image
                    } else {
                        Serial.println("[BTN] IMAGE -> next image");
                        triggerImmediateRefresh(true);   // next image
                    }
#else
                    triggerImmediateRefresh(true);
#endif
                    ctx.ignoreConfigButtonUntilRelease = true;  // debounce until released
                }
            }
        }
    }
}

// ── setup() ─────────────────────────────────────────────────
void setup() {
    ledInit();
    Serial.begin(115200);
#if defined(BOARD_PROFILE_RLCD_S3)
    delay(800);    // RLCD powers on instantly; no long e-ink settle delay needed
#else
    delay(3000);
#endif
#if defined(BOARD_PROFILE_ESP32_C3_WROOM02) || defined(BOARD_PROFILE_SMT_WROOM32E) || defined(BOARD_PROFILE_YD_ESP32_S3_N16R8) || defined(BOARD_PROFILE_RLCD_S3)
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
#endif
    Serial.println("\n=== InkSight RLCD ===");

    gpioInit();
    ctx.state = DeviceState::BOOT;

    epdInit();
    cacheInit();
    Serial.println("EPD ready");

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
        renderTodoScreen(demo, 6, 0, 3, 87);
        delay(2500);
    }
#endif

    loadConfig();

    bool forcePortal = false;
    if (digitalRead(PIN_CFG_BTN) == LOW) {
        delay(400);
        forcePortal = (digitalRead(PIN_CFG_BTN) == LOW);
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
        Serial.println("No server URL configured -> portal");
        enterPortalMode();
        return;
    }

    ledFeedback("connecting");
    if (!connectWiFi()) {
        if (g_userAborted) {
            Serial.println("User aborted during WiFi connect -> portal");
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
        Serial.println("User aborted during focus fetch -> portal");
        enterPortalMode();
        return;
    }

    // ── Default view: todo list pulled from inksight-server ──
    Serial.println("Fetching todos...");
    ledFeedback("downloading");
    {
        TodoItem todoItems[TODO_MAX];
        int todoCount = 0;
        if (fetchTodos(todoItems, todoCount, TODO_MAX) && todoCount > 0) {
            float vb = readBatteryVoltage();
            int pct = (int)((vb - 3.0f) / (4.2f - 3.0f) * 100.0f);
            if (pct < 0) pct = 0; else if (pct > 100) pct = 100;
            renderTodoScreen(todoItems, todoCount, 0, 1, pct);
            cacheSave(imgBuf, IMG_BUF_LEN);
            lastContentChecksum = computeChecksum(imgBuf, IMG_BUF_LEN);
            Serial.printf("Todos rendered: %d items\n", todoCount);
        } else {
            Serial.println("Todo fetch failed; keeping demo list on screen");
        }
    }
    ledFeedback("success");
    syncNTP();   // after first paint so the screen lights sooner
    Serial.println("Display done");
    lastRenderedPeriod = currentPeriodIndex();
    ctx.lastClockTick = millis();

    // ── Always-on: keep WiFi up so button image fetch / todo refresh work ──
    g_view = AppView::TODO;
    ctx.state = DeviceState::DISPLAYING;
    ctx.setupDoneAt = millis();
    Serial.println("Boot complete (inksight-server mode), entering main loop (always on)");
}

// ── loop() ──────────────────────────────────────────────────
void loop() {
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

    if (millis() - ctx.setupDoneAt >= refreshIntervalMs()) {
#if DEBUG_MODE
        Serial.printf("[DEBUG] %d min elapsed, refreshing content...\n", DEBUG_REFRESH_MIN);
#else
        Serial.printf("%d min elapsed, refreshing content...\n", cfgSleepMin);
#endif
#if INKSIGHT_BACKEND_V2
        if (g_view == AppView::TODO) {
            TodoItem todoItems[TODO_MAX];
            int todoCount = 0;
            if (fetchTodos(todoItems, todoCount, TODO_MAX) && todoCount > 0) {
                float vb = readBatteryVoltage();
                int pct = (int)((vb - 3.0f) / (4.2f - 3.0f) * 100.0f);
                if (pct < 0) pct = 0; else if (pct > 100) pct = 100;
                renderTodoScreen(todoItems, todoCount, 0, 1, pct);
                uint32_t cs = computeChecksum(imgBuf, IMG_BUF_LEN);
                if (cs != lastContentChecksum) {
                    smartDisplay(imgBuf);
                    lastContentChecksum = cs;
                }
            }
        } else {
            triggerImmediateRefresh();
        }
#else
        triggerImmediateRefresh();
#endif
        ctx.setupDoneAt = millis();
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
            Serial.println("[MEM] alertBackupBuf malloc failed; focus alerts disabled");
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
