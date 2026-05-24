# ReAct 重构后续：执行质量改进方案

> 写作时间：2026-05-24
> 适用版本：第一轮 ReAct 重构落地后（`graph.py` 现为 `agent ↔ tools` 双节点循环）
> 配套阅读：`docs/agent/claude-code-agent-lessons.md`、`docs/agent/tool-loop-agent-upgrade.md`

> 落地状态：P0 路径语义、P1 read-before-write、P2 prompt discipline、P3 exec 输出清洗已进入代码；P2 流式 UX 分层保留为下一步协议改造，避免牺牲普通最终回答的实时流式体验。

## 1. 背景

第一轮重构把 `plan_task / select_tool / check_tool_policy / run_*_tool / observe_tool_result / verify_goal` 等节点替换成了 Claude Code 同款的 `agent ↔ tools` 循环（`backend/app/agent/graphs/memory_chat/graph.py:80-109`），同时去掉了所有 `_should_try_*` / `_looks_like_*` 关键词路由，由模型的 `tool_calls` 字段决定是否继续调工具。新一轮端到端测试（"在 E:\demo 建 Rust 项目并打印 8 个随机数"）验证：

- 链路确实走了 `agent → tools → agent`，不再凭空伪造结果；
- 最终回答的 8 个随机数来自真实 `cargo run` 输出；
- 策略层成功拦截了 `exec_command` 里的 shell 重定向写文件。

但同一轮也暴露 5 个新问题，全部属于"agent 能做、做得不够聪明"。这份文档把每一项对照 Claude Code 的设计给出可落地的改造路线。

## 2. 5 个问题对照表

| # | 现象 | 根因 | Claude Code 对应设计 | 优先级 |
|---|------|------|----------------------|--------|
| 1 | 路径写到 `E:/Ai记/demo/...` 而不是 `E:\demo` | 工具接受相对路径 + 没显式告诉模型当前 cwd | 工具 schema 强制绝对路径 + 系统提示告知 cwd | **P0** |
| 2 | 删了旧 `random_numbers.rs` 之后 cargo run 报错 | 改 Cargo.toml 前没读现状，对项目状态盲改 | `FileWriteTool` / `FileEditTool` 的 read-before-write 运行时保护 | **P1** |
| 3 | OP 171→191 大量失败重试，路径太绕 | 模型没"诊断 root cause 再换策略"的硬约束 | 系统提示里的 "diagnose why before switching tactics" + TodoWrite 风格的轻量计划 | **P2** |
| 4 | Windows `dir` / cargo 中文输出乱码 | 子进程默认 GBK，模型读到的是 mojibake | Claude Code 统一 UTF-8 + `stripAnsi`（`QueryEngine.ts:20`） | **P3** |
| 5 | tool_call 前的过程文本流到了回答气泡 | mapper 仅在 chunk 自身带 tool_calls 时屏蔽；早期 text token 漏过 | content block 分离：文本 vs `tool_use` 在同一 AIMessage 内是两类块，UI 分层渲染 | **P2** |

## 3. 详细改造方案

### 3.1 P0 · 路径语义：绝对路径 + 当前 cwd 上下文

#### 现象回放
用户说 `E:\demo`，模型第一次调用 `write_file` 时给的是 `demo/Cargo.toml`。后端 `policy.from_roots()` 把它解析成相对当前 workspace（`e:/Ai记/`）的路径，于是写到了 `e:/Ai记/demo/Cargo.toml`。模型在后续轮次看了 `list_dir` 才意识到错位置，又重新走了一遍。

#### Claude Code 怎么做
1. **schema 层强制绝对路径**
   - `submodules/Claude-Code/src/tools/FileReadTool/prompt.ts:36`：
     > "The `file_path` parameter must be an absolute path, not a relative path."
   - `FileEditTool.ts` 在 prompt 和分析事件里都校验 `isAbsolute(file_path)`。
2. **运行时 `expandPath` 规范化**：避免 `..` / `~` 绕过 allowlist。
3. **系统提示告知工作目录**：Claude Code 的环境块明确写出 `Primary working directory: ...`、平台、shell，让模型不必猜。

