// ─────────────────────────────────────────────────────────────────────────────
// RLCD (ESP32-S3-RLCD-4.2) reflective LCD driver — ST7305 controller.
// Software SPI implementation (bit-banging), since hardware HSPI/FSPI is used
// by the SD card module on this board.
// See rlcd_bsp.h for the pixel-packing description.
// ─────────────────────────────────────────────────────────────────────────────

#include "rlcd_bsp.h"
#include <cassert>

DisplayPort::DisplayPort(int mosi, int scl, int dc, int cs, int rst, int width, int height)
    : mosi_(mosi), scl_(scl), dc_(dc), cs_(cs), rst_(rst), width_(width), height_(height) {
    pinMode(dc_, OUTPUT);
    pinMode(cs_, OUTPUT);
    pinMode(rst_, OUTPUT);
    pinMode(mosi_, OUTPUT);
    pinMode(scl_, OUTPUT);
    digitalWrite(cs_, HIGH);
    digitalWrite(dc_, HIGH);
    digitalWrite(rst_, HIGH);
    digitalWrite(mosi_, LOW);
    digitalWrite(scl_, LOW);

    int transfer = width_ * height_;
    DisplayLen = transfer >> 3;
    DispBuffer = (uint8_t *)malloc(DisplayLen);
    assert(DispBuffer);

#if (RLCD_ALGO_LUT == 3)
    PixelIndexLUT = (uint16_t(*)[300])malloc(transfer * sizeof(uint16_t));
    PixelBitLUT   = (uint8_t(*)[300])malloc(transfer * sizeof(uint8_t));
    assert(PixelIndexLUT);
    assert(PixelBitLUT);
    if (width_ == 400) {
        InitLandscapeLUT();
    } else {
        InitPortraitLUT();
    }
#endif
}

DisplayPort::~DisplayPort() {
    if (DispBuffer) free(DispBuffer);
#if (RLCD_ALGO_LUT == 3)
    if (PixelIndexLUT) free(PixelIndexLUT);
    if (PixelBitLUT) free(PixelBitLUT);
#endif
}

void DisplayPort::SetResetIOLevel(uint8_t level) {
    digitalWrite(rst_, level ? HIGH : LOW);
}

void DisplayPort::Reset() {
    SetResetIOLevel(1);
    delay(50);
    SetResetIOLevel(0);
    delay(20);
    SetResetIOLevel(1);
    delay(50);
}

inline void DisplayPort::spi_write_bit(bool bit) {
    digitalWrite(mosi_, bit ? HIGH : LOW);
    delayMicroseconds(1);
    digitalWrite(scl_, HIGH);
    delayMicroseconds(1);
    digitalWrite(scl_, LOW);
    delayMicroseconds(1);
}

inline void DisplayPort::spi_write_byte(uint8_t data) {
    for (int i = 7; i >= 0; i--) {
        spi_write_bit((data >> i) & 1);
    }
}

void DisplayPort::SendCommand(uint8_t reg) {
    digitalWrite(dc_, LOW);
    digitalWrite(cs_, LOW);
    spi_write_byte(reg);
    digitalWrite(cs_, HIGH);
}

void DisplayPort::SendData(uint8_t data) {
    digitalWrite(dc_, HIGH);
    digitalWrite(cs_, LOW);
    spi_write_byte(data);
    digitalWrite(cs_, HIGH);
}

void DisplayPort::SendBuffer(const uint8_t *data, int len) {
    digitalWrite(dc_, HIGH);
    digitalWrite(cs_, LOW);
    for (int i = 0; i < len; i++) {
        spi_write_byte(data[i]);
    }
    digitalWrite(cs_, HIGH);
}

