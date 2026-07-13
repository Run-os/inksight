#include "network.h"
#include "config.h"
#include "storage.h"
#include "certs.h"

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <mbedtls/base64.h>
#include <time.h>
#include <stdlib.h>     // setenv() for forcing the TZ (UTC+8) explicitly
#include <sys/time.h>   // settimeofday / struct timeval for writing local RTC
#include <cstring>
#include <cctype>
#include <ArduinoJson.h>
#if defined(BOARD_PROFILE_ESP32_C3_WROOM02) || defined(BOARD_PROFILE_SMT_WROOM32E) || defined(BOARD_PROFILE_YD_ESP32_S3_N16R8)
#include <esp_adc_cal.h>
#endif

// ── Time state ──────────────────────────────────────────────
int curHour, curMin, curSec;
static unsigned long lastHeartbeatAt = 0;
bool g_userAborted = false;
bool g_suppressAbortCheck = false;

static bool checkAbort() {
    if (g_suppressAbortCheck) return false;
    if (digitalRead(PIN_CFG_BTN) == LOW) {
        delay(50);
        if (digitalRead(PIN_CFG_BTN) == LOW) {
            g_userAborted = true;
            return true;
        }
    }
    return false;
}

static bool beginHttpForUrl(HTTPClient &http, WiFiClient &plainClient, WiFiClientSecure &secClient, const String &url);
static bool recoverDeviceTokenIfUnauthorized(int code);
static String extractJsonStringField(const String &body, const char *key);
static String extractJsonBoolField(const String &body, const char *key);
static int extractJsonIntField(const String &body, const char *key, int defaultValue = 0);

// ── WiFi connection ─────────────────────────────────────────

// Associate with a single AP and wait up to WIFI_TIMEOUT. Returns true on
// success. Returns false on timeout/abort (g_userAborted set on abort).
static bool tryAssociate(const String &ssid, const String &pass) {
    Serial.printf("WiFi: %s ", ssid.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    WiFi.begin(ssid.c_str(), pass.c_str());

    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (checkAbort()) return false;
        if (millis() - t0 > (unsigned long)WIFI_TIMEOUT) {
            Serial.println("TIMEOUT");
            return false;
        }
        delay(300);
        Serial.print(".");
    }
    Serial.printf(" OK  IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

bool connectWiFi() {
    g_userAborted = false;

    // Try each saved network in order; first success wins.
    int count = getWiFiCount();
    if (count <= 0) {
        // No saved list (shouldn't normally happen post-loadConfig); fall back
        // to the legacy primary credentials.
        if (cfgSSID.length() == 0) return false;
        if (!tryAssociate(cfgSSID, cfgPass)) return false;
    } else {
        bool associated = false;
        for (int i = 0; i < count; i++) {
            String ssid, pass;
            if (!getWiFiAt(i, ssid, pass)) continue;
            if (g_userAborted) return false;
            Serial.printf("[WIFI] Trying network %d/%d\n", i + 1, count);
            if (tryAssociate(ssid, pass)) {
                // Promote the connected network to primary so cfgSSID/cfgPass
                // (used for display, pairing, heartbeat) reflect it.
                cfgSSID = ssid;
                cfgPass = pass;
                associated = true;
                break;
            }
            if (g_userAborted) return false;
        }
        if (!associated) {
            Serial.println("[WIFI] All saved networks failed");
            return false;
        }
    }

    if (!ensureDeviceToken()) return false;
    if (cfgPendingPairCode.length() > 0) {
        String mac = WiFi.macAddress();
        String url = cfgServer + "/api/device/" + mac + "/claim-token";
        String body = String("{\"pair_code\":\"") + cfgPendingPairCode + "\"}";
        for (int attempt = 0; attempt < 3; attempt++) {
            if (checkAbort()) return false;
            Serial.printf("[PAIR] POST %s (attempt %d/3)\n", url.c_str(), attempt + 1);
            WiFiClient plainClient;
            WiFiClientSecure secClient;
            HTTPClient http;
            if (!beginHttpForUrl(http, plainClient, secClient, url)) {
                Serial.println("[PAIR] begin failed");
                delay(800);
                continue;
            }
            http.addHeader("Content-Type", "application/json");
            if (cfgDeviceToken.length() > 0) {
                http.addHeader("X-Device-Token", cfgDeviceToken);
            }
            http.setTimeout(HTTP_TIMEOUT);

            int code = http.POST(body);
            Serial.printf("[PAIR] HTTP code: %d\n", code);
            if (code >= 200 && code < 300) {
                String resp = http.getString();
                String savedPairCode = extractJsonStringField(resp, "pair_code");
                http.end();
                if (savedPairCode == cfgPendingPairCode) {
                    clearPendingPairCode();
                    Serial.println("[PAIR] pair code registered");
                    break;
                }
                Serial.printf(
                    "[PAIR] pair code mismatch: local=%s remote=%s\n",
                    cfgPendingPairCode.c_str(),
                    savedPairCode.length() > 0 ? savedPairCode.c_str() : "empty"
                );
                delay(800);
                continue;
            }
            if (code < 0) {
                Serial.printf("[PAIR] error: %s\n", http.errorToString(code).c_str());
            } else {
                String resp = http.getString();
                Serial.printf("[PAIR] response: %s\n", resp.substring(0, 300).c_str());
            }
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) {
                delay(800);
            }
        }
    }
    postHeartbeat(true);
    return true;
}

// ── Battery voltage ─────────────────────────────────────────

#if defined(BOARD_PROFILE_RLCD_S3)
// Waveshare ESP32-S3-RLCD-4.2: battery sense on GPIO4 (ADC1_CH3), 3x divider.
// Use a plain linear conversion of the Arduino analogRead() result. We deliberately
// avoid esp_adc_cal (legacy): on ESP32-S3 parts lacking the ADC calibration eFuse,
// esp_adc_cal_characterize() fails and esp_adc_cal_raw_to_voltage() then returns 0,
// which zeroes the reading. Attenuation is set to ADC_11db at boot, whose full
// scale is ~3.1V, so raw*3.1/4095 is an accurate linear mapping here.
float readBatteryVoltage() {
    const int N = 16;
    long sum = 0;
    for (int i = 0; i < N; i++) {
        sum += analogRead(PIN_BAT_ADC);
        delayMicroseconds(100);
    }
    float avgRaw = (float)(sum / N);
    float vAdc = avgRaw * (3.1f / 4095.0f);  // 11dB atten -> ~3.1V full scale
    float vBat = vAdc * 3.0f;                 // 3x divider (battery -> ADC)
    Serial.printf("[BAT] raw=%.1f adc=%.2fV vbat=%.2fV\n", avgRaw, vAdc, vBat);
    return vBat;
}
#else
float readBatteryVoltage() {
    const int SAMPLES = 16;
    const int DISCARD = 2;  // Discard highest and lowest outliers
    int readings[SAMPLES];

    for (int i = 0; i < SAMPLES; i++) {
        readings[i] = analogRead(PIN_BAT_ADC);
        delayMicroseconds(100);
    }

    // Sort for outlier removal
    for (int i = 0; i < SAMPLES - 1; i++)
        for (int j = i + 1; j < SAMPLES; j++)
            if (readings[i] > readings[j]) {
                int tmp = readings[i];
                readings[i] = readings[j];
                readings[j] = tmp;
            }

    // Average middle readings (discard DISCARD highest and lowest)
    long sum = 0;
    for (int i = DISCARD; i < SAMPLES - DISCARD; i++)
        sum += readings[i];

    float avgRaw = (float)sum / (SAMPLES - 2 * DISCARD);
#if defined(BOARD_PROFILE_ESP32_C3_WROOM02) || defined(BOARD_PROFILE_SMT_WROOM32E)
    static esp_adc_cal_characteristics_t adcChars;
    static bool calibrated = false;
    if (!calibrated) {
        esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_12, ADC_WIDTH_BIT_12, 1100, &adcChars);
        calibrated = true;
    }

    uint32_t mv = esp_adc_cal_raw_to_voltage((uint32_t)avgRaw, &adcChars);
    float realBatteryVoltage = (mv / 1000.0f) * 2.0f; // R1=10k, R2=10k
    Serial.printf("[BAT] raw=%.1f adc=%umV vbat=%.2fV\n", avgRaw, (unsigned int)mv, realBatteryVoltage);
    return realBatteryVoltage;
#else
    float realBatteryVoltage = avgRaw * (3.3f / 4095.0f) * 2.0f;
    Serial.printf("[BAT] raw=%.1f vbat=%.2fV\n", avgRaw, realBatteryVoltage);
    return realBatteryVoltage;
#endif
}
#endif

