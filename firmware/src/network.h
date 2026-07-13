#ifndef INKSIGHT_NETWORK_H
#define INKSIGHT_NETWORK_H

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>
#include "display.h"   // TodoItem (used by fetchTodos)

extern bool g_userAborted;
extern bool g_suppressAbortCheck;

// ── Time state (updated by syncNTP / tickTime) ──────────────
extern int curHour, curMin, curSec;

// ── WiFi ────────────────────────────────────────────────────

// Connect to WiFi using stored credentials. Returns true on success.
bool connectWiFi();

// ── HTTP ────────────────────────────────────────────────────

// Fetch BMP image from backend and store in imgBuf. Returns true on success.
// If nextMode is true, appends &next=1 to request the next mode in sequence.
bool fetchBMP(bool nextMode = false, bool *isFallback = nullptr, String *renderedModeIdOut = nullptr);

// inksight-server (v2) data layer -------------------------------------------
// Pull the todo list from GET /api/todos. Fills up to maxOut TodoItem structs
// (text/remind/done) and returns the count. Returns true if >=1 item parsed.
bool fetchTodos(TodoItem *out, int &outCount, int maxOut);

// Pull the image manifest from GET /api/images/manifest. Returns the version
// hash and the ordered file-name list (up to maxNames).
bool fetchImageManifest(String &version, String *names, int &count, int maxNames);

// Fetch a single image file (GET /api/images/{name}) and decode it into imgBuf.
bool fetchImageByName(const String &name);

// Check whether backend has pending refresh/switch request for this device.
// If shouldExitLive is not null, it is set to true when backend runtime_mode is interval.
bool hasPendingRemoteAction(bool *shouldExitLive = nullptr);

// Peek pending_mode for this device without consuming it.
bool peekPendingMode(String &pendingModeOut);

// POST runtime mode (active/interval) to backend.
bool postRuntimeMode(const char *mode);

// POST device config JSON to backend /api/config endpoint.
void postConfigToBackend();

bool ensureDeviceToken();
bool postHeartbeat(bool force = false);

// ── Focus listening helpers ─────────────────────────────────
bool fetchFocusListeningFlag(bool *outEnabled, bool *outAlwaysActive = nullptr);
bool fetchFocusAlertBMP();

// ── Battery ─────────────────────────────────────────────────

// Read battery voltage via ADC (returns volts)
float readBatteryVoltage();

// ── NTP time ────────────────────────────────────────────────

// Sync time from NTP servers
void syncNTP();

// Advance software clock by one second
void tickTime();

#endif // INKSIGHT_NETWORK_H
