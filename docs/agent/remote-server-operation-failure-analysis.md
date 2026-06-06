# 远程服务器操作失败复盘与改进说明

本文复盘 2026-06-06 一轮“让 Agent 帮忙给 Nginx 编写静态网页并传到远程服务器”的体验问题。

复盘目标不是追究某一次模型回答，而是定位系统性缺口：为什么 Agent 看起来执行了很多步骤，却没有给用户一个清晰、可验证的结果。

## 数据来源

本次复盘参考了本地运行数据：

```text
backend/data/ai_note.db
  conversation / chatmessage / chatturn
  agentoperation
  backgroundtask

backend/data/langgraph_checkpoints.db
  checkpoints
  writes
```

为避免泄露敏感信息，本文不记录真实 IP、账号、路径、口令或服务器标识。

## 现象摘要

用户目标逐步演进为：

```text
1. 根据教程在云服务器上安装 Nginx。
2. 遇到 yum GPG 校验失败后，希望调整安装命令。
3. 需要给 Nginx 编写一个静态网页。
4. 期望 Agent 登录 / 传输到远程服务器完成修改。
```

实际体验是：

```text
1. Agent 先在本地创建了静态页文件。
2. 尝试用 scp 传输到远程服务器。
3. 因 scp 需要交互式认证而超时。
4. 用户要求安装 sshpass。
5. Agent 尝试 choco / winget / 查找 sshpass.exe。
6. 最终消息进入 failed / cancelled 状态，没有完成上传，也没有给出清晰下一步。
```

用户看到的是“改了半天没有结论”，这和数据库里的执行轨迹一致。

## 关键证据

### 1. 工具失败密集出现

`agentoperation` 中该轮存在大量 `exec_command` 失败或阻塞：

```text
exec_command blocked: 7 次
exec_command failed: 7 次
exec_command completed: 4 次
search_files completed: 2 次
write_file completed: 1 次
```

典型失败包括：

```text
COMMAND_BLOCKED
  exec 不允许 shell 重定向 / 后台运算符。

COMMAND_EXITED_NON_ZERO
  sshpass 未安装或不在 PATH。

COMMAND_TIMEOUT
  scp 等待交互式密码 / 主机确认导致超时。
```

### 2. Graph 已经意识到失败，但没有转成有效决策

checkpoint 中出现：

```text
consecutive_failed_tools
tool_budget
thought_events
tool_observation_context
```

说明 graph 知道工具失败在累积，也记录了工具预算。但它的后续行为仍然是继续尝试不同命令，而不是稳定地进入“向用户结构化确认下一步”的状态。

### 3. 最终 assistant 消息状态异常

最后一轮用户要求：

```text
安装 sshpass 指令，然后再传。
```

对应 assistant 消息状态为 `failed`，chatturn 状态为 `cancelled`。消息内容里混杂了多个阶段判断：

```text
sshpass 在 choco 源中不存在。
系统已有 scp。
用户选择了从 GitHub 下载。
winget 找到了 sshpass-win32。
sshpass 安装成功。
路径未刷新，正在找 sshpass.exe。
```

这不是一个可执行结论，而是中间状态拼接。用户无法知道到底是：

```text
已经装好了？
没有装好？
需要重开 shell？
需要用完整路径？
已经传输了？
还没传？
```

### 4. 违反了工作区落地规则

用户说“我需要给 nginx 编写一个静态网页”时，没有明确让 Agent 在 AiMemo 仓库内创建文件。Agent 却先在本地仓库附近生成了静态页文件，再尝试上传。

这违反了项目规则的精神：

```text
没有明确目标路径时，不应默认在 AiMemo 仓库里落新文件。
应先确认：直接远程写入、在临时目录生成后上传，还是在某个用户指定目录保存。
```

这会让用户觉得 Agent 在“自顾自搭流程”，不是在稳定完成目标。

## 根因分析

### 根因 1：缺少远程操作任务模型

当前 Local Operator 更偏向本地文件和本地命令，远程服务器操作被模型临时拼成：

```text
write_file 生成本地文件
scp 上传
sshpass 处理密码
ssh / systemctl 验证
```

但系统没有一个专门的 Remote Operation 抽象来表达：

```text
远程主机
认证方式
目标路径
是否需要 sudo
上传方式
执行方式
验收命令
回滚方式
```

缺少这个抽象后，模型只能边试边猜。

### 根因 2：交互式命令和非交互工具边界不清

`scp` 在没有密钥、没有 sshpass、没有 BatchMode 设置时，会等待密码或 host key 确认。当前 `exec_command` 是非交互命令工具，无法处理这类交互。

表现为：

```text
scp 打出登录提示或 banner。
命令等待输入。
工具超时。
Agent 把超时当成普通命令失败继续重试。
```

