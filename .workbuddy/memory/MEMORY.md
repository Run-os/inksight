# InkSight 固件项目记忆（长期）

## 构建环境注意（重要）
- 项目路径含中文（`D:/文档/...`）时，PlatformIO ESP32 链接阶段会报
  `ld.exe: cannot open map file .../firmware.map: No such file or directory`。
  原因：PlatformIO ESP32 平台脚本自动给链接器加 `-Wl,--Map`，Windows 版 GNU ld 无法处理中文路径。
  已修复：`platformio.ini` 的 `[platformio]` 段设 `build_dir = C:/Users/liuyz/inksight_pio_build`（纯 ASCII）。
  若换机器/路径，遇到同样的 map 报错 → 把 build_dir 指向无中文的路径即可。

## 显示驱动架构（firmware/src）— 已于 2026-07-13 收敛为 RLCD-only
- **现状**：`epd_driver.cpp` 只剩 `EPD_PANEL_42_RLCD` 一条实现（~90 行），其余面板分支
  （SSD1683/WFT/GxEPD2/DKE_RY683/GDEM042F52/2.9/5.83/7.5）已删除；`epd_driver.h` 删了 `epdDisplay2bpp`/`epdSleep`。
- 上层 `display.cpp`/`main.cpp` 仍只通过 `epd_driver.h` 接口 + 统一 `imgBuf`（1bpp，黑=0，MSB 优先）访问。
- `platformio.ini` 只剩 `epd_42_rlcd_s3_n16r8` 一个 env；`lib_deps` 已清空（GxEPD2/Adafruit/WebSockets 不再被引用，
  rlcd_bsp 只依赖 Arduino/SPI 框架头）。
- 历史：曾用 `#if defined(EPD_PANEL_xxx)` 多面板分派，重构后移除。如需再支持多面板，恢复该分派结构即可。
- 新增反射式 LCD 驱动套路：写 `rlcd_bsp.{h,cpp}`（Arduino SPI 版，移植官方示例），epd_driver.cpp 调用；
  config.h 加 `BOARD_PROFILE_xxx` 引脚；platformio.ini 加 env。

## 反射式 LCD（RLCD）特性
- **设备现为常驻在线（always-on）**：2026-07-13 重构移除了全部深度睡眠/唤醒逻辑（main.cpp 删
  `enterDeepSleep`/`detectWakeupReason`/`WakeupReason`/`esp_sleep.h`；epd_driver 删 `epdSleep`）。
  loop() 连续运行，handleLiveMode/handleFailure 按 `refreshIntervalMs()` 重试而非睡眠。
- 启动流程：setup() 中 `showBootScreen()`（白底+"InkSight"/"Initializing..."）在 epdInit 后、
  WiFi/网络请求前渲染 → 确保**通电先出启动页再跑业务**。
- 掉电（硬断电）不保图；重新上电后 main.cpp 重绘 `smartDisplay(imgBuf)`。
- 无 partial refresh 概念，partial 调用退化为全屏重绘（epdPartialDisplayWithOld 直接 epdDisplay(imgBuf)）。
- 弱光对比度低，强光/日光下清晰。

## 板型分支约定（重构后仅 RLCD_S3）
- 现 config.h 用 `#ifndef BOARD_PROFILE_RLCD_S3 / #error` 守卫，只认 RLCD_S3；其余板型分支已删。
- RLCD_S3 电池 ADC：`analogReadResolution(12)+ADC_11db` + `esp_adc_cal` 校准（network.cpp readBatteryVoltage
  有 `#if defined(BOARD_PROFILE_RLCD_S3)` 线性转换分支）；MAC 用 `WiFi.macAddress()`。
- 无 `BOARD_HAS_AUDIO`（音频 codec/voice 代码已整体删除）。

