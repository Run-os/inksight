# InkSight 固件项目记忆（长期）

## 项目总览
- **项目**：InkSight —— ESP32-S3-RLCD-4.2（ST7305 反射式 LCD 400×300，1bpp）固件 + 后端。
- 设备**常驻在线**（无深度睡眠），黑白墨水长屏；核心功能：① 原生待办列表 ② 网络图片展示（后端下发 1-bit BMP）。
- 已完成重构：RLCD-only；移除深度睡眠/音频/多面板分支；统一 `imgBuf`（1bpp，黑=0，MSB 优先）。
- **当前字体**：`firmware/src/cjk16.h` 中 CJK 16×16 点阵；ASCII 为 **18px 比例点阵 `ascii18`**
  （从 `Inter_24pt-Medium.ttf` 提取，单元格 20×23，BASE=18，CAP=4，按 `ascii18_width + 1px` 步进）。
  所有英文路径已统一用 ascii18；旧 5×7 `getGlyph` 已删除。
  生成脚本：`firmware/tools/gen_ascii20.py --size 18 --apply`。
- **当前状态**：固件编译通过（Flash 22.9% / RAM 24.9%），`firmware_merged.bin` 已生成；**待真机刷机联调**。

## 构建环境
- PlatformIO 在中文路径下链接失败（`ld.exe cannot open map file`），已在 `platformio.ini` 设
  `build_dir = C:/Users/liuyz/inksight_pio_build`（纯 ASCII）。
- **分区表坑**：`partitions/inksight_16mb_ota.csv` 数据分区须让 `LittleFS` 可用。
  该工具链 `gen_esp32part` **不认 `littlefs` 关键字**，只接受数字 subtype；故写成
  `spiffs, data, 0x83, 0xa10000, 0x5f0000,`（Name 保留 `spiffs` 以匹配 `LittleFS.begin` 默认 `partitionLabel="spiffs"`，
  subtype `0x83`=littlefs 让 esp_littlefs 能格式化）。改分区表后**必须 erase+重烧**，否则旧分区表仍使 LittleFS 挂载失败(-84)。

## 显示与架构
- `epd_driver.cpp` 只剩 `EPD_PANEL_42_RLCD` 实现；`platformio.ini` 单 env `epd_42_rlcd_s3_n16r8`。
- 无 partial refresh；partial 调用 = 全屏重绘。掉电（硬断电）不保图。
- 启动流程：先 `showBootScreen()`，再 WiFi/后端请求。

## 按键
- 固件可读键：**BOOT(GPIO0)**、**KEY(GPIO18)**。**PWR 是硬件电源开关**（短按上电/长按断电），不接 GPIO，固件无法检测。
- **最终映射**：BOOT 短按=下一页/光标下移；BOOT 长按=上一页/退出设置；KEY 短按=确认（设置内）；KEY 长按=进/出设置。
- 设置界面：`AppView::SETTINGS` + `renderSettingsScreen`，两级菜单（系统设置 → 重新配网 / MAC / WiFi 名）。

## 时间/时区
- **时间主源 = NTP**：`network.cpp::syncNTP()` 调 `configTime(NTP_UTC_OFFSET, 0, "ntp1.ntsc.ac.cn")`
  （原 aliyun/pool.ntp/time.google，已统一改为 `ntp1.ntsc.ac.cn`）。
- **不使用后端 HTTP `Date` 头做时间源**（用户明确要求撤掉该方案）：已删除 `applyServerTime` /
  `g_serverTimeSet` / `collectHeaders({"Date"})` 及相关解析函数（HTTP_MONTHS/civilToDays/parseHttpDateUTC）。
- `configTime` 正偏移可能回退 UTC，故 `syncNTP` 显式 `setenv("TZ","CST-8",1); tzset();` 强制北京时。
- 动态刷新：`loop()` 检测分钟变化，调用 `repaintTodoView()` 用缓存待办 + 实时电量重绘，不重新拉后端。

## 后端/图片
- 新后端：`inksight-server`（FastAPI，esp32.122050.xyz），使用 ArduinoJson v7 + `setInsecure()` 跳证书。
- 旧 `backend/` 目录尚未清理；OTA 在 `INKSIGHT_BACKEND_V2` 下为 no-op，需重新规划。
- 图片模式待补：`inksight-server/images/` 为空，manifest 为空，设备进图片视图会失败。

## 字体修改流程（关键约定）
- **结论**：中文固定 16×16 点阵；英文用矢量 TTF 提取，与中文不等高（中文铺满 16px，英文 cap-height≈0.75×字号）。中文 16px 配 **英文 18px** 视觉等高，已定稿。
- **改英文字号的标准步骤**（已验证可复用，详见技能 `inksight-font-change`）：
  1. `firmware/tools/gen_ascii20.py --size N --apply`：运行时**自动测量** Inter TTF 几何（AW/AH/BASE/CAP/OFF，不硬编码），生成 `ascii{N}_*` 数据并 regex 替换 `cjk16.h` 的 ASCII 区块。
  2. `firmware/src/display.cpp` 全局重命名 `ascii20→ascii{N}`、`ASCII20→ASCII{N}`、`drawAscii20→drawAscii{N}`（注意 cjk16.h 里的 `gen_ascii20.py` 工具名引用**不要改**）。
  3. 编译：`cd firmware && pio run -e epd_42_rlcd_s3_n16r8`（build_dir 已设 ASCII 路径，避开中文路径 link 失败）。
  4. 视觉验证：`firmware/tools/render_ascii{N}_check.py` 解析**真实烧录**的位图 + 16px 中文混排，确认无截断、cap-top 与中文顶端对齐。
- **对齐规则**：中文顶端 y，英文画在 `y - ASCII{N}_CAP`（CAP=大写'H'顶端行）；步进 `cx += ascii{N}_width[cp] + 1`（比例字宽 + 1px 间距）。
- **⚠️ 字形注释不可含行末反斜杠**（2026-07-14 踩坑）：`gen_ascii20.py` 给每条字形写行内注释 `// 0x{cp:02X} {disp}`，
  其中 `disp` 曾用字形原始字符。对 `\`(0x5C)，注释变成 `// 0x5C \`，末尾反斜杠在 C 里是**行续接**，
  会把下一行字形条目整行吞进注释 → 编译期数组缺一项、'\\' 之后所有字形错位 +1（曾导致 "milk" 渲染成 "njrh"，
  而源码肉眼/Python 都数成 95 项正确，极隐蔽）。已修：`disp = "BS" if ch=="\\\\" else ch`。
  生成/手改字形表后，务必确认无 `// xxx\` 结尾的行。

## 未决事项
- 真机刷机确认：时间、键位、设置界面、电量动态刷新、中英文混排视觉。
- 图片生成脚本与 1-bit 样式规则。
- 旧 backend 清理与 OTA 新端点。