// ── Stream helper ───────────────────────────────────────────

static bool readExact(WiFiClient *s, uint8_t *buf, int len) {
    int got = 0;
    unsigned long t0 = millis();
    while (got < len) {
        if (millis() - t0 > 10000) {
            Serial.printf(
                "readExact: timeout %d/%d connected=%d available=%d wifi=%d\n",
                got,
                len,
                s->connected() ? 1 : 0,
                s->available(),
                WiFi.status()
            );
            return false;
        }
        int avail = s->available();
        if (avail > 0) {
            int r = s->readBytes(buf + got, min(avail, len - got));
            got += r;
            t0 = millis();  // Reset timeout on progress
        } else {
            delay(1);
        }
    }
    return true;
}

static bool beginHttpForUrl(HTTPClient &http, WiFiClient &plainClient, WiFiClientSecure &secClient, const String &url) {
    if (url.startsWith("https://")) {
#if BACKEND_TLS_INSECURE
        secClient.setInsecure();
#else
        secClient.setCACert(ROOT_CA);
#endif
        return http.begin(secClient, url);
    }
    return http.begin(plainClient, url);
}

static String extractJsonStringField(const String &body, const char *key) {
    String needle = String("\"") + key + "\"";
    int start = body.indexOf(needle);
    if (start < 0) return "";
    start += needle.length();
    while (start < body.length() && (body[start] == ' ' || body[start] == '\t' || body[start] == '\r' || body[start] == '\n')) {
        start++;
    }
    if (start >= body.length() || body[start] != ':') return "";
    start++;
    while (start < body.length() && (body[start] == ' ' || body[start] == '\t' || body[start] == '\r' || body[start] == '\n')) {
        start++;
    }
    if (start >= body.length() || body[start] != '"') return "";
    start++;
    int end = body.indexOf('"', start);
    if (end < 0) return "";
    return body.substring(start, end);
}