#### 我们的改造
- `backend/app/local_operator/schemas.py:39-87`：
  - 在 `ListDirInput.path`、`ReadFileInput.path`、`WriteFileInput.path`、`SearchFilesInput.root`、`SearchTextInput.root`、`GetFileInfoInput.path`、`ExecCommandInput.cwd` 的 `Field(description=...)` 末尾追加：
    > "**必须是绝对路径**（Windows 例：`E:\\demo`；POSIX 例：`/home/user/demo`）。不要传相对路径。"
  - 可选：加一个 `field_validator` 在 schema 层就 reject 非绝对路径，给模型一个明确的 422 反馈，比让 policy 静默重定位更好。
- `backend/app/local_operator/tools.py`：在 `run_with_audit` 包装的 `action` 内部，若收到相对路径，应主动拼接到 `policy.workspace_root`（已经如此）**但是**把"我把 `xxx` 当作 `<root>/xxx` 处理"写进 ToolResult 的 `message`，让模型立刻意识到自己漏了绝对路径。
- `backend/app/agent/graphs/memory_chat/nodes.py:554` `_build_react_agent_system_prompt()`：
  - 新增一段 "环境上下文"，由 `_default_local_operator_workspace_roots()` 动态拼出：
    ```
    工作环境：
    - 当前 workspace 根：{roots[0]}
    - 平台：Windows，shell：powershell/cmd
    - 路径规则：所有工具的 path/root/cwd 必须是绝对路径。
      用户给的相对名（如 demo/）请先和用户当前意图的盘符/根目录拼成绝对路径再调用。
    ```
  - 把"用户提到 E:\\demo 时不要写到 e:/Ai记/demo"作为反例写进规则。

#### 收益
模型不必试错就知道："`E:\demo`" 是绝对路径起点；schema 描述本身就让 `tool_calls` 默认带绝对路径，少 2-4 次回路。

---

### 3.2 P1 · 项目状态：read-before-write 的运行时保护

#### 现象回放
模型为了"清理项目"，直接 `write_file` 覆盖 `Cargo.toml` 并把旧的 `random_numbers.rs` 删掉，但 Cargo.toml 里残留 `[[bin]] path="random_numbers.rs"`，导致 cargo run 找不到文件，又得回头修。本质是"在没真正了解当前状态的情况下做覆盖"。

#### Claude Code 怎么做
- `submodules/Claude-Code/src/tools/FileWriteTool/FileWriteTool.ts:195-225`：
  ```ts
  const readTimestamp = toolUseContext.readFileState.get(fullFilePath)
  if (!readTimestamp || readTimestamp.isPartialView) {
    return { result: false, message: 'File has not been read yet. Read it first before writing to it.', errorCode: 2 }
  }
  // 接着比对 mtime，若文件在我们 read 之后被改过则拒绝。
  ```
- `FileEditTool.ts:281` 同款。
- prompt 也写明：
  > "If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first."

#### 我们的改造
- `backend/app/local_operator/filesystem.py`（write_file 实现处）：
  1. `LocalFilesystemService` 内部维护 `read_timestamps: dict[str, (mtime, full_view_bool)]`，每次 `read_file` 成功就记一次。
  2. `write_file(path, ..., overwrite=True)` 且文件已存在时：
     - 如果 `path` 不在 `read_timestamps` 或对应 entry 是部分视图（带 `start_line/end_line` 的读法）→ 返回 `ToolResult(ok=False, error_code="WRITE_WITHOUT_READ", message="文件已存在但本会话未完整读取，请先 read_file 后再覆盖。")`。
     - 如果当前 `os.path.getmtime(path) > recorded_mtime` → 返回 `ToolResult(ok=False, error_code="FILE_MTIME_CHANGED", message="文件在你读取后被外部修改，请重新 read_file。")`。
  3. `known_existing_paths` 已经在 `create_read_tools(... known_existing_paths=...)` 流通（`tools.py:34, 202`），可以作为第一个 session 的 seed（例如初始 cargo init 写出来的文件）。
- `backend/app/local_operator/schemas.py:52-56` `WriteFileInput`：
  - description 追加："如果目标已存在，**必须先调用 `read_file`** 完整读过；策略层会拒绝未读过的覆盖。"
- `nodes.py:554` 系统提示中相关一行（"写入已有文件或覆盖文件时，遵守工具返回的 read-before-write 保护"）已存在，但把它从泛指改成显式："**写入或覆盖任何已存在的文件之前必须先 `read_file` 完整读过该文件**。"

#### 收益
模型不会再"凭印象改 Cargo.toml"。即便它一开始想，被 `WRITE_WITHOUT_READ` 顶回去后必然先读，状态判断就稳了。

---

