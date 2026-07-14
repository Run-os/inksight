import sys

with open('../src/main.cpp', 'rb') as f:
    data = f.read()

NL = chr(0x5C) + 'n'  # \n as two bytes, NOT a newline
B = lambda s: s.encode()

reps = [
    # LIVE
    (B('"Temporary online window: %lu min"'),    B('"临时在线窗口：%lu 分钟"')),
    (B('"Temporary online window expired, returning to interval mode'), B('"临时在线窗口已过期，返回间隔模式')),
    (B('"WiFi connected'),                        B('"WiFi 已连接')),
    (B('"WiFi reconnect failed'),                 B('"WiFi 重连失败')),
    (B('"Pending action detected, refreshing now'),B('"检测到待处理操作，立即刷新')),
    (B('"Backend requested interval mode'),       B('"后端要求间隔模式')),
    (B('"Fallback %d min elapsed, refreshing content...'), B('"备用 %d 分钟已过，刷新内容...')),

    # FOCUS
    (B('"fetch failed, keeping previous flags'),  B('"获取失败，沿用上次标志')),
    (B('"listening=%d alwaysActive=%d'),          B('"监听=%d 常驻活跃=%d')),

    # PORTAL
    (B('"AP: %s (timeout %lums)'),                B('"热点：%s（超时 %lums）')),
    (B('"Timeout %lus'),                          B('"超时 %lus')),

    # DIAG
    (B('" | SSID=%s | Server=%s'),                B('" | SSID=%s | 服务器=%s')),
    (B('"Showing cached content (offline mode)'), B('"显示缓存内容（离线模式）')),
    (B('"No cached content; will retry on next refresh cycle'), B('"无缓存内容；下个刷新周期重试')),
    (B('"WiFi unreachable, quick retry sweep %d/%d in %lus'), B('"WiFi 不可达，快速重试 %d/%d（%lus 后）')),
    (B('"WiFi recovered'),                        B('"WiFi 已恢复')),
    (B('"WiFi still unreachable -> captive portal'), B('"WiFi 仍不可达 -> 配网门户')),

    # TODO
    (B('"fetch empty/failed; keeping current screen'), B('"拉取为空/失败；保持当前屏幕')),
    (B('"rendered page %d/%d (%d items)'),        B('"已渲染第 %d/%d 页（%d 项）')),

    # VIEW
    (B('"next -> %s'),                            B('"下一页 -> %s')),
    (B('"prev -> %s'),                            B('"上一页 -> %s')),

    # SETTINGS
    (B('"entered'),                               B('"已进入')),
    (B('"exited'),                                B('"已退出')),
    (B('"cursor=%d'),                             B('"光标=%d')),
    (B('"confirm item %d (detail)'),              B('"确认第 %d 项（详情）')),

    # REFRESH
    (B('"Triggering immediate refresh...'),       B('"触发立即刷新...')),
    (B('"Restoring previous image after failed next-mode refresh'), B('"下模式刷新失败，恢复上一张图片')),
    (B('"Content unchanged, skipping display refresh'), B('"内容未变，跳过屏幕刷新')),
    (B('"Displaying new content...'),             B('"正在显示新内容...')),
    (B('"Display done'),                          B('"显示完成')),
    (B('"Fetch failed, retrying on existing WiFi...'), B('"拉取失败，在当前 WiFi 上重试...')),
    (B('"Fetch failed, retrying after reconnect...'),  B('"拉取失败，重连后重试...')),
    (B('"Retry succeeded'),                       B('"重试成功')),
    (B('"Retry also failed, keeping old content'),B('"重试也失败，保留旧内容')),
    (B('"WiFi reconnect failed, keeping old content'), B('"WiFi 重连失败，保留旧内容')),
    (B('"WiFi reconnect failed'),                 B('"WiFi 重连失败')),

    # BOOT
    (B('"Content not ready, retry %d/%d'),        B('"内容未就绪，重试 %d/%d')),
    (B('"Config button held during wait -> portal'), B('"等待期间配置键长按 -> 配网门户')),
    (B('"Content is ready'),                      B('"内容已就绪')),
    (B('"FW_DEBUG_TAG=%s build=%s %s'),           B('"固件指纹=%s 构建=%s %s')),
    (B('"BTN map: BOOT(GPIO0) short=next/down long=prev/exit | KEY(GPIO18) short=confirm long=settings'),
     B('"按键映射：BOOT(GPIO0) 短按=下一页/下移 长按=上一页/退出 | KEY(GPIO18) 短按=确认 长按=进/出设置')),
    (B('"EPD ready'),                             B('"EPD 就绪')),
    (B('"No server URL configured -> portal'),    B('"未配置服务器地址 -> 配网门户')),
    (B('"User aborted during WiFi connect -> portal'), B('"WiFi 连接期间用户中断 -> 配网门户')),
    (B('"User aborted during focus fetch -> portal'),  B('"Focus 拉取期间用户中断 -> 配网门户')),
    (B('"Fetching todos...'),                     B('"正在拉取待办...')),
    (B('"Boot complete (inksight-server mode), entering main loop (always on)'),
     B('"启动完成（inksight-server 模式），进入主循环（常驻在线）')),

    # HB
    (B('"FW_DEBUG_TAG=%s build=%s %s state=%d view=%d wifi=%s rtc=%d heap=%d'),
     B('"指纹=%s 构建=%s %s 状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d')),
    (B('"state=%d view=%d wifi=%s rtc=%d heap=%d'),
     B('"状态=%d 视图=%d WiFi=%s 时钟=%d 堆=%d')),

    # DEBUG / generic
    (B('"%d min elapsed, refreshing content...'), B('"%d 分钟已过，刷新内容...')),

    # NTP
    (B('"RTC became valid; repainting current view'), B('"时钟已校准；重绘当前视图')),

    # MEM
    (B('"alertBackupBuf malloc failed; focus alerts disabled'), B('"alertBackupBuf 分配失败；Focus 告警已禁用')),
]

count = 0
for old, new in reps:
    n = data.count(old)
    if n > 0:
        data = data.replace(old, new)
        count += n
    else:
        print(f'WARN: not found: {old[:60]}...')

print(f'Total replacements: {count}')

with open('../src/main.cpp', 'wb') as f:
    f.write(data)
print('Done.')
