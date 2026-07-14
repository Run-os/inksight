#pragma once

#include <Arduino.h>

#define RLCD_ALGO_LUT 3

class DisplayPort {
private:
    int mosi_;
    int scl_;
    int dc_;
    int cs_;
    int rst_;
    int width_;
    int height_;
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
    inline void spi_write_bit(bool bit);
    inline void spi_write_byte(uint8_t data);

public:
    DisplayPort(int mosi, int scl, int dc, int cs, int rst, int width, int height);
    ~DisplayPort();

    void Init();
    void ColorClear(uint8_t color);
    void Display();
    void SetPixel(uint16_t x, uint16_t y, uint8_t color);
    void Blit1bpp(const uint8_t *src, int srcW, int srcH, bool blackIsZero = true);
};
