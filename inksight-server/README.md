# InkSight Server

InkSight 设备（ESP32-S3-RLCD-4.2，400×300 反射屏）的**独立后端服务**，提供两类设备接口：

1. **待办列表** — 对接[滴答清单 (Dida365)](https://developer.dida365.com) Open API
2. **图片资源** — 读取站点 `images/` 目录（图片由配套脚本生成）

设备开机**默认显示待办页**，通过物理按键切换到图片页（固件侧逻辑，默认 `TODO` 状态）。

> 本服务与旧 `backend/`（LLM 内容系统）完全解耦、相互独立。上线稳定运行后，
> 按文末「迁移」步骤移除旧后端，全站切换为本品提供数据支撑。

---

## 目录结构

```
inksight-server/
├── app/
│   ├── main.py            # FastAPI 入口
│   ├── config.py          # .env 配置加载
│   ├── dida/              # 滴答清单 OAuth + Open API 封装
│   │   ├── client.py      #   令牌存储 / 刷新 / 任务拉取
│   │   └── auth.py        #   /admin/dida365/* 授权路由
│   ├── routers/
│   │   ├── todos.py       #   GET /api/todos
│   │   └── images.py      #   GET /api/images/{manifest,file}
│   └── web/templates/index.html   # inksight 风格状态页
├── images/                # 设备图片资源目录（生成脚本写入）
├── .env.example           # 配置模板
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 接口契约

### 待办（设备默认页）

```
GET /api/todos?mac=AA:BB:CC:DD:EE:FF
→ 200 {
  "updated_at": 1752400000,
  "source": "dida365",
  "items": [
    {"id":"...","text":"提交季度报告","done":false,"remind_at":"14:30","due":"2026-07-13","project":"工作"},
    {"id":"...","text":"买牛奶","done":true,"remind_at":"","due":"","project":"生活"}
  ]
}
→ 503  { "detail": "Dida365 未授权：请访问 /admin/dida365/auth 完成授权" }
         （未配置令牌时）
```
可选设备鉴权：设置 `DEVICE_SHARED_SECRET` 后，请求需带 `X-Device-Secret: <值>`。

### 图片（按键切换页）

```
GET /api/images/manifest
→ 200 { "version":"a1b2c3d4e5f6g7h8", "count":3,
       "images":[{"slot":1,"name":"01.bmp","url":"/api/images/01.bmp"}, ...] }

GET /api/images/01.bmp   → 200 image/bmp
```
设备缓存 `version`，相等则跳过下载。

### 管理 / 健康检查

```
GET /                       # inksight 风格状态页
GET /admin/dida365/auth     # 跳转滴答清单授权
GET /admin/dida365/callback # OAuth 回跳（交换令牌并保存）
GET /health                 # {"status":"ok"}
```

---

## 本地开发 / 部署

### 1. 安装依赖

```bash
cd inksight-server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置 .env

```bash
cp .env.example .env
# 编辑 .env，至少填写 DIDA_CLIENT_ID / DIDA_CLIENT_SECRET / DIDA_REDIRECT_URI
```

### 3. 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
# 开发热重载：uvicorn app.main:app --reload --port 8000
```

### 4. 滴答清单授权（一次性）

浏览器打开 `http://<server>:<PORT>/admin/dida365/auth`，按提示用滴答清单账号授权。
回调成功后令牌写入 `.dida_tokens.json`，设备即可拉取待办。
（也可直接在 `.env` 预填 `DIDA_ACCESS_TOKEN` / `DIDA_REFRESH_TOKEN` 跳过网页授权。）

### 5. 云服务器部署建议

- 反代：用 Nginx/Caddy 将域名转发到 `127.0.0.1:8000`，并启用 HTTPS。
- 进程管理：用 systemd / supervisor 守护 `uvicorn`；示例 systemd：
  ```
  ExecStart=/path/to/inksight-server/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```
- 防火墙：仅放行反代端口；`DIDA_REDIRECT_URI` 必须与滴答清单后台登记的回调一致（用公网 https 域名）。
- 图片：部署后由配套生成脚本把图片写入 `images/`。

---

## 迁移（上线后移除旧后端）

本服务验证稳定运行、设备正常拉取待办与图片后，再执行：

1. 停掉旧 `backend/` 服务，确认设备仅指向本服务地址。
2. 删除/归档旧 `backend/` 目录及其相关前端模块（webapp / inksight-mobile 中仅依赖旧 LLM 后端的部分）。
3. 更新固件/前端配置中的后端地址指向本服务。
4. 保留本服务与 `firmware/` 的设备接口契约（见上文）长期维护。

> 注意：**在确认本服务已上线并稳定前，不要删除旧 `backend/`**，以免设备无数据来源。