static String extractJsonBoolField(const String &body, const char *key) {
    String needle = String("\"") + key + "\":";
    int start = body.indexOf(needle);
    if (start < 0) return "";
    start += needle.length();
    while (start < body.length() && body[start] == ' ') {
        start++;
    }
    if (body.startsWith("true", start)) return "true";
    if (body.startsWith("false", start)) return "false";
    if (body.startsWith("1", start)) return "1";
    if (body.startsWith("0", start)) return "0";
    return "";
}

static int extractJsonIntField(const String &body, const char *key, int defaultValue) {
    String needle = String("\"") + key + "\":";
    int start = body.indexOf(needle);
    if (start < 0) return defaultValue;
    start += needle.length();
    while (start < body.length() && body[start] == ' ') {
        start++;
    }
    int end = start;
    while (end < body.length() && (body[end] == '-' || (body[end] >= '0' && body[end] <= '9'))) {
        end++;
    }
    if (end <= start) return defaultValue;
    return body.substring(start, end).toInt();
}

static bool recoverDeviceTokenIfUnauthorized(int code) {
    if (code != 401 || cfgDeviceToken.length() == 0) return false;
    Serial.println("[AUTH] 401 unauthorized, resetting cached device token");
    clearDeviceToken();
    return ensureDeviceToken();
}

bool postHeartbeat(bool force) {
#if INKSIGHT_BACKEND_V2
    return false;  // legacy device heartbeat; inksight-server has no device registry
#endif
    if (WiFi.status() != WL_CONNECTED) return false;
    unsigned long now = millis();
    if (!force && lastHeartbeatAt != 0 && now - lastHeartbeatAt < HEARTBEAT_INTERVAL_MS) {
        return true;
    }
    if (!ensureDeviceToken()) return false;

    float v = readBatteryVoltage();
    int rssi = WiFi.RSSI();
    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/heartbeat";
    String body = String("{\"battery_voltage\":") + String(v, 2) + ",\"wifi_rssi\":" + String(rssi) + "}";
    for (int attempt = 0; attempt < 2; attempt++) {
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (!beginHttpForUrl(http, plainClient, secClient, url)) return false;
        http.addHeader("Content-Type", "application/json");
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }
        http.setTimeout(HTTP_TIMEOUT);

        int code = http.POST(body);
        if (code >= 200 && code < 300) {
            Serial.printf("[HEARTBEAT] POST -> %d\n", code);
            http.end();
            lastHeartbeatAt = now;
            return true;
        }
        if (code < 0) {
            Serial.printf("[HEARTBEAT] error: %s\n", http.errorToString(code).c_str());
        } else {
            Serial.printf("[HEARTBEAT] POST -> %d\n", code);
        }
        http.end();
        if (!recoverDeviceTokenIfUnauthorized(code)) {
            return false;
        }
    }
    return false;
}

bool ensureDeviceToken() {
#if INKSIGHT_BACKEND_V2
    return true;  // no device-token handshake under inksight-server
#endif
    if (cfgDeviceToken.length() > 0) return true;
    if (WiFi.status() != WL_CONNECTED) return false;

    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/token";
    delay(1200);
    for (int attempt = 0; attempt < 3; attempt++) {
        if (checkAbort()) return false;
        Serial.printf("[TOKEN] POST %s (attempt %d/3)\n", url.c_str(), attempt + 1);
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (!beginHttpForUrl(http, plainClient, secClient, url)) {
            Serial.println("[TOKEN] begin failed");
            delay(800);
            continue;
        }
        http.addHeader("Content-Type", "application/json");
        http.setTimeout(HTTP_TIMEOUT);

        int code = http.POST("{}");
        Serial.printf("[TOKEN] HTTP code: %d\n", code);
        if (code >= 200 && code < 300) {
            String body = http.getString();
            http.end();
            String token = extractJsonStringField(body, "token");
            if (token.length() == 0) {
                Serial.println("[TOKEN] token field empty");
                delay(800);
                continue;
            }
            saveDeviceToken(token);
            Serial.println("[TOKEN] token saved");
            return true;
        }
        if (code < 0) {
            Serial.printf("[TOKEN] error: %s\n", http.errorToString(code).c_str());
        } else {
            String body = http.getString();
            Serial.printf("[TOKEN] response: %s\n", body.substring(0, 300).c_str());
        }
        http.end();
        delay(800);
    }
    Serial.println("[TOKEN] failed to obtain device token");
    return false;
}

