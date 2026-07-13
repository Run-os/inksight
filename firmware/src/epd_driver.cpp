#include "epd_driver.h"
#include "config.h"

// ── ESP32-S3-RLCD-4.2 reflective LCD (ST7305), 400x300, 1bpp ──
// Driven by the vendor DisplayPort driver (see rlcd_bsp.h).
// This firmware targets the RLCD panel exclusively; other panel backends have
// been removed. The RLCD is always-on: there is no sleep/power-down path here.

#include "rlcd_bsp.h"

#if (EPD_WIDTH != 400) || (EPD_HEIGHT != 300)
#error "EPD_PANEL_42_RLCD only supports 400x300 (ESP32-S3-RLCD-4.2)"
#endif

static DisplayPort *g_rlcd = nullptr;

static void rlcdEnsure() {
    if (!g_rlcd) {
        g_rlcd = new DisplayPort(
            PIN_EPD_MOSI, PIN_EPD_SCK, PIN_EPD_DC, PIN_EPD_CS, PIN_EPD_RST, W, H);
        g_rlcd->Init();
    }
}

// ── GPIO initialization ─────────────────────────────────────

void gpioInit() {
    pinMode(PIN_CFG_BTN, INPUT_PULLUP);
    // Display pins are configured inside DisplayPort::Init().
}

// ── EPD init ──

void epdInit() {
    rlcdEnsure();
}

void epdInitFast() {
    rlcdEnsure();
}

// ── Full-screen display ──
// Reflective LCD: clear to white, blit 1bpp image (black=0, MSB-first), push to panel.

void epdDisplay(const uint8_t *image) {
    rlcdEnsure();
    g_rlcd->ColorClear(0xFF);
    g_rlcd->Blit1bpp(image, W, H, /*blackIsZero=*/true);
    g_rlcd->Display();
}

// ── Deep clear (RLCD has no ghosting, so this is identical to a normal display) ──

void epdDisplayDeepClear(const uint8_t *image) {
    epdDisplay(image);
}

// ── Fast refresh (RLCD has no LUT concept, identical to a normal display) ──

void epdDisplayFast(const uint8_t *image) {
    epdDisplay(image);
}

// ── Partial refresh ─────────────────────────────────────────
// RLCD has no ghosting and no true partial refresh: always repaint the full
// frame from imgBuf (fast, flicker-free).

void epdPartialDisplay(uint8_t *data, int xStart, int yStart, int xEnd, int yEnd) {
    epdPartialDisplayWithOld(data, nullptr, xStart, yStart, xEnd, yEnd);
}

bool epdSupportsPartialRefresh() {
    // RLCD has no ghosting, so a cheap full repaint replaces partial refresh.
    return false;
}

void epdPartialDisplayWithOld(uint8_t *data, const uint8_t *oldData,
                              int xStart, int yStart, int xEnd, int yEnd) {
    (void)data;
    (void)oldData;
    (void)xStart; (void)yStart; (void)xEnd; (void)yEnd;
    // Reflective LCD: repaint the full frame from imgBuf (fast, no ghosting).
    epdDisplay(imgBuf);
}