## ESP32-S3-RLCD-4.2 按键与电源架构（官方 wiki 确认，重要）
- 板载三颗物理键：**BOOT(GPIO0)**、**PWR(电源键)**、**KEY(GPIO18)**；另有 RST=硬件复位键。
- **PWR 键是硬件电源开关**：`长按下电 / 单击上电`。板子"硬断电"时 ESP32 的 3.3V 被切断，芯片根本没电。
- **因此：电池下开机必须单击 PWR 键**；BOOT / RST / KEY 都不是电源键，硬断电态下按它们无任何作用（ESP32 无电），这是硬件设计，固件改不了。
- BOOT(GPIO0)：按住+重新上电→下载模式；已被固件配为深度睡眠唤醒源之一。
- KEY(GPIO18)：固件里短按(50ms–2s)→翻下一页(nextMode)，长按(≥2s)→进配网 portal；也是深度睡眠唤醒源。
- RST：硬件复位（硬重启），非电源键、非 GPIO 功能键。
- 固件"自动深度睡眠"（每次刷新周期结束 `enterDeepSleep`）时 3.3V 仍通，此时 **短按 BOOT 或 KEY 可唤醒**（曾在 `enterDeepSleep` 内把 GPIO0+GPIO18 配为 LOW 唤醒源）。若用户长按 PWR 硬断电，则只能 PWR 单击救活。
- 结论：电池下"按某键开机"的正确键是 **PWR（单击）**；想用 BOOT/KEY 唤醒，应避免长按 PWR 硬断电、改让设备自动深度睡眠。
- **⚠️ 2026-07-13 重构后固件已不再做深度睡眠**（设备常驻在线）：上述"自动深度睡眠/唤醒源"仅作历史记录。
  现状：KEY(GPIO18) 短按翻页/长按进配网；BOOT(GPIO0) 仅作下载模式键。要"唤醒"已无意义（设备不睡），
  唯一让屏幕"复活"的硬件手段仍是上电（PWR 单击 或 RST 复位，后者会硬重启）。

## ima 知识库 MCP 限制（重要）
- 已连接的 `ima-mcp` 连接器**只暴露只读工具**：`get_knowledge_base_list`、`get_knowledge_list`、
  `search_knowledge`、`fetch_media_content`。**没有上传/新建/导入（add/create/import）接口**。
- 因此"把文档导出到 ima 知识库"无法用 MCP 自动完成；只能：写好 Markdown 文件 → 用户在 ima
  客户端手动导入。目标知识库 `esp32-rlcd-4.2` 的 id = `7482084985686315`（个人知识库，can_add_knowledge=true）。
- 若要真正自动推送，需要更换/扩展 ima MCP（提供写接口），或走 ima 官方导入 API/网页上传。

## 新项目核心功能方向（2026-07-13 规划，详见 docs/core-features-plan.md）
- 重构后只保留两大核心功能：① 原生待办列表（设备本地渲染）② 网络图片展示（后端下发 1-bit BMP，本地缓存≤5张，按键切换）。
- 统一 UI：黑白墨水长屏复古像素风，三层（状态栏/内容区/底栏），字号层级 时间>待办正文>日期·提醒·分页·底栏。
- 三项已确认决策：D1 待办由 Web/App 后台管理、设备只拉取展示；D2 图片独立新建 FastAPI 服务 `inksight-content`（与现有 LLM backend 解耦）；D3 按键模型见下。
- **新按键模型（规划已确认，需改固件）**：短按 KEY(GPIO18)=上一页/上一张；短按 BOOT(GPIO0)=下一页/下一张。
  - 规划建议（待用户拍板）：长按 KEY(≥2s)=在待办/图片 App 间切换；长按 BOOT(≥2s)=进配网 Portal（替代原"长按 KEY=portal"）。
  - 此模型**取代**原固件 KEY 短按=翻下一页/长按=portal 的行为，实施时需同步改 `checkConfigButton`。
  - BOOT(GPIO0) 运行时长按安全可复用（下载模式仅"按住+重新上电"触发），`config.h` 需新增 `PIN_NEXT_BTN 0`。
- **关键缺口**：现有 `display.cpp::drawText` 仅 5×7 ASCII 字体，无 CJK 字库，无法渲染中文待办正文。
  原生渲染须新增 `hanzi_font` 16×16 点阵（建议 3500 常用字 ~112KB，放 Flash/PSRAM）+ `draw7Seg` 数码管时间 + `drawMixed` 中英文混排。
- 图片模式：后端把图预处理成内容区尺寸(400×214)的 1-bit BMP 下发，设备原生画 chrome 并 blit 图片缓冲，本地 5 张缓存用 PSRAM。
- 实施里程碑建议（详见文档 §7）：字体与基础绘制 → UI chrome → 后端骨架(待办) → 待办闭环 → 图片后端 → 图片闭环 → 离线/真机验证。