bool fetchFocusListeningFlag(bool *outEnabled, bool *outAlwaysActive) {
#if INKSIGHT_BACKEND_V2
    if (outEnabled) *outEnabled = false;
    if (outAlwaysActive) *outAlwaysActive = false;
    return false;  // focus-listening is an old-backend concept
#endif
    if (!outEnabled) return false;
    *outEnabled = false;
    if (outAlwaysActive) *outAlwaysActive = false;
    if (WiFi.status() != WL_CONNECTED) return false;
    if (!ensureDeviceToken()) return false;

    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/config/" + mac;
    bool useSSL = cfgServer.startsWith("https://");

    for (int attempt = 0; attempt < 2; attempt++) {
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }
        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.GET();
        if (code != 200) {
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) return false;
            continue;
        }

        String body = http.getString();
        http.end();
        bool enabled =
            body.indexOf("\"is_focus_listening\":true") >= 0 ||
            body.indexOf("\"is_focus_listening\": true") >= 0 ||
            body.indexOf("\"focus_listening\":1") >= 0 ||
            body.indexOf("\"focus_listening\": 1") >= 0;
        bool alwaysActive =
            body.indexOf("\"is_always_active\":true") >= 0 ||
            body.indexOf("\"is_always_active\": true") >= 0 ||
            body.indexOf("\"always_active\":1") >= 0 ||
            body.indexOf("\"always_active\": 1") >= 0 ||
            body.indexOf("\"always_active\":true") >= 0 ||
            body.indexOf("\"always_active\": true") >= 0;
        *outEnabled = enabled;
        if (outAlwaysActive) *outAlwaysActive = alwaysActive;
        Serial.printf("[CONFIG] is_focus_listening=%s always_active=%s\n",
                      enabled ? "true" : "false",
                      alwaysActive ? "true" : "false");
        return true;
    }
    return false;
}

bool fetchFocusAlertBMP() {
#if INKSIGHT_BACKEND_V2
    return false;  // focus alerts belong to the retired backend
#endif
    if (WiFi.status() != WL_CONNECTED) return false;
    if (!ensureDeviceToken()) return false;
    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/alert-bmp"
               + "?w=" + String(W) + "&h=" + String(H);
    bool useSSL = cfgServer.startsWith("https://");

    for (int attempt = 0; attempt < 2; attempt++) {
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }
        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.GET();
        Serial.printf("[FOCUS] alert-bmp HTTP code: %d\n", code);
        if (code == 204) {
            http.end();
            return false;
        }
        if (code != 200) {
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) return false;
            continue;
        }

        WiFiClient *stream = http.getStreamPtr();
        uint8_t fileHeader[14];
        if (!readExact(stream, fileHeader, 14)) {
            http.end();
            return false;
        }
        uint32_t pixelOffset = fileHeader[10]
                             | ((uint32_t)fileHeader[11] << 8)
                             | ((uint32_t)fileHeader[12] << 16)
                             | ((uint32_t)fileHeader[13] << 24);
        int toSkip = (int)pixelOffset - 14;
        while (toSkip > 0 && stream->connected()) {
            if (stream->available()) { stream->read(); toSkip--; }
        }

        uint8_t rowBuf[ROW_STRIDE];
        for (int bmpY = 0; bmpY < H; bmpY++) {
            if (!readExact(stream, rowBuf, ROW_STRIDE)) {
                http.end();
                return false;
            }
            int dispY = H - 1 - bmpY;
            memcpy(imgBuf + dispY * ROW_BYTES, rowBuf, ROW_BYTES);
        }
        http.end();
        return true;
    }
    return false;
}

// ── inksight-server (v2) data layer ─────────────────────────
// Pulls todo list from /api/todos and image resources from the
// /api/images/{manifest,file} endpoints. No device-token handshake.

// Persistent buffers so returned TodoItem::text/remind point at stable storage.
static char g_todoText[TODO_MAX][96];
static char g_todoRem[TODO_MAX][8];
static bool g_todoDone[TODO_MAX];

bool fetchTodos(TodoItem *out, int &outCount, int maxOut) {
    outCount = 0;
    if (WiFi.status() != WL_CONNECTED) return false;
    String url = cfgServer + "/api/todos";
    WiFiClient plainClient;
    WiFiClientSecure secClient;
    HTTPClient http;
    if (!beginHttpForUrl(http, plainClient, secClient, url)) return false;
    http.setTimeout(HTTP_TIMEOUT);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    Serial.printf("GET %s\n", url.c_str());
    int code = http.GET();
    if (code != 200) {
        if (code < 0) Serial.printf("[TODOS] HTTP error: %s\n", http.errorToString(code).c_str());
        else { String b = http.getString(); Serial.printf("[TODOS] HTTP %d: %s\n", code, b.substring(0, 300).c_str()); }
        http.end();
        return false;
    }
    String body = http.getString();
    http.end();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (err) {
        Serial.printf("[TODOS] JSON parse error: %s\n", err.c_str());
        return false;
    }
    JsonArray items = doc["items"];
    if (items.isNull()) {
        Serial.println("[TODOS] no 'items' array in response");
        return false;
    }

    int n = 0;
    for (JsonObject it : items) {
        if (n >= maxOut || n >= TODO_MAX) break;
        const char *t = it["text"] | "";
        strncpy(g_todoText[n], t, sizeof(g_todoText[n]) - 1);
        g_todoText[n][sizeof(g_todoText[n]) - 1] = 0;
        g_todoDone[n] = (bool)(it["done"] | false);
        g_todoRem[n][0] = 0;
        const char *ra = it["remind_at"] | it["due"] | "";
        if (ra && *ra) {
            const char *tpos = strchr(ra, 'T');
            if (tpos && strlen(tpos) >= 6 && isdigit((unsigned char)tpos[1])) {
                snprintf(g_todoRem[n], sizeof(g_todoRem[n]), "%.2s:%.2s", tpos + 1, tpos + 4);
            }
        }
        out[n].text = g_todoText[n];
        out[n].done = g_todoDone[n];
        out[n].remind = g_todoRem[n][0] ? g_todoRem[n] : nullptr;
        n++;
    }
    outCount = n;
    Serial.printf("[TODOS] parsed %d items\n", n);
    return n > 0;
}