// ── ST7305 init sequence (from vendor example, verbatim) ────────────────────
void DisplayPort::Init() {
    Reset();

    SendCommand(0xD6);
    SendData(0x17);
    SendData(0x02);

    SendCommand(0xD1);
    SendData(0x01);

    SendCommand(0xC0);
    SendData(0x11);
    SendData(0x04);

    SendCommand(0xC1);
    SendData(0x69);
    SendData(0x69);
    SendData(0x69);
    SendData(0x69);

    SendCommand(0xC2);
    SendData(0x19);
    SendData(0x19);
    SendData(0x19);
    SendData(0x19);

    SendCommand(0xC4);
    SendData(0x4B);
    SendData(0x4B);
    SendData(0x4B);
    SendData(0x4B);

    SendCommand(0xC5);
    SendData(0x19);
    SendData(0x19);
    SendData(0x19);
    SendData(0x19);

    SendCommand(0xD8);
    SendData(0x80);
    SendData(0xE9);

    SendCommand(0xB2);
    SendData(0x02);

    SendCommand(0xB3);
    SendData(0xE5);
    SendData(0xF6);
    SendData(0x05);
    SendData(0x46);
    SendData(0x77);
    SendData(0x77);
    SendData(0x77);
    SendData(0x77);
    SendData(0x76);
    SendData(0x45);

    SendCommand(0xB4);
    SendData(0x05);
    SendData(0x46);
    SendData(0x77);
    SendData(0x77);
    SendData(0x77);
    SendData(0x77);
    SendData(0x76);
    SendData(0x45);

    SendCommand(0x62);
    SendData(0x32);
    SendData(0x03);
    SendData(0x1F);

    SendCommand(0xB7);
    SendData(0x13);

    SendCommand(0xB0);
    SendData(0x64);

    SendCommand(0x11);
    delay(200);
    SendCommand(0xC9);
    SendData(0x00);

    SendCommand(0x36);
    SendData(0x48);

    SendCommand(0x3A);
    SendData(0x11);

    SendCommand(0xB9);
    SendData(0x20);

    SendCommand(0xB8);
    SendData(0x29);

    SendCommand(0x21);

    SendCommand(0x2A);
    SendData(0x12);
    SendData(0x2A);

    SendCommand(0x2B);
    SendData(0x00);
    SendData(0xC7);

    SendCommand(0x35);
    SendData(0x00);

    SendCommand(0xD0);
    SendData(0xFF);

    SendCommand(0x38);
    SendCommand(0x29);

    ColorClear(0xFF);
    Display();
}

void DisplayPort::ColorClear(uint8_t color) {
    memset(DispBuffer, color, DisplayLen);
}

void DisplayPort::Display() {
    SendCommand(0x2A);
    SendData(0x12);
    SendData(0x2A);

    SendCommand(0x2B);
    SendData(0x00);
    SendData(0xC7);

    SendCommand(0x2C);
    SendBuffer(DispBuffer, DisplayLen);
}

#if (RLCD_ALGO_LUT == 3)
void DisplayPort::InitPortraitLUT() {
    uint16_t W4 = width_ >> 2;
    for (uint16_t y = 0; y < height_; y++) {
        uint16_t byte_y = y >> 1;
        uint8_t local_y = y & 1;
        for (uint16_t x = 0; x < width_; x++) {
            uint16_t byte_x = x >> 2;
            uint8_t local_x = x & 3;
            uint32_t index = byte_y * W4 + byte_x;
            uint8_t bit = 7 - ((local_x << 1) | local_y);
            PixelIndexLUT[x][y] = index;
            PixelBitLUT[x][y] = (1 << bit);
        }
    }
}

void DisplayPort::InitLandscapeLUT() {
    uint16_t H4 = height_ >> 2;
    for (uint16_t y = 0; y < height_; y++) {
        uint16_t inv_y = height_ - 1 - y;
        uint16_t block_y = inv_y >> 2;
        uint8_t local_y = inv_y & 3;
        for (uint16_t x = 0; x < width_; x++) {
            uint16_t byte_x = x >> 1;
            uint8_t local_x = x & 1;
            uint32_t index = byte_x * H4 + block_y;
            uint8_t bit = 7 - ((local_y << 1) | local_x);
            PixelIndexLUT[x][y] = index;
            PixelBitLUT[x][y] = (1 << bit);
        }
    }
}

void DisplayPort::SetPixel(uint16_t x, uint16_t y, uint8_t color) {
    if (x >= width_ || y >= height_) return;
    uint32_t idx = PixelIndexLUT[x][y];
    uint8_t mask = PixelBitLUT[x][y];
    uint8_t *p = &DispBuffer[idx];
    if (color)
        *p |= mask;
    else
        *p &= ~mask;
}
#endif

void DisplayPort::Blit1bpp(const uint8_t *src, int srcW, int srcH, bool blackIsZero) {
    int rowBytes = srcW / 8;
    for (int y = 0; y < srcH; y++) {
        for (int x = 0; x < srcW; x++) {
            uint8_t bit = src[y * rowBytes + (x / 8)] & (0x80 >> (x % 8));
            bool isWhite = blackIsZero ? (bit != 0) : (bit == 0);
            SetPixel(x, y, isWhite ? 0xFF : 0x00);
        }
    }
}
