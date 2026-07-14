"""
One-shot patch for main.cpp:
  1. Add #include "esp_log.h"
  2. Replace all debug Serial.* calls with log_printf
  3. Translate all debug strings to Chinese
"""
import re

with open('../src/main.cpp', 'r', encoding='utf-8') as f:
    content = f.read()

# === Step 1: add esp_log.h ===
content = content.replace(
    '#include <WiFi.h>',
    '#include <WiFi.h>\n#include "esp_log.h"'
)

# === Step 2: Serial -> log_printf ===
# Serial.printf("fmt", ...)  -> log_printf("fmt", ...)
content = content.replace('Serial.printf(', 'log_printf(')

# Serial.println() -> log_printf("\\n")
content = content.replace('Serial.println()', 'log_printf("\\n")')

# Serial.println("xxx"); -> log_printf("xxx\\n");
def repl_println(m):
    return m.group(1) + 'log_printf("' + m.group(2) + '\\n");' + m.group(3)
content = re.sub(
    r'(\s*)Serial\.println\("([^"]*)"\);(.*)',
    repl_println,
    content
)

# === Step 3: translate ===
translations = {
    '[LIVE] Temporary online window: %lu min':   '[LIVE] 临时在线窗口：%lu 分钟',
    '[LIVE] Temporary online window expired, returning to interval mode': '[LIVE] 临时在线窗口已过期，返回间隔模式',
    '[LIVE] WiFi connected':                      '[LIVE] WiFi 已连接',
    '[LIVE] WiFi reconnect failed':               '[LIVE] WiFi 重连失败',
    '[LIVE] Pending action detected, refreshing now': '[LIVE] 检测到待处理操作，立即刷新',
    '[LIVE] Backend requested interval mode':     '[LIVE] 后端要求间隔模式',
    '[LIVE] Fallback %d min elapsed, refreshing content...': '[LIVE] 备用 %d 分钟已过，刷新内容...',
    '[FOCUS] fetch failed, keeping previous flags':'[FOCUS] 获取失败，沿用上次标志',
    '[FOCUS] listening=%d alwaysActive=%d':        '[FOCUS] 监听=%d 常驻活跃=%d',
    '[PORTAL] AP: %s (timeout %lums)':             '[PORTAL] 热点：%s（超时 %lums）',
    '[PORTAL] Timeout %lus — rebooting to retry':  '[PORTAL] 超时 %lus——重启重试',
    '[DIAG] %s | SSID=%s | Server=%s':             '[DIAG] %s | SSID=%s | 服务器=%s',
    'Showing cached content (offline mode)':        '显示缓存内容（离线模式）',
    'No cached content; will retry on next refresh cycle': '无缓存内容；下个刷新周期重试',
    '[DIAG] WiFi unreachable, quick retry sweep %d/%d in %lus': '[DIAG] WiFi 不可达，快速重试 %d/%d（%lus 后）',
    '[DIAG] WiFi recovered':                        '[DIAG] WiFi 已恢复',
    '[DIAG] WiFi still unreachable -> captive portal': '[DIAG] WiFi 仍不可达 -> 配网门户',
    '[TODO] fetch empty/failed; keeping current screen': '[TODO] 拉取为空/失败；保持当前屏幕',
    '[TODO] rendered page %d/%d (%d items)':        '[TODO] 已渲染第 %d/%d 页（%d 项）',
    '[VIEW] next -> %s':                            '[VIEW] 下一页 -> %s',
    '[VIEW] prev -> %s':                            '[VIEW] 上一页 -> %s',
    '[SETTINGS] entered':                           '[SETTINGS] 已进入',
    '[SETTINGS] exited':                            '[SETTINGS] 已退出',
    '[SETTINGS] cursor=%d':                         '[SETTINGS] 光标=%d',
    '[SETTINGS] confirm item %d (detail)':          '[SETTINGS] 确认第 %d 项（详情）',
    '[REFRESH] Triggering immediate refresh...':    '[REFRESH] 触发立即刷新...',
    '[REFRESH] Restoring previous image after failed next-mode refresh': '[REFRESH] 下模式刷新失败，恢复上一张图片',
    'Content unchanged, skipping display refresh':   '内容未变，跳过屏幕刷新',
    'Displaying new content...':                     '正在显示新内容...',
    'Display done':                                  '显示完成',
    'Fetch failed, retrying on existing WiFi...':   '拉取失败，在当前 WiFi 上重试...',
    'Fetch failed, retrying after reconnect...':    '拉取失败，重连后重试...',
    'Retry succeeded':                               '重试成功',
    'Retry also failed, keeping old content':        '重试也失败，保留旧内容',
    'WiFi reconnect failed, keeping old content':    'WiFi 重连失败，保留旧内容',
    'WiFi reconnect failed':                         'WiFi 重连失败',
    '[BOOT] Content not ready, retry %d/%d':         '[BOOT] 内容未就绪，重试 %d/%d',
    '[BOOT] Config button held during wait -> portal': '[BOOT] 等待期间配置键长按 -> 配网门户',
    '[BOOT] Content is ready':                        '[BOOT] 内容已就绪',
    '[BOOT] FW_DEBUG_TAG=%s build=%s %s':             '[BOOT] 固件指纹=%s 构建=%s %s',
    '[BOOT] BTN map: BOOT(GPIO0) short=next/down long=prev/exit | KEY(GPIO18) short=confirm long=settings': '[BOOT] 按键映射：BOOT(GPIO0) 短按=下一页/下移 长按=上一页/退出 | KEY(GPIO18) 短按=确认 长按=进/出设置',
    'EPD ready':                                      'EPD 就绪',
    'No server URL configured -> portal':             '未配置服务器地址 -> 配网门户',
    'User aborted during WiFi connect -> portal':     'WiFi 连接期间用户中断 -> 配网门户',
    'User aborted during focus fetch -> portal':      'Focus 拉取期间用户中断 -> 配网门户',
    'Fetching todos...':                              '正在拉取待办...',
    'Boot complete (inksight-server mode), entering main loop (always on)': '启动完成（inksight-server 模式），进入主循环（常驻在线）',
    '[HB] FW_DEBUG_TAG=%s build=%s %s state=%d view=%d wifi=%s rtc=%d heap=%d': '[HB] 指纹=%s 构建=%s %s 状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d',
    '[HB] state=%d view=%d wifi=%s rtc=%d heap=%d':   '[HB] 状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d',
    '[DEBUG] %d min elapsed, refreshing content...':  '[DEBUG] %d 分钟已过，刷新内容...',
    '%d min elapsed, refreshing content...':           '%d 分钟已过，刷新内容...',
    '[NTP] RTC became valid; repainting current view': '[NTP] 时钟已校准；重绘当前视图',
    '[MEM] alertBackupBuf malloc failed; focus alerts disabled': '[MEM] alertBackupBuf 分配失败；Focus 告警已禁用',
}

count = 0
for eng, chn in translations.items():
    n = content.count(eng)
    if n > 0:
        content = content.replace(eng, chn)
        count += n
    else:
        print(f'WARN: not found: {eng[:70]}')

print(f'Translations: {count}')
print(f'Remaining Serial.print: {content.count("Serial.print")}  (should be 1 = Serial.begin)')

with open('../src/main.cpp', 'w', encoding='utf-8', newline='') as f:
    f.write(content)
print('Done.')