bool fetchImageManifest(String &version, String *names, int &count, int maxNames) {
    count = 0;
    version = "";
    if (WiFi.status() != WL_CONNECTED) return false;
    String url = cfgServer + "/api/images/manifest";
    WiFiClient plainClient;
    WiFiClientSecure secClient;
    HTTPClient http;
    if (!beginHttpForUrl(http, plainClient, secClient, url)) return false;
    http.setTimeout(HTTP_TIMEOUT);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    int code = http.GET();
    if (code != 200) {
        if (code < 0) Serial.printf("[IMG] manifest HTTP error: %s\n", http.errorToString(code).c_str());
        else Serial.printf("[IMG] manifest HTTP %d\n", code);
        http.end();
        return false;
    }
    String body = http.getString();
    http.end();
    JsonDocument doc;
    if (deserializeJson(doc, body)) return false;
    version = doc["version"] | "";
    JsonArray imgs = doc["images"];
    if (imgs.isNull()) return false;
    int n = 0;
    for (JsonVariant v : imgs) {
        if (n >= maxNames) break;
        names[n++] = v.as<String>();
    }
    count = n;
    return true;
}

// Decode a 1-bit (or grayscale/24-bit) BMP stream into imgBuf.
static bool decodeBmpToImgBuf(WiFiClient *stream) {
    uint8_t fileHeader[14];
    if (!readExact(stream, fileHeader, 14)) {
        Serial.println("[IMG] Failed to read BMP header");
        return false;
    }
    uint32_t pixelOffset = (uint32_t)fileHeader[10]
                         | ((uint32_t)fileHeader[11] << 8)
                         | ((uint32_t)fileHeader[12] << 16)
                         | ((uint32_t)fileHeader[13] << 24);
    uint8_t infoHeader[16];
    if (!readExact(stream, infoHeader, 16)) {
        Serial.println("[IMG] Failed to read BMP info header");
        return false;
    }
    int bmpBits = (int)infoHeader[14] | ((int)infoHeader[15] << 8);
    int toSkip = (int)pixelOffset - 14 - 16;
    while (toSkip > 0 && stream->connected()) {
        if (stream->available()) { stream->read(); toSkip--; }
    }
    memset(imgBuf, 0xFF, IMG_BUF_LEN);
    if (bmpBits <= 1) {
        uint8_t rowBuf[ROW_STRIDE];
        for (int bmpY = 0; bmpY < H; bmpY++) {
            if (!readExact(stream, rowBuf, ROW_STRIDE)) {
                Serial.printf("[IMG] Failed to read row %d\n", bmpY);
                return false;
            }
            int dispY = H - 1 - bmpY;
            memcpy(imgBuf + dispY * ROW_BYTES, rowBuf, ROW_BYTES);
        }
    } else {
        int srcRowBytes = (W * bmpBits + 31) / 32 * 4;
        uint8_t *srcRow = (uint8_t *)malloc(srcRowBytes);
        if (!srcRow) {
            Serial.println("[IMG] Failed to alloc srcRow");
            return false;
        }
        for (int bmpY = 0; bmpY < H; bmpY++) {
            if (!readExact(stream, srcRow, srcRowBytes)) {
                Serial.printf("[IMG] Failed to read row %d\n", bmpY);
                free(srcRow);
                return false;
            }
            int dispY = H - 1 - bmpY;
            for (int x = 0; x < W; x++) {
                uint8_t pixel = (bmpBits == 8) ? srcRow[x] : srcRow[x * 3];
                if (pixel < 128) {
                    imgBuf[dispY * ROW_BYTES + x / 8] &= ~(0x80 >> (x % 8));
                }
            }
        }
        free(srcRow);
    }
    return true;
}

bool fetchImageByName(const String &name) {
    if (WiFi.status() != WL_CONNECTED) return false;
    String url = cfgServer + "/api/images/" + name;
    WiFiClient plainClient;
    WiFiClientSecure secClient;
    HTTPClient http;
    if (!beginHttpForUrl(http, plainClient, secClient, url)) return false;
    http.setTimeout(HTTP_TIMEOUT);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    Serial.printf("GET %s\n", url.c_str());
    int code = http.GET();
    if (code != 200) {
        if (code < 0) Serial.printf("[IMG] HTTP error: %s\n", http.errorToString(code).c_str());
        else Serial.printf("[IMG] HTTP %d\n", code);
        http.end();
        return false;
    }
    WiFiClient *stream = http.getStreamPtr();
    bool ok = decodeBmpToImgBuf(stream);
    http.end();
    if (ok) Serial.printf("[IMG] OK  %s\n", name.c_str());
    return ok;
}

// ── Fetch BMP from backend ──────────────────────────────────

