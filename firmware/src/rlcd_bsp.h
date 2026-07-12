#pragma once

#include <Arduino.h>
#include <SPI.h>

// ─────────────────────────────────────────────────────────────────────────────
// RLCD (ESP32-S3-RLCD-4.2) reflective LCD driver — ST7305 controller, 400x300,
// 1 bit per pixel. Ported from the vendor example display_bsp (ESP-IDF panel IO)
// to the Arduino SPI API so it builds under the firmware's Arduino framework.
//
// Pixel packing (ST7305 native 1bpp memory layout, landscape 400x300):
//   Each byte holds an 4(x) x 2(y) block of pixels. For pixel (x, y):
//     byte index  = (x / 2) * (H / 4) + ((H - 1 - y) / 4)
//     bit         = 7 - (((H - 1 - y) & 3) * 2 + (x & 1))
//   value 0 = black, 0xFF = white.
// This matches the proven vendor example, so we keep the exact same mapping.
// ─────────────────────────────────────────────────────────────────────────────

#define RLCD_ALGO_LUT 3  // 3 = precomputed index/bit LUT (fastest CPU path)

class DisplayPort {
private:
    int mosi_;
    int scl_;
    int dc_;
    int cs_;
    int rst_;
    int width_;
    int height_;
    SPIClass *spi_ = nullptr;
    uint8_t *DispBuffer = nullptr;
    int DisplayLen;

#if (RLCD_ALGO_LUT == 3)
    uint16_t (*PixelIndexLUT)[300];
    uint8_t  (*PixelBitLUT)[300];
    void InitPortraitLUT();
    void InitLandscapeLUT();
#endif

    void SetResetIOLevel(uint8_t level);
    void SendCommand(uint8_t reg);
    void SendData(uint8_t data);
    void SendBuffer(const uint8_t *data, int len);
    void Reset();

public:
    DisplayPort(int mosi, int scl, int dc, int cs, int rst, int width, int height,
                SPIClass *spi = nullptr);
    ~DisplayPort();

    // Initialize GPIO + SPI and run the ST7305 init sequence.
    void Init();

    // Clear the internal framebuffer to the given color (0=black, 0xFF=white).
    void ColorClear(uint8_t color);

    // Push the whole framebuffer to the panel.
    void Display();

    // Set a single pixel in the internal framebuffer.
    void SetPixel(uint16_t x, uint16_t y, uint8_t color);

    // Convert a firmware 1bpp image buffer (MSB-first, black=0, white=1)
    // into the internal framebuffer. `blackIsZero` should be true for the
    // firmware's imgBuf convention.
    void Blit1bpp(const uint8_t *src, int srcW, int srcH, bool blackIsZero = true);
};
