# 精灵事件中心

本文记录后端精灵事件中心的第一版实现。这个结构把“精灵要说什么、做什么表情”从浏览器本地状态里抽出来，改为由后端业务事件驱动。

## 定位

后端现在是精灵事件的权威来源：

```text
backend
  产生 chat / job / memory / graph 事件

AiMemo Web
  作为主应用页面
  默认不再渲染主精灵
  只保留精灵工坊入口

Memo Elf Desktop
  轮询后端精灵事件
  展示表情、动作、气泡和菜单
```

这样浏览器和桌面精灵是并行关系，而不是“浏览器控制精灵”。后端最接近 job、graph、chat stream 等真实状态变化，因此也更适合作为事件源。

## 数据结构

事件 schema 位于：

```text
backend/app/schemas/elf.py
```

核心字段：

```text
source
  事件来源：jobs / chat / memory / graph / workshop / system。

mood
  精灵情绪：idle / thinking / working / success / warning / error / talking。

motion
  可选动作：thinking / working / success / error 等。

message
  气泡文本。为空时可以只驱动表情或动作。

priority
  优先级。桌面端后续可用它决定是否打断当前气泡。

ttl_ms
  建议展示时长。

dedupe_key
  去重键，避免同一个 job 完成、页面刷新等场景重复播报。

metadata
  调试字段，例如 conversation_id、turn_id、job_id、job_type。
```

## 服务实现

服务位于：

```text
backend/app/services/elf_event_service.py
```

第一版使用进程内短事件队列：

```text
最多保留最近 200 条事件
使用自增 id 支持 after_id 增量读取
使用 dedupe_key 做短窗口去重
不落库
```

不落库是刻意选择：精灵气泡属于短生命周期 UI 信号，真正重要的业务状态仍由 jobs、chat_messages、chat_turns 等表保存。

## API

路由位于：

```text
backend/app/api/elf.py
```

接口：

```text
GET /api/elf/events?after_id=0&limit=50
  读取 after_id 之后的事件。

POST /api/elf/events
  发布事件。主要用于调试和未来桌面端回传用户交互。
```

## 当前事件来源

已接入：

```text
chat stream
  turn 创建时：精灵进入 thinking。
  首个 answer_delta 时：精灵进入 talking。
  done 时：提示本轮对话完成。
  error 时：提示对话失败。

job worker
  job completed 时：根据 job type 提示任务完成。
  job failed 时：提示后台任务失败。
```

后续建议接入：

```text
memory mutation
  记忆停用、恢复、删除、合并。

note mutation
  笔记创建、修改、删除进入后台处理。

graph debug
  用户主动打开 graph 面板时给精灵一个轻提示。
```

## 消费端

桌面端第一版位于：

```text
desktop/src/main.ts
```

它每秒轮询：

```text
GET http://127.0.0.1:8000/api/elf/events?after_id=<last_id>
```

收到事件后：

```text
更新气泡文本
按 ttl_ms 自动隐藏气泡
把 mood/motion 写入精灵按钮 data 属性，驱动 CSS 动画
```

Web 端默认关闭主精灵：

```text
VITE_ENABLE_WEB_ELF=false
```

如果需要调试浏览器内精灵，可以设置：

```text
VITE_ENABLE_WEB_ELF=true
```

## 后续演进

第一版使用 polling 是为了低风险跑通结构。后续可升级：

```text
SSE / WebSocket
  后端主动推送事件给桌面精灵和浏览器。

事件持久化
  只在确实需要跨进程恢复 UI 事件时考虑。

统一 runtime
  把 Web 精灵和桌面精灵的 priority / ttl / dedupe 策略抽成共享模块。
```