bool fetchBMP(bool nextMode, bool *isFallback, String *renderedModeIdOut) {
#if INKSIGHT_BACKEND_V2
    if (isFallback) *isFallback = false;
    if (renderedModeIdOut) *renderedModeIdOut = "";
    static String imgNames[64];
    static int g_imgIdx = 0;
    String version;
    int n = 0;
    if (!fetchImageManifest(version, imgNames, n, 64)) {
        Serial.println("[IMG] manifest fetch failed");
        return false;
    }
    if (n == 0) {
        Serial.println("[IMG] manifest empty (no images generated yet)");
        return false;
    }
    if (nextMode) g_imgIdx = (g_imgIdx + 1) % n;
    if (g_imgIdx >= n) g_imgIdx = 0;
    if (!fetchImageByName(imgNames[g_imgIdx])) {
        Serial.println("[IMG] image fetch failed");
        return false;
    }
    if (renderedModeIdOut) *renderedModeIdOut = imgNames[g_imgIdx];
    return true;
#else
    if (isFallback) *isFallback = false;
    if (renderedModeIdOut) *renderedModeIdOut = "";
    if (!ensureDeviceToken()) return false;
    float v = readBatteryVoltage();
    String mac = WiFi.macAddress();
    int rssi = WiFi.RSSI();
#if DEBUG_MODE
    int effectiveRefreshMin = DEBUG_REFRESH_MIN;
#else
    int effectiveRefreshMin = cfgSleepMin;
#endif
    const int colorCapability = 2;  // RLCD is 1bpp (2 colors)
    String url = cfgServer + "/api/render?v=" + String(v, 2)
               + "&mac=" + mac + "&rssi=" + String(rssi)
               + "&refresh_min=" + String(effectiveRefreshMin)
               + "&w=" + String(W) + "&h=" + String(H)
               + "&bpp=" + String(EPD_BPP)
               + "&colors=" + String(colorCapability);
    if (nextMode) {
        url += "&next=1";
    }
    Serial.printf("GET %s (RSSI=%d)\n", url.c_str(), rssi);

    bool useSSL = cfgServer.startsWith("https://");
    for (int attempt = 0; attempt < 2; attempt++) {
        if (checkAbort()) {
            Serial.println("[RENDER] fetchBMP aborted before HTTP");
            return false;
        }
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        bool begun = false;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            begun = http.begin(secClient, url);
        } else {
            begun = http.begin(plainClient, url);
        }
        if (!begun) {
            Serial.println("[RENDER] http.begin failed");
            http.end();
            return false;
        }
        http.setReuse(false);
        http.setTimeout(HTTP_TIMEOUT);
        http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
        const char *headerKeys[] = {"X-Content-Fallback", "X-Refresh-Minutes", "X-Mode-Id"};
        http.collectHeaders(headerKeys, 3);

        http.addHeader("Accept-Encoding", "identity");
        http.addHeader("Connection", "close");
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
        int code = http.GET();
        Serial.printf("HTTP code: %d\n", code);
        if (renderedModeIdOut && code >= 200 && code < 300) {
            *renderedModeIdOut = http.header("X-Mode-Id");
        }
        if (isFallback) {
            String fallbackHeader = http.header("X-Content-Fallback");
            *isFallback = (fallbackHeader == "1" || fallbackHeader == "true");
            if (*isFallback) {
                Serial.println("[RENDER] Received fallback content");
            }
        }
        String refreshHeader = http.header("X-Refresh-Minutes");
        int serverRefreshMin = refreshHeader.toInt();
        if (serverRefreshMin >= 10 && serverRefreshMin <= 1440 && serverRefreshMin != cfgSleepMin) {
            saveSleepMin(serverRefreshMin);
            Serial.printf("[RENDER] Applied refresh interval: %d min\n", serverRefreshMin);
        }

        if (code != 200) {
            if (code < 0) {
                Serial.printf("HTTP error: %s\n", http.errorToString(code).c_str());
            } else {
                String body = http.getString();
                Serial.printf("Response: %s\n", body.substring(0, 500).c_str());
            }
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) {
                return false;
            }
            continue;
        }

        int contentLen = http.getSize();
        Serial.printf("Content-Length: %d\n", contentLen);

        WiFiClient *stream = http.getStreamPtr();

        uint8_t fileHeader[14];
        if (!readExact(stream, fileHeader, 14)) {
            Serial.println("Failed to read BMP header");
            http.end();
            return false;
        }

        uint32_t pixelOffset = fileHeader[10]
                             | ((uint32_t)fileHeader[11] << 8)
                             | ((uint32_t)fileHeader[12] << 16)
                             | ((uint32_t)fileHeader[13] << 24);
        Serial.printf("BMP pixel offset: %u\n", pixelOffset);

        // Read info header to get bit count (biBitCount at offset 28 = 14+14)
        uint8_t infoHeader[16];
        if (!readExact(stream, infoHeader, 16)) {
            Serial.println("Failed to read BMP info header");
            http.end();
            return false;
        }
        int bmpBits = infoHeader[14] | ((int)infoHeader[15] << 8);
        Serial.printf("BMP bit count: %d\n", bmpBits);

        int toSkip = pixelOffset - 14 - 16;  // skip remaining header+palette
        while (toSkip > 0 && stream->connected()) {
            if (stream->available()) { stream->read(); toSkip--; }
        }

        memset(imgBuf, 0xFF, IMG_BUF_LEN);

        if (bmpBits <= 1) {
            // 1-bit BMP: each row is ROW_STRIDE bytes (padded)
            uint8_t rowBuf[ROW_STRIDE];
            for (int bmpY = 0; bmpY < H; bmpY++) {
                if (!readExact(stream, rowBuf, ROW_STRIDE)) {
                    Serial.printf("Failed to read row %d\n", bmpY);
                    http.end();
                    return false;
                }
                int dispY = H - 1 - bmpY;
                memcpy(imgBuf + dispY * ROW_BYTES, rowBuf, ROW_BYTES);
            }
        } else {
            // 8-bit (or 24-bit) BMP: convert each pixel to 1 bit
            int srcRowBytes = (W * bmpBits + 31) / 32 * 4;
            uint8_t *srcRow = (uint8_t *)malloc(srcRowBytes);
            if (!srcRow) {
                Serial.println("Failed to alloc srcRow");
                http.end();
                return false;
            }
            for (int bmpY = 0; bmpY < H; bmpY++) {
                if (!readExact(stream, srcRow, srcRowBytes)) {
                    Serial.printf("Failed to read row %d\n", bmpY);
                    free(srcRow);
                    http.end();
                    return false;
                }
                int dispY = H - 1 - bmpY;
                for (int x = 0; x < W; x++) {
                    uint8_t pixel;
                    if (bmpBits == 8) {
                        pixel = srcRow[x];
                    } else {
                        pixel = srcRow[x * 3];  // 24-bit: use blue channel
                    }
                    if (pixel < 128) {
                        imgBuf[dispY * ROW_BYTES + x / 8] &= ~(0x80 >> (x % 8));
                    }
                }
            }
            free(srcRow);
        }

        http.end();
        Serial.printf("BMP OK  %d bytes\n", IMG_BUF_LEN);
        lastHeartbeatAt = millis();
        return true;
    }
    return false;