正确行为应该是尽早判断：

```text
这是交互式认证问题，不是普通失败。
需要用户选择认证方案：SSH key / sshpass / 手动复制命令 / 提供一次性密码输入通道。
```

### 根因 3：命令策略阻断后没有形成恢复分支

多次命令被策略拦截：

```text
shell 重定向
后台运算符
疑似交互式命令
```

工具返回了明确错误，但 Agent 没有把这些错误归纳成“策略限制卡住任务”，也没有稳定调用 `request_user_input` 让用户选择恢复方案。

它继续尝试同类命令变体，导致用户感知为“纠结半天”。

### 根因 4：验证逻辑过弱

`winget install sshpass-win32` 返回 0 后，Agent 立即说“安装成功”，但随后 `sshpass -V` 失败，因为 PATH 未刷新或命令不可直接调用。

这里暴露两个问题：

```text
1. 工具 exit_code=0 不等于目标能力 ready。
2. 验证失败后，最终回答没有把状态降级为“安装包存在，但命令不可直接调用”。
```

正确状态应是：

```text
package_installed=true
command_in_path=false
resolved_exe_path=...
next_action=用完整路径调用或刷新 PATH
```

### 根因 5：错误输出编码污染模型判断

Windows 命令输出里出现了 mojibake，例如“不是内部或外部命令”的中文提示被记录成乱码。

这会降低模型对错误的理解质量，也会污染最终回答。

工具层应该尽量把常见 Windows 错误标准化为结构化原因：

```text
command_not_found
interactive_auth_required
path_not_refreshed
permission_or_elevation_required
package_not_found
```

### 根因 6：最终回答没有执行“任务闭环检查”

这轮的真实目标不是“安装 sshpass”，而是：

```text
远程 Nginx 静态网页已经可访问。
```

但 Agent 的中间目标不断漂移：

```text
先写本地文件
再 scp
再装 sshpass
再找 sshpass.exe
```

最终没有回到原始验收条件：

```text
文件是否已上传到远程 /usr/share/nginx/html/index.html？
Nginx 是否 reload / start？
curl 远程页面是否返回新内容？
```

## 应改进的产品体验

### 1. 远程操作前置确认

当用户要求“登录服务器修改代码 / 上传网页 / 部署服务”时，Agent 应先结构化确认：

```text
目标主机：
  已知服务器 / 新服务器 / 让用户输入

认证方式：
  SSH key
  密码 + sshpass
  用户手动执行命令

落地方式：
  直接远程写入
  本地临时生成后上传
  输出命令让用户复制执行

目标路径：
  Nginx 默认站点目录
  用户指定目录
```

只要缺少关键参数，就不应直接开始试命令。

### 2. 对交互式认证做显式识别

`scp` / `ssh` 出现以下情况时，应立即进入恢复分支：

```text
超时且 stderr/stdout 包含登录 banner。
提示 password。
提示 host authenticity。
提示 permission denied。
提示 pseudo-terminal / tty。
```

恢复分支应使用 `request_user_input`：

```text
当前 scp 等待交互式认证，非交互工具无法继续。你希望采用哪种方式？

1. 配置 SSH key
2. 使用 sshpass / 密码方式
3. 我输出命令，你在服务器或本地手动执行
```

### 3. 把“安装工具”改成后台任务或明确验证步骤

安装包管理器软件属于可能耗时且影响系统的操作。应当：

```text
1. 先说明将安装什么、来源是什么、为什么需要。
2. 用户确认后执行。
3. 安装成功后做 capability check。
4. 如果 PATH 未刷新，使用已发现的绝对路径，或提示重开终端。
```

不要把 `winget install` 的 exit_code=0 直接等同于“工具可用”。

### 4. 强化最终验收

远程部署类任务必须有验收清单：

```text
文件已生成。
文件已上传到远程目标路径。
远程文件内容 hash / 关键文本一致。
Nginx 配置测试通过。
Nginx reload / start 成功。
curl 或浏览器访问返回预期内容。
```

如果任何一步缺失，最终回答必须写清楚：

```text
已完成：
  ...

未完成：
  ...

卡住原因：
  ...

下一步需要用户选择：
  ...
```

### 5. 禁止把新产物默认写进 AiMemo 仓库

对于“写一个网页 / 脚本 / 配置并传到服务器”这类需求，如果用户没有指定本地工作路径：

```text
优先使用系统临时目录生成中间文件。
或者直接远程写入。
或者用 request_user_input 询问保存位置。
```

不应在 AiMemo 仓库根目录创建临时项目或产物。

## 工程改进建议

### Phase 1：低成本止血（已落地）

