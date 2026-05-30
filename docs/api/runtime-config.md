# Runtime Config API

运行时配置 API 给 Web 前端和桌面精灵读取轻量开关。它不返回密钥，也不替代 `.env`。

## 读取运行时配置

```http
GET /api/config/runtime
```

响应：

```json
{
  "elf": {
    "enabled": true
  }
}
```

当前字段：

```text
elf.enabled
  true：允许 Web / 桌面精灵加载。
  false：不加载精灵；精灵工坊页面仍可访问。
```

实现说明：

```text
接口每次读取最新 config.json5，避免被后端启动时 settings 缓存卡住。
前端请求使用 no-store。
桌面精灵启动时如果接口暂不可用，会等待并重试；只有明确读到 false 才保持隐藏。
```

相关文件：

```text
backend/app/api/app_config.py
backend/app/core/config.py
frontend/src/shared/runtimeConfig.ts
frontend/src/features/jobs/JobDrawer.tsx
desktop/src/main.ts
```