#endif
}

bool hasPendingRemoteAction(bool *shouldExitLive) {
#if INKSIGHT_BACKEND_V2
    if (shouldExitLive) *shouldExitLive = false;
    return false;  // remote-action polling is an old-backend concept
#endif
    if (WiFi.status() != WL_CONNECTED) return false;
    if (!ensureDeviceToken()) return false;

    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/state";

    bool useSSL = cfgServer.startsWith("https://");
    for (int attempt = 0; attempt < 2; attempt++) {
        if (checkAbort()) return false;
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }
        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.GET();
        if (code != 200) {
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) {
                return false;
            }
            continue;
        }

        String body = http.getString();
        http.end();

        if (shouldExitLive) {
            bool intervalRequested =
                body.indexOf("\"runtime_mode\":\"interval\"") >= 0 ||
                body.indexOf("\"runtime_mode\": \"interval\"") >= 0;
            *shouldExitLive = intervalRequested;
        }

        bool pendingRefresh =
            body.indexOf("\"pending_refresh\":1") >= 0 ||
            body.indexOf("\"pending_refresh\": 1") >= 0 ||
            body.indexOf("\"pending_refresh\":true") >= 0 ||
            body.indexOf("\"pending_refresh\": true") >= 0;

        bool pendingMode =
            (body.indexOf("\"pending_mode\":\"") >= 0 || body.indexOf("\"pending_mode\": \"") >= 0) &&
            body.indexOf("\"pending_mode\":\"\"") < 0 &&
            body.indexOf("\"pending_mode\": \"\"") < 0;

        return pendingRefresh || pendingMode;
    }
    return false;
}

bool peekPendingMode(String &pendingModeOut) {
#if INKSIGHT_BACKEND_V2
    pendingModeOut = "";
    return false;  // remote-mode polling is an old-backend concept
#endif
    pendingModeOut = "";
    if (WiFi.status() != WL_CONNECTED) return false;
    if (!ensureDeviceToken()) return false;

    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/state";
    bool useSSL = cfgServer.startsWith("https://");

    for (int attempt = 0; attempt < 2; attempt++) {
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }

        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.GET();
        if (code != 200) {
            http.end();
            if (!recoverDeviceTokenIfUnauthorized(code)) return false;
            continue;
        }

        String body = http.getString();
        http.end();
        pendingModeOut = extractJsonStringField(body, "pending_mode");
        return pendingModeOut.length() > 0;
    }

    return false;
}

// ── Post config to backend ──────────────────────────────────

void postConfigToBackend() {
#if INKSIGHT_BACKEND_V2
    return;  // config push belongs to the retired backend
#endif
    if (cfgConfigJson.length() == 0) return;
    if (!ensureDeviceToken()) return;

    // Inject MAC address into the config JSON
    String mac = WiFi.macAddress();
    String body = cfgConfigJson;
    if (body.startsWith("{")) {
        body = "{\"mac\":\"" + mac + "\"," + body.substring(1);
    }

    String url = cfgServer + "/api/config";
    bool useSSL = cfgServer.startsWith("https://");
    for (int attempt = 0; attempt < 2; attempt++) {
        if (checkAbort()) return;
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }
        http.addHeader("Content-Type", "application/json");
        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.POST(body);
        Serial.printf("POST /api/config -> %d\n", code);
        http.end();
        if (!recoverDeviceTokenIfUnauthorized(code)) {
            return;
        }
    }
}

// ── Post runtime mode to backend ────────────────────────────

