# InkSight 固件项目记忆（长期）

## 构建环境注意（重要）
- 项目路径含中文（`D:/文档/...`）时，PlatformIO ESP32 链接阶段会报
  `ld.exe: cannot open map file .../firmware.map: No such file or directory`。
  原因：PlatformIO ESP32 平台脚本自动给链接器加 `-Wl,--Map`，Windows 版 GNU ld 无法处理中文路径。
  已修复：`platformio.ini` 的 `[platformio]` 段设 `build_dir = C:/Users/liuyz/inksight_pio_build`（纯 ASCII）。
  若换机器/路径，遇到同样的 map 报错 → 把 build_dir 指向无中文的路径即可。

## 显示驱动架构（firmware/src）
- 显示抽象层：`epd_driver.cpp` 按面板宏 `#if ... #elif defined(EPD_PANEL_xxx)` 分派；
  上层 `display.cpp`/`main.cpp` 只通过 `epd_driver.h` 接口 + 统一 `imgBuf`（1bpp，黑=0，MSB 优先）访问，新增面板无需改上层。
- 已支持的面板宏：`EPD_PANEL_42_SSD1683_BW`、`EPD_PANEL_42_GXEPD2_GYE042A87`、`EPD_PANEL_42_RLCD`（ST7305 反射式 LCD）、`EPD_PANEL_29`、`EPD_PANEL_583_*`、`EPD_PANEL_75`、`EPD_PANEL_42_WFT`、`EPD_PANEL_42_DKE_RY683`、`EPD_PANEL_42_GDEM042F52` 等。
- 新增反射式 LCD 驱动套路：写 `rlcd_bsp.{h,cpp}`（Arduino SPI 版，移植官方示例），在 epd_driver.cpp 加 `#elif defined(EPD_PANEL_42_RLCD)` 分支调用它；config.h 加 `BOARD_PROFILE_xxx` 引脚；platformio.ini 加 env。

## 板型分支约定
- ESP32-S3 板型（含 `BOARD_PROFILE_YD_ESP32_S3_N16R8`、`BOARD_PROFILE_RLCD_S3`）共用：电池 ADC 用 `analogReadResolution(12)+ADC_11db` + `esp_adc_cal` 校准（R1=R2=10k，×2）；MAC 用 `WiFi.macAddress()`。新增 S3 板型时记得在 `main.cpp`/`network.cpp` 的这些条件分支里补上。
- `BOARD_HAS_AUDIO` 由具体 env 的 build_flags 控制（如 AI Chat 版），RLCD_S3 未定义该宏 → 不含音频 codec。

## 反射式 LCD（RLCD）特性
- 掉电/深度睡眠不保图；唤醒后由 main.cpp 重绘 `smartDisplay(imgBuf)`，内容不丢。
- 无 partial refresh 概念，partial 调用退化为全屏重绘。
- 弱光对比度低，强光/日光下清晰。