```text
1. 在 Memory Chat ReAct system prompt 中增加远程服务器操作规则。
2. 在 _infer_acceptance_criteria 中识别：
   服务器、远程、ssh、scp、nginx、部署、上传、静态网页。
3. 对这些任务加入验收条件：
   必须有远程写入 / 上传成功 observation 和远程验证 observation。
4. 如果远程工具超时，且输出含登录 banner / password / authenticity，
   classify 为 interactive_auth_required。
5. interactive_auth_required 必须触发 request_user_input，不允许继续盲试。
6. exec_command 拦截原始 scp/sftp/plink/pscp，引导 agent 使用 remote_* 工具。
```

本阶段对应代码入口：

```text
backend/app/local_operator/remote.py
backend/app/local_operator/tools.py
backend/app/local_operator/command.py
backend/app/agent/graphs/memory_chat/nodes.py
```

已提供的远程工具：

```text
remote_connectivity_check
remote_upload_file
remote_exec
remote_verify_http
```

### Phase 2：远程任务 Session（已落地）

在已有 remote_* 工具之上，引入 checkpoint 内的 `RemoteTaskSession`，而不是让模型只靠单次工具结果临时判断：

```text
collect_target
collect_auth
prepare_artifact
transfer
remote_apply
verify
done / blocked
```

Session 当前保存在 Memory Chat Graph state 中，随 LangGraph checkpoint 恢复，不额外新增数据库表：

```text
当前服务器是谁。
认证方式是什么。
目标路径是什么。
已完成哪些步骤。
下一步为什么卡住。
```

本阶段对应代码入口：

```text
backend/app/agent/graphs/memory_chat/state.py
  RemoteTaskSessionPayload
  RemoteTaskPhasePayload

backend/app/agent/graphs/memory_chat/nodes.py
  _empty_remote_task_session
  _remote_task_session_from_observations
  build_plan_task_node
  build_observe_tool_result_node
  build_verify_goal_node
```

关键行为：

```text
1. 用户目标包含远程 / 服务器 / ssh / scp / nginx / 部署 / 上传时，plan_task 创建 remote_task_session。
2. 每次 remote_* observation 返回后，observe_tool_result 更新阶段状态。
3. INTERACTIVE_AUTH_REQUIRED、LOCAL_SSH_NOT_FOUND、REMOTE_PATH_NOT_ABSOLUTE 等错误会把 session 置为 blocked。
4. blocked session 会进入 verification.status = needs_user_input，下一轮 agent 必须调用 request_user_input。
5. 成功上传 + HTTP 验证后，session.status = completed，agent 可以最终总结。
```

### Phase 3：前端远程任务体验

在前端为远程任务提供独立过程卡片：

```text
目标服务器
认证状态
上传 / 执行 / 验证步骤
当前错误与可选恢复方案
```

这样用户不需要从一串命令输出里猜“到底做到哪一步”。

## 推荐的下一版行为

面对同样用户输入：

```text
我需要给 nginx 编写一个静态网页。
```

Agent 应答不应直接写仓库文件，而应进入：

```text
我可以帮你做。需要确认两件事：
1. 静态页是直接写到远程 Nginx 默认目录，还是先在本地生成再上传？
2. 远程登录采用 SSH key、密码，还是你希望我只输出命令让你复制执行？
```

如果用户说：

```text
安装 sshpass，然后再传。
```

Agent 应执行：

```text
1. 检查系统是否已有可用 scp / ssh。
2. 安装 sshpass 前说明来源和影响。
3. 安装后验证：
   - sshpass.exe 是否存在。
   - 命令是否在 PATH。
   - 如果不在 PATH，使用绝对路径。
4. 上传前使用非交互参数，并设置合理超时。
5. 上传后远程 ls / cat / curl 验证。
6. 若认证仍失败，停止并结构化询问认证方案。
```

最终回答必须类似：

```text
已完成：
  - 本地页面已生成。
  - sshpass-win32 已安装，实际路径为 ...

未完成：
  - 尚未上传到远程服务器。

卡住原因：
  - scp 需要交互式认证，当前工具不能输入密码。

请选择：
  - 使用 SSH key
  - 提供一次性密码输入能力
  - 我给你一组手动命令
```

## 结论

这次体验差的根本原因不是单条命令失败，而是 Agent 缺少“远程服务器修改代码”的任务模型。

它把一个需要目标确认、认证确认、上传、远程执行和验收的远程变更任务，拆成了临时命令试错。工具层虽然记录了失败和 checkpoint，但这些信号没有被升级成明确的用户决策点，最终导致：

```text
执行很多。
状态混乱。
没有验收。
没有清晰结论。
```

改进重点应放在：

```text
远程操作前置确认
交互式认证识别
结构化恢复分支
远程操作专用工具
最终验收清单
```

这样 Agent 才能从“会执行命令”升级为“能可靠完成远程变更任务”。