### 3.3 P2 · 执行效率：减少绕路，靠 prompt 而非额外节点

#### 现象回放
OP 171 → 191 之间大量 "失败 → 重试 → 失败" 的循环，主因不是单点 bug，而是模型在出错时倾向于"换一种调法重试"而不是"读错误信息推因"。

#### Claude Code 怎么做
- `submodules/Claude-Code/src/constants/prompts.ts` 的 `# Doing tasks`、`# Using your tools`、`# Tone and style` 段都没有硬规则，但反复强调三件事：
  1. 出现障碍时，**找根因**再换策略，不要把破坏性 / `--no-verify` / 重试当捷径。
  2. **优先并行调用**：彼此独立的工具一次性发出去。
  3. 长任务（>3 步）用 TodoWrite 自维护进度，"mark completed IMMEDIATELY after finishing, don't batch"。
- TodoWrite 自身就是一个 in-memory tool（没有副作用），但因为它产生显式 plan，会改变后续 token 的 attention 分布——这是非常便宜的"理性提升"。

#### 我们的改造
**Phase A（先做，零代码新增）**——在 `_build_react_agent_system_prompt()` 增加：
```
工作纪律：
- 工具失败时，先完整阅读 ToolMessage 里的 error_code / message / stdout / stderr，
  推断根因再决定下一步；不要原样重试，也不要立刻换无关工具。
- 任务跨 3 步以上时，先用一两句话给出"计划"（步骤 1/2/3），后续每完成一步在回答里
  打勾推进。不要在中途新建步骤而不复述清单。
- 多个工具调用如果彼此独立（例如读 a.toml 和读 b.rs），尽量在同一轮 tool_calls
  里并行发出。
- 不要把破坏性操作（删除文件、`cargo clean`、覆盖 Cargo.toml）当作"先清理再说"的
  开局，应作为定位到具体问题之后的针对性修复。
```
**Phase B（可选）**——加一个轻量 `plan_todo` 工具（无副作用，只把列表回写到 state）：
- schema：`PlanTodoInput { steps: list[{content: str, status: Literal["pending","in_progress","done"]}] }`
- 实现：把 steps 写进 `state["agent_plan"]`，在 SSE 里以 `thought_snapshot` 推到前端右侧面板。
- 这样既不阻断 ReAct 循环，又给模型一个"显式 think aloud"的载体；和 Claude Code 的 TodoWrite 行为对齐。
- 风险：会多一次 tool round-trip。Phase A 跑两轮看看是否足够，再决定要不要做 B。

#### 收益
绝大多数效率问题靠 Phase A 就能压下来，Phase B 是锦上添花。

---

### 3.4 P3 · Windows 编码：exec_command 统一 UTF-8

#### 现象回放
`dir`、`cargo` 的中文输出在 Windows 控制台默认 GBK，进 Python `subprocess` 之后解 UTF-8 时变 mojibake，模型读不懂自己的命令输出，反过来又判错。

#### Claude Code 怎么做
- `submodules/Claude-Code/src/services/QueryEngine.ts:20` 用 `stripAnsi` 清理工具输出里的转义码。
- Bash 工具在 spawn 时强制 `LANG=C.UTF-8`/`PYTHONIOENCODING=UTF-8`（Unix），Windows shell tool 走 PowerShell + UTF-8 输出。

#### 我们的改造
- `backend/app/local_operator/command.py` 的 `LocalCommandExecutor.exec_command`：
  - Windows 分支：用 PowerShell 包装，加 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8;` 前缀；或者在子进程 env 里设置 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`；并把 subprocess 的 `encoding="utf-8", errors="replace"`。
  - 不要用 `chcp 65001` 这种"设置当前控制台代码页"的写法，它对子进程不可靠。
  - 输出文本后跑一遍 `re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)` 去 ANSI（等价 stripAnsi）。
- `ExecCommandInput.description`（`schemas.py:65`）追加："stdout/stderr 会以 UTF-8 返回，无需额外指定编码参数。"
- 单测：加一条 `test_exec_command_returns_utf8_on_windows`，跑 `cmd /c "echo 中文"` 应得到 `"中文"` 而不是 `"中文"` 的乱码字节。

#### 收益
模型读到的工具输出和用户在终端看到的一致，少一类完全"非智能"的失败。

---

### 3.5 P2 · 流式 UX：思考归思考、回答归回答

#### 现象回放
用户原话："agent 节点在工具调用前生成的一些过程性文字可能会被当作回答 token 流到前端。"