bool postRuntimeMode(const char *mode) {
#if INKSIGHT_BACKEND_V2
    return false;  // runtime-mode reporting belongs to the retired backend
#endif
    if (!ensureDeviceToken()) return false;
    String mac = WiFi.macAddress();
    String url = cfgServer + "/api/device/" + mac + "/runtime";
    bool useSSL = cfgServer.startsWith("https://");
    String body = String("{\"mode\":\"") + mode + "\"}";
    for (int attempt = 0; attempt < 2; attempt++) {
        WiFiClient plainClient;
        WiFiClientSecure secClient;
        HTTPClient http;
        if (useSSL) {
            secClient.setCACert(ROOT_CA);
            http.begin(secClient, url);
        } else {
            http.begin(plainClient, url);
        }
        http.addHeader("Content-Type", "application/json");
        http.setTimeout(HTTP_TIMEOUT);
        if (cfgDeviceToken.length() > 0) {
            http.addHeader("X-Device-Token", cfgDeviceToken);
        }

        int code = http.POST(body);
        http.end();

        if (code == 404) {
            return true;
        }
        if (code >= 200 && code < 300) {
            return true;
        }
        if (!recoverDeviceTokenIfUnauthorized(code)) {
            return false;
        }
    }
    return false;
}


// ── NTP time sync ───────────────────────────────────────────

// Local RTC is considered "initialized" only after this epoch (~2023-11-14).
// Earlier means the RTC reset to 1970 (e.g. after a power loss with no backup),
// so it must NOT be trusted as a valid local time.
#define TIME_LOCAL_VALID_MIN ((time_t)1700000000)

// Time policy: prefer the local RTC, then fetch NTP. If NTP differs, adopt the
// network time and write it back to the local RTC (settimeofday). If NTP is
// unreachable, keep the local RTC instead of resetting to 00:00.
//
// configTime() is only invoked once (SNTP init); subsequent calls just poll
// getLocalTime() so we never restart the SNTP client mid-sync.
static bool sntpStarted = false;

bool rtcTimeValid() {
    return time(nullptr) > TIME_LOCAL_VALID_MIN;
}

void syncNTP() {
    time_t tLocal = time(nullptr);
    bool localValid = (tLocal > TIME_LOCAL_VALID_MIN);

    struct tm lt;
    if (localValid) localtime_r(&tLocal, &lt);

    // Force UTC+8 (China Standard Time) explicitly. configTime() derives its
    // TZ string from NTP_UTC_OFFSET, but some ESP32 Arduino cores emit a TZ
    // string that is not parsed correctly for positive offsets (e.g. they may
    // omit the leading '+' so localtime_r() falls back to UTC). Re-assert the
    // zone around configTime() so every localtime_r()/mktime() call below uses
    // Beijing time, not UTC. (POSIX TZ sign is inverted: CST-8 == UTC+8.)
    setenv("TZ", "CST-8", 1);
    tzset();
    if (!sntpStarted) {
        configTime(NTP_UTC_OFFSET, 0, "ntp.aliyun.com", "pool.ntp.org", "time.google.com");
        setenv("TZ", "CST-8", 1);   // re-assert: configTime() overwrites TZ
        tzset();
        sntpStarted = true;
    }

    // Poll up to ~12s for the first SNTP sync (cold connect can be slow).
    struct tm info;
    bool ok = false;
    for (int attempt = 0; attempt < 3 && !ok; attempt++) {
        if (getLocalTime(&info, 5000)) { ok = true; break; }
        Serial.println("[NTP] getLocalTime timeout, retrying...");
        delay(500);
    }

    if (ok) {
        time_t tNet = mktime(&info);
        long long diff = (long long)tNet - (long long)tLocal;
        if (!localValid) {
            // Local RTC invalid (reset to 1970): take the network time outright.
            struct timeval tv; tv.tv_sec = tNet; tv.tv_usec = 0;
            settimeofday(&tv, nullptr);
            Serial.printf("Local RTC invalid; synced to NTP %02d:%02d:%02d\n",
                          info.tm_hour, info.tm_min, info.tm_sec);
        } else if (diff < -1 || diff > 1) {
            // Local and network differ: adopt network time, write back to local RTC.
            struct timeval tv; tv.tv_sec = tNet; tv.tv_usec = 0;
            settimeofday(&tv, nullptr);
            Serial.printf("Local %02d:%02d differs from NTP %02d:%02d; resynced\n",
                          lt.tm_hour, lt.tm_min, info.tm_hour, info.tm_min);
        } else {
            Serial.println("Local time already matches NTP");
        }
        curHour = info.tm_hour; curMin = info.tm_min; curSec = info.tm_sec;
    } else {
        // NTP unreachable: keep the local RTC, never force 00:00.
        if (localValid) {
            Serial.println("NTP sync failed; keeping local RTC time");
            curHour = lt.tm_hour; curMin = lt.tm_min; curSec = lt.tm_sec;
        } else {
            Serial.println("NTP failed and local RTC invalid; clock shows 00:00");
            curHour = 0; curMin = 0; curSec = 0;
        }
    }
}

// ── Software clock tick ─────────────────────────────────────

void tickTime() {
    curSec++;
    if (curSec >= 60) { curSec = 0; curMin++; }
    if (curMin >= 60) { curMin = 0; curHour++; }
    if (curHour >= 24) { curHour = 0; }
}
