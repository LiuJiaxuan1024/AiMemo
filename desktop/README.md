# Memo Elf Desktop PoC

这是 Memo Elf 桌面化的第一版 PoC。目标是验证精灵是否可以跳出浏览器，以透明置顶窗口的形式停留在桌面上。

## 当前能力

```text
透明无边框窗口
always on top
显示 Memo PNG 精灵
显示状态气泡
检查本地后端 /api/health
点击精灵或按钮打开 http://127.0.0.1:5173
```

## 暂不包含

```text
自动启动 FastAPI 后端
系统托盘
全局快捷键
跨窗口精灵事件同步
真正的 Live2D
系统级自动化能力
```

## 环境要求

```text
Node.js
npm
Rust 1.88.0
WebView2 Runtime
```

本目录包含 `rust-toolchain.toml`，只会让 `desktop/` 使用 Rust 1.88.0，不会修改全局默认 Rust。

如果下载 Rust 或 crates 较慢，可以在当前 shell 设置代理：

```powershell
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
$env:ALL_PROXY='socks5://127.0.0.1:7897'
```

## 启动

先启动 AiMemo 后端和前端：

```powershell
cd E:\Ai记
.\scripts\start-backend.ps1
.\scripts\start-frontend.ps1
```

再启动桌面 PoC：

```powershell
cd E:\Ai记\desktop
npm install
npm run dev
```

## 验证点

```text
精灵窗口是否透明
是否置顶
是否能拖拽
点击是否能打开 AiMemo 页面
后端未启动时气泡是否提示未连接
```