看一下当前 `backend/app/agent/streaming/mapper.py:86-87`：
```python
if node_name == visible_answer_node and _has_tool_call_chunk(token_chunk):
    return None
```
只在**当前 chunk 自身已经带 tool_calls 时**才屏蔽。但 LangChain 流式行为是先吐文本 delta，最后才在某个 chunk 里出现 `tool_calls`/`tool_call_chunks`——所以"我现在去调 list_dir 看一下..."这段会先被分类为 `answer_delta`。

#### Claude Code 怎么做
- 模型返回的 `Message.content` 是 `Array<TextBlock | ToolUseBlock | ...>`；UI 渲染时按 block 类型走不同样式（text → 气泡，tool_use → 折叠面板）。
- 关键洞察：**一条 AIMessage 要么是"中间思考 + 工具调用"，要么是"最终回答"，不会混着用。** Claude Code 的循环判定 `if (toolUseBlocks.length > 0) needsFollowUp = true`（`query.ts:830-836`）——只要这一轮有任何 tool_use，整条消息就是"过程"。

#### 我们的改造（两种思路，建议选 A）

**思路 A（缓冲 + 末尾判定，推荐）**
在 `_map_messages_chunk` 里：
1. 不直接对单个 token chunk 做 answer/internal 分类，而是按 `(run_id, node_name)` 缓存 token chunks，直到该 message 流式结束（`is_final` 标记或者下一个 message 开始）。
2. 流结束后判定：如果整条 message 带 `tool_calls` → 全部 chunk 重新发成 `internal_token`（或新的 `thought_token`）。否则发成 `answer_delta`。
3. 实现细节：前端需要能接受"先 thought 后 answer"两类事件，且 `thought_token` 进半透明思考面板。streaming events 里新增 `thought_token`，复用 `bubble_delta` 同款 schema 但带 `node: "agent_thought"`。

代价是"答案首 token 延迟"——但因为带 `tool_calls` 的 message 通常文本量很小（一两句"我去看一下目录结构"），缓冲到尾再下发并不会让用户觉得卡顿；真正的"最终回答 message"是不带 tool_calls 的，那一条还是按 chunk 实时流。

**思路 B（启发式拦截）**
保留按 chunk 推送，但用一个滑窗 buffer 监听 `tool_call_chunks` 是否在最近 N 个 chunk 内出现；出现就**追溯撤销**前面发出的 `answer_delta`，把它们改成 `thought_token`。
缺点：前端要支持"撤销"语义，体验差，不推荐。

#### 涉及文件
- `backend/app/agent/streaming/events.py`：定义 `thought_token` 事件类型。
- `backend/app/agent/streaming/mapper.py`：改成缓冲式（思路 A）。
- `backend/app/api/chat.py` 的 SSE 转发逻辑：把 `thought_token` 透传给前端。
- `frontend/src/features/chat/...`：右侧 thought panel 已支持 `thought_snapshot`，可以扩展接收实时 `thought_token` 拼字符串展示。

#### 收益
最终用户看到的聊天气泡只有"真正的回答"；中间的"我去看一眼目录"、"现在调 cargo run"自然落入思考面板。这一条改完，agent 多调几次工具用户也不会觉得"答案怎么写到一半又跑别的去了"。

---

## 4. 优先级与落地顺序

| 顺序 | 项目 | 预估投入 | 风险 |
|------|------|---------|------|
| 1 | 3.1 路径绝对化 + cwd 注入 | 0.5 天 | 低（改 schema description 和 system prompt 即可） |
| 2 | 3.5 流式分层（思路 A） | 1 天 | 中（涉及前后端协议） |
| 3 | 3.2 read-before-write 运行时保护 | 1 天 | 低（filesystem 层加内存表） |
| 4 | 3.3 Phase A：扩展系统提示 + 并行调用约束 | 0.5 天 | 低 |
| 5 | 3.4 Windows UTF-8 包装 | 0.5 天 | 低 |
| 6 | 3.3 Phase B：plan_todo 工具 | 1 天 | 低，但建议先观察 1-2 个 case 再决定要不要做 |

P0/P1 完成后再跑一遍同一个 "在 E:\demo 建 Rust 项目并打印 8 个随机数" 用例，预期：

- 工具调用总数从 21 次降到 6-8 次。
- 不再出现 `e:/Ai记/demo/...` 误写。
- 不再出现 `cargo run` 找不到 `random_numbers.rs`。
- 聊天气泡里只有最终的"项目已建好，运行结果是 [...]"，而非穿插过程描述。

