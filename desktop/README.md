# Memo Elf Desktop

这是 Memo Elf 的桌面外置精灵壳。它以透明置顶窗口停留在桌面上，连接 AiMemo 后端对话服务，并作为后续技能入口。

## 当前能力

```text
透明无边框窗口
always on top
显示 Memo PNG 精灵
显示状态气泡和对话气泡
点击角色打开简洁菜单
结构化选项卡选择（例如选择文件落地目录）
检查本地后端 /api/health
打开 AiMemo 统一入口 http://127.0.0.1:8000/app
与后端 Memory Chat Graph 对话
Linux 透明桌宠窗口兼容路径
```

## 暂不包含

```text
系统托盘
全局快捷键
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

推荐从仓库根目录一键启动后端、前端和桌面精灵：

```powershell
cd E:\Ai记
.\scripts\start-dev.ps1
```

Linux / macOS：

```bash
./scripts/start-dev.sh
```

如果只调试桌面壳，也可以先启动后端，再在 `desktop/` 下执行 `npm run dev`。

## 验证点

```text
精灵窗口是否透明
是否置顶
是否能拖拽
点击是否能打开 AiMemo 页面
后端未启动时气泡是否提示未连接
用户选择卡片是否显示在角色右侧且能恢复对话
```
