# Runtime Config API

运行时配置 API 给 Web 前端和桌面精灵读取轻量状态。它不返回密钥，也不替代 `.env`。

## 读取运行时配置

```http
GET /api/config/runtime
```

响应：

```json
{
  "elf": {
    "voice_mode_enabled": false
  }
}
```

当前字段：

```text
elf.voice_mode_enabled
  true：精灵处于持续语音对话模式。
  false：精灵保留文本交互和气泡播报，但不进入持续语音对话。
```

实现说明：

```text
语音模式来自 runtime config / config.json5 的有效值。
前端请求使用 no-store。
桌面精灵不再通过该接口判断是否隐藏自身；精灵显示是前端/桌面渲染行为，不等同于关闭精灵能力。
```

相关文件：

```text
backend/app/api/app_config.py
backend/app/core/config.py
frontend/src/shared/runtimeConfig.ts
frontend/src/features/jobs/JobDrawer.tsx
desktop/src/main.ts
```