## 5. 落地清单（按文件）

需要改：

- `backend/app/local_operator/schemas.py`
  - 已完成：所有路径字段 description 加"必须绝对路径"。
  - 已完成：`WriteFileInput.description` 强调覆盖前必须 `read_file`。
- `backend/app/local_operator/filesystem.py`
  - 已完成：新增 `KnownReadFile` 快照；`write_file` 走完整读取校验和 mtime 校验。
- `backend/app/local_operator/command.py`
  - 已完成：子进程环境设置 UTF-8，stdout/stderr 做 ANSI strip。
- `backend/app/local_operator/tools.py`
  - 已完成：支持从 graph observation 注入 `known_read_files`，让 read-before-write 跨 ReAct 轮次生效。
- `backend/app/agent/graphs/memory_chat/nodes.py`
  - 已完成：`_build_react_agent_system_prompt()` 注入工作目录 / 平台 / 路径规则 / 工作纪律。
- `backend/app/agent/streaming/events.py`
  - 待做：新增 `thought_token` 事件。
- `backend/app/agent/streaming/mapper.py`
  - 待做：改成能按完整 AIMessage 判定 answer vs thought。注意不能把普通最终回答整段缓冲到节点结束，否则会损失实时流式体验。
- `backend/app/api/chat.py`
  - 透传 `thought_token`。
- `frontend/src/features/chat/ChatGraphPanel.tsx` 或 thought panel 组件
  - 实时接收 `thought_token` 并拼接展示。

可选新增：

- `backend/app/local_operator/plan_tool.py` + `schemas.py` 的 `PlanTodoInput`：Phase B 才做。

不需要改：

- `backend/app/agent/graphs/memory_chat/graph.py`（拓扑已经是 ReAct 标准两节点循环，本轮不动）
- `backend/app/services/chat_turn_service.py` 的 `MEMORY_CHAT_NODE_ORDER`（节点列表已对齐重构后的命名）
- 前端 `ChatTurnGraphRead` 协议（`node_statuses` / `mermaid` 仍透明）

## 6. 风险与回滚

- 3.2 的 read-before-write 校验可能导致已有"模型直接 write 新文件"的用例报错。缓解：只对**已存在文件**生效，新建文件不要求；并把 `known_existing_paths` 在每个 conversation 启动时 seed 一次。
- 3.5 缓冲式 streaming 会让"最终回答"的首 token 延迟比现在多 ~100-300ms（取决于工具消息长度）。前端可以在等待第一个 `answer_delta` 期间显示 typing 指示，体验差距很小。
- 3.4 PowerShell 包装意味着子进程总会带一层 `pwsh -NoProfile -Command`；脚本里若依赖原生 cmd.exe 的语义（极少）需要白名单回退。

## 7. 与 Claude Code 的对照小结

| 改造点 | Claude Code 出处 |
|--------|-----------------|
| 工具 schema 强制绝对路径 | `src/tools/FileReadTool/prompt.ts:36`，`FileEditTool.ts` 中 `isAbsolute(file_path)` 分析 |
| 系统提示注入 cwd / 平台 | `src/constants/prompts.ts` 的环境信息段 |
| read-before-write 运行时校验 | `src/tools/FileWriteTool/FileWriteTool.ts:195-225`，`FileEditTool.ts:281` |
| 出错先找根因，不重试不捷径 | `src/constants/prompts.ts` `# Doing tasks` 段 |
| 多工具并行调用 | `src/constants/prompts.ts` `# Using your tools` 段 |
| TodoWrite 自维护计划 | `src/tools/TodoWriteTool/*` |
| ANSI / 编码清洗 | `src/services/QueryEngine.ts:20` `stripAnsi` |
| 文本 vs tool_use 分层渲染 | `src/utils/messages.ts`（content block 类型）+ UI render 分支 |
| 循环退出靠 tool_use 块计数 | `src/services/query.ts:830-836` |

---

**结论**：这一轮的问题已经不在"agent 框架"层面，而是在"工具产品 polish"层面——Claude Code 之所以好用，主要是 8 年踩坑沉淀出来的 tool prompt + 运行时校验 + UI 分层，而不是循环结构本身。沿着上面 P0 → P3 的顺序走，AiMemo 的 Local Operator 体验可以快速接近 Claude Code 的"看起来很聪明"的实际感受。
