import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

import "./styles.css";

const bubble = document.querySelector<HTMLDivElement>("#bubble");
const elf = document.querySelector<HTMLButtonElement>("#elf");
const elfImage = document.querySelector<HTMLImageElement>("#elf-image");
const elfMenu = document.querySelector<HTMLElement>("#elf-menu");
const openAppButton = document.querySelector<HTMLButtonElement>("#open-app");
const chatToggleButton = document.querySelector<HTMLButtonElement>("#chat-toggle");
const chatPanel = document.querySelector<HTMLFormElement>("#chat-panel");
const chatInput = document.querySelector<HTMLTextAreaElement>("#chat-input");
const chatSendButton = document.querySelector<HTMLButtonElement>("#chat-send");
const choicePanel = document.querySelector<HTMLFormElement>("#choice-panel");
const currentWindow = getCurrentWindow();
const ELF_EVENTS_URL = "http://127.0.0.1:8000/api/elf/events";
const ELF_CHAT_STREAM_URL = "http://127.0.0.1:8000/api/elf/chat/stream";
const ELF_CHAT_RESUME_STREAM_URL = (turnId: number) =>
  `http://127.0.0.1:8000/api/elf/chat/turns/${turnId}/resume/stream`;
const isLinuxElfWindow = navigator.userAgent.includes("Linux");

if (isLinuxElfWindow) {
  document.documentElement.classList.add("linux-elf-window");
}

let dragStart:
  | {
      x: number;
      y: number;
    }
  | null = null;
let isDragging = false;
let suppressClick = false;
let suppressClickTimer: number | null = null;
let lastElfEventId = 0;
let bubbleHideTimer: number | null = null;
let bubbleSequenceTimer: number | null = null;
let isElfChatRunning = false;
let activeToolCallIds = new Set<string>();
let toolProgressCount = 0;

elf?.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }

  dragStart = {
    x: event.clientX,
    y: event.clientY,
  };
  isDragging = false;
  elf.setPointerCapture(event.pointerId);
});

elf?.addEventListener("pointermove", async (event) => {
  if (!dragStart || isDragging) {
    return;
  }

  const deltaX = Math.abs(event.clientX - dragStart.x);
  const deltaY = Math.abs(event.clientY - dragStart.y);
  if (deltaX <= 5 && deltaY <= 5) {
    return;
  }

  isDragging = true;
  suppressNextClickBriefly();
  hideElfMenu();
  // 只有确认用户真的在拖动后才交给 Tauri，保留普通点击能力。
  await currentWindow.startDragging();
});

elf?.addEventListener("pointerup", endPointerInteraction);
elf?.addEventListener("pointercancel", endPointerInteraction);
elf?.addEventListener("lostpointercapture", endPointerInteraction);

elf?.addEventListener("click", () => {
  if (suppressClick) {
    suppressClick = false;
    return;
  }
  toggleElfMenu();
});

openAppButton?.addEventListener("click", () => {
  hideElfMenu();
  openAiMemo().catch(() => setBubble("打开 AiMemo 失败。"));
});

chatToggleButton?.addEventListener("click", () => {
  hideElfMenu();
  toggleChatPanel();
});

chatPanel?.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput?.value.trim() ?? "";
  if (!message || isElfChatRunning) {
    return;
  }
  if (chatInput) {
    chatInput.value = "";
    autoResizeChatInput();
  }
  streamElfChat(message).catch(() => {
    setBubble("刚才没连上对话服务。");
    clearBubbleAfter(4500);
  });
});

chatInput?.addEventListener("input", () => {
  autoResizeChatInput();
});

chatInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chatPanel?.requestSubmit();
  }
});

window.addEventListener("pointerdown", (event) => {
  if (!(event.target instanceof Node)) {
    return;
  }
  if (!elfMenu?.contains(event.target) && !elf?.contains(event.target) && !chatPanel?.contains(event.target)) {
    if (choicePanel?.contains(event.target)) {
      return;
    }
    hideElfMenu();
  }
});

checkBackendHealth().catch(() => {
  setBubble("后端还没连上。");
});
startElfEventPolling();

async function checkBackendHealth() {
  try {
    const isHealthy = await invoke<boolean>("check_backend_health");
    if (!isHealthy) {
      setBubble("后端还没准备好。");
      return;
    }
    setBubble("我在这里，点我打开 AiMemo。");
  } catch {
    setBubble("后端还没启动。");
  }
}

async function openAiMemo() {
  setBubble("我把 AiMemo 打开给你。");
  await invoke("open_aimemo");
}

function setBubble(message: string) {
  if (bubble) {
    bubble.textContent = message;
    bubble.classList.add("visible");
  }
}

function showChatBubble(part: ChatBubblePart, ttlMs = 5200) {
  setBubble(part.text);
  if (elf) {
    elf.dataset.mood = moodFromEmoji(part.emoji);
    elf.dataset.motion = motionFromEmoji(part.emoji);
  }
  setElfExpression(expressionFromEmoji(part.emoji));
  clearBubbleAfter(ttlMs, { resetExpression: true });
}

function clearBubbleAfter(ttlMs = 4000, options: { resetExpression?: boolean } = {}) {
  if (bubbleHideTimer !== null) {
    window.clearTimeout(bubbleHideTimer);
  }
  bubbleHideTimer = window.setTimeout(() => {
    bubble?.classList.remove("visible");
    if (options.resetExpression && !isElfChatRunning && bubbleSequenceTimer === null) {
      resetElfExpression();
    }
    bubbleHideTimer = null;
  }, ttlMs);
}

function toggleElfMenu() {
  if (!elfMenu) {
    return;
  }
  const isOpen = elfMenu.classList.toggle("open");
  elfMenu.setAttribute("aria-hidden", String(!isOpen));
}

function hideElfMenu() {
  if (!elfMenu) {
    return;
  }
  elfMenu.classList.remove("open");
  elfMenu.setAttribute("aria-hidden", "true");
}

function toggleChatPanel() {
  if (!chatPanel) {
    return;
  }
  const isOpen = chatPanel.classList.toggle("open");
  chatPanel.setAttribute("aria-hidden", String(!isOpen));
  if (isOpen) {
    window.setTimeout(() => {
      chatInput?.focus();
      autoResizeChatInput();
    }, 0);
  }
}

function hideChatPanel() {
  if (!chatPanel) {
    return;
  }
  chatPanel.classList.remove("open");
  chatPanel.setAttribute("aria-hidden", "true");
}

function hideChoicePanel() {
  if (!choicePanel) {
    return;
  }
  choicePanel.classList.remove("open");
  choicePanel.setAttribute("aria-hidden", "true");
  choicePanel.onsubmit = null;
  choicePanel.replaceChildren();
}

function endPointerInteraction() {
  dragStart = null;
  isDragging = false;
}

function suppressNextClickBriefly() {
  suppressClick = true;
  if (suppressClickTimer !== null) {
    window.clearTimeout(suppressClickTimer);
  }
  // Tauri 接管窗口拖拽后，WebView 不一定稳定收到 pointerup/click。
  // 所以点击抑制必须有时间兜底，避免一次拖拽后精灵菜单永远打不开。
  suppressClickTimer = window.setTimeout(() => {
    suppressClick = false;
    suppressClickTimer = null;
  }, 450);
}

async function startElfEventPolling() {
  window.setInterval(() => {
    pollElfEvents().catch(() => {
      // 轮询失败通常只是后端重启或未启动。这里保持安静，避免桌面精灵刷屏。
    });
  }, 1000);
}

async function pollElfEvents() {
  const response = await fetch(`${ELF_EVENTS_URL}?after_id=${lastElfEventId}&limit=20`);
  if (!response.ok) {
    return;
  }

  const payload = (await response.json()) as {
    events?: ElfEvent[];
    latest_id?: number;
  };
  const events = payload.events ?? [];
  for (const event of events) {
    lastElfEventId = Math.max(lastElfEventId, event.id);
    applyElfEvent(event);
  }
}

function applyElfEvent(event: ElfEvent) {
  if (isElfChatRunning) {
    return;
  }
  if (event.message) {
    setBubble(event.message);
    clearBubbleAfter(event.ttl_ms ?? 4000);
  }

  if (!elf) {
    return;
  }
  elf.dataset.mood = event.mood;
  if (event.motion) {
    elf.dataset.motion = event.motion;
  }
  setElfExpression(expressionFromMood(event.mood));
}

async function streamElfChat(message: string) {
  isElfChatRunning = true;
  activeToolCallIds = new Set<string>();
  toolProgressCount = 0;
  setChatInputEnabled(false);
  hideChatPanel();
  clearBubbleSequence();
  showChatBubble({ text: "嗯，我听着。", emoji: "thinking" }, 2600);

  try {
    const response = await fetch(ELF_CHAT_STREAM_URL, {
      body: JSON.stringify({ message }),
      headers: {
        "Content-Type": "application/json",
      },
      method: "POST",
    });

    if (!response.ok || !response.body) {
      throw new Error(`Elf chat failed with ${response.status}`);
    }

    const result = await readElfChatStream(response);

    // 回答流结束后稍等片刻再切气泡，避免用户看到气泡在 token 级别闪烁。
    window.setTimeout(() => {
      playBubbleSequence(result.bubbles.length > 0 ? result.bubbles : splitAnswerIntoBubbleParts(result.answer));
      isElfChatRunning = false;
      setChatInputEnabled(true);
      hideChoicePanel();
    }, 650);
  } catch (error) {
    isElfChatRunning = false;
    activeToolCallIds = new Set<string>();
    setChatInputEnabled(true);
    throw error;
  }
}

async function readElfChatStream(response: Response): Promise<ElfChatStreamResult> {
  let answer = "";
  let bubbles: ChatBubblePart[] = [];
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Elf chat stream is empty");
  }
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  async function handleEvent(event: SseEvent) {
    if (event.event === "answer_delta") {
      answer += String(event.data.content ?? "");
    }
    if (event.event === "done" && Array.isArray(event.data.bubbles)) {
      bubbles = normalizeGraphBubbles(event.data.bubbles);
    }
    if (event.event === "tool_invocation") {
      applyElfToolProgress(event.data);
    }
    if (event.event === "interrupt") {
      return resumeElfChoice(event.data as unknown as ElfInterruptEvent);
    }
    if (event.event === "error") {
      throw new Error(String(event.data.message ?? "Elf chat error"));
    }
    return null;
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const rawEvents = buffer.split("\n\n");
    buffer = rawEvents.pop() ?? "";
    for (const rawEvent of rawEvents) {
      const event = parseSseEvent(rawEvent);
      if (!event) {
        continue;
      }
      const resumed = await handleEvent(event);
      if (resumed) {
        return resumed;
      }
    }
  }

  if (buffer.trim()) {
    const event = parseSseEvent(buffer);
    if (event) {
      const resumed = await handleEvent(event);
      if (resumed) {
        return resumed;
      }
    }
  }
  return { answer, bubbles };
}

function applyElfToolProgress(data: Record<string, unknown>) {
  const toolName = String(data.tool_name ?? "");
  if (!toolName) {
    return;
  }
  const toolCallId = String(data.tool_call_id ?? `${toolName}-${toolProgressCount}`);
  const running = Boolean(data.running);
  if (running) {
    activeToolCallIds.add(toolCallId);
    toolProgressCount += 1;
    showChatBubble(
      {
        text: describeRunningTool(toolName, data.arguments),
        emoji: "working_focus",
      },
      5200,
    );
    return;
  }

  if (activeToolCallIds.has(toolCallId)) {
    activeToolCallIds.delete(toolCallId);
  }
  const ok = Boolean(data.ok);
  showChatBubble(
    {
      text: describeFinishedTool(toolName, data, ok),
      emoji: ok ? "thinking" : "error_worried",
    },
    ok ? 3600 : 6200,
  );
}

function describeRunningTool(toolName: string, rawArgs: unknown) {
  const args = isRecord(rawArgs) ? rawArgs : {};
  if (toolName === "search_files") {
    const pattern = String(args.pattern ?? "目标文件");
    const root = String(args.root ?? "");
    return root ? `我正在 ${root} 里找 ${pattern}，磁盘搜索可能要稍等一下。` : `我正在查找 ${pattern}。`;
  }
  if (toolName === "exec_command" || toolName === "exec_command_background") {
    return "我正在执行启动命令，先等它返回结果。";
  }
  if (toolName === "read_file") {
    return "我正在读取文件内容。";
  }
  if (toolName === "write_file") {
    return "我正在写入文件。";
  }
  return `我正在调用 ${toolName}。`;
}

function describeFinishedTool(toolName: string, data: Record<string, unknown>, ok: boolean) {
  if (!ok) {
    const message = String(data.message ?? data.error_code ?? "工具执行失败");
    return message.length > 64 ? `${message.slice(0, 64)}...` : message;
  }
  const summary = String(data.result_summary ?? "").trim();
  if (summary) {
    return summary.length > 72 ? `${summary.slice(0, 72)}...` : summary;
  }
  if (toolName === "search_files") {
    return "这一步搜索完成了，我继续判断下一步。";
  }
  if (toolName === "exec_command" || toolName === "exec_command_background") {
    return "命令已经执行完了，我看看结果。";
  }
  return "这一步工具执行完成了。";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

async function resumeElfChoice(event: ElfInterruptEvent): Promise<ElfChatStreamResult> {
  const request = normalizeUserInputRequest(event.request);
  showChatBubble({ text: request.question, emoji: "curious" }, 4200);
  const answer = await showChoicePanel(request);
  showChatBubble({ text: "收到，我继续处理。", emoji: "working_focus" }, 2200);
  const response = await fetch(ELF_CHAT_RESUME_STREAM_URL(Number(event.turn_id || 0)), {
    body: JSON.stringify(answer),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
  });
  if (!response.ok || !response.body) {
    throw new Error(`Elf chat resume failed with ${response.status}`);
  }
  return readElfChatStream(response);
}

function normalizeUserInputRequest(raw: unknown): UserInputRequest {
  const payload = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const options = Array.isArray(payload.options)
    ? payload.options
        .map((option, index): UserInputOption | null => {
          if (!option || typeof option !== "object") {
            return null;
          }
          const item = option as Record<string, unknown>;
          const label = String(item.label ?? item.value ?? "").trim();
          const value = String(item.value ?? label).trim();
          if (!label && !value) {
            return null;
          }
          return {
            id: String(item.id ?? `option-${index + 1}`),
            label: label || value,
            value: value || label,
            description: String(item.description ?? ""),
            recommended: Boolean(item.recommended ?? index === 0),
          };
        })
        .filter((option): option is UserInputOption => option !== null)
    : [];
  return {
    kind: "user_input",
    request_id: String(payload.request_id ?? ""),
    question: String(payload.question ?? "请补充一个具体选择。"),
    selection_mode: payload.selection_mode === "multiple" ? "multiple" : "single",
    options,
    allow_other: payload.allow_other !== false,
    other_option: {
      id: "other",
      label: "Other",
      value: "",
      description: "Custom answer",
      placeholder:
        typeof payload.other_option === "object" && payload.other_option !== null
          ? String((payload.other_option as Record<string, unknown>).placeholder ?? "Type another answer")
          : "Type another answer",
    },
  };
}

function showChoicePanel(request: UserInputRequest): Promise<UserInputAnswer> {
  return new Promise((resolve) => {
    if (!choicePanel) {
      resolve({
        request_id: request.request_id,
        selected_option_id: "",
        selected_option_ids: [],
        answer: "",
      });
      return;
    }

    hideElfMenu();
    hideChatPanel();
    choicePanel.replaceChildren();
    const mode = request.selection_mode === "multiple" ? "multiple" : "single";
    const selectedIds = new Set<string>();
    if (request.options[0]?.id) {
      selectedIds.add(request.options[0].id);
    }

    const title = document.createElement("div");
    title.className = "choice-panel-title";
    const question = document.createElement("strong");
    question.textContent = request.question;
    const hint = document.createElement("span");
    hint.textContent = mode === "multiple" ? "Multi-select" : "Single choice";
    title.append(question, hint);
    choicePanel.append(title);

    const list = document.createElement("div");
    list.className = "choice-options";
    choicePanel.append(list);

    const syncInputs = () => {
      list.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
        input.checked = selectedIds.has(input.value);
        input.closest("label")?.classList.toggle("selected", input.checked);
      });
    };

    function setSelected(id: string) {
      if (mode === "single") {
        selectedIds.clear();
        selectedIds.add(id);
      } else if (selectedIds.has(id)) {
        selectedIds.delete(id);
      } else {
        selectedIds.add(id);
      }
      syncInputs();
    }

    for (const option of request.options) {
      const label = document.createElement("label");
      label.className = "choice-option";
      const input = document.createElement("input");
      input.type = mode === "multiple" ? "checkbox" : "radio";
      input.name = `choice-${request.request_id}`;
      input.value = option.id;
      input.addEventListener("change", () => setSelected(option.id));
      const body = document.createElement("span");
      const main = document.createElement("span");
      main.textContent = option.label;
      if (option.recommended) {
        const badge = document.createElement("em");
        badge.textContent = "Recommended";
        main.append(badge);
      }
      body.append(main);
      if (option.description) {
        const description = document.createElement("small");
        description.textContent = option.description;
        body.append(description);
      }
      label.append(input, body);
      list.append(label);
    }

    let otherTextArea: HTMLTextAreaElement | null = null;
    if (request.allow_other) {
      const other = document.createElement("label");
      other.className = "choice-option choice-option-other";
      const input = document.createElement("input");
      input.type = mode === "multiple" ? "checkbox" : "radio";
      input.name = `choice-${request.request_id}`;
      input.value = "other";
      input.addEventListener("change", () => setSelected("other"));
      const body = document.createElement("span");
      const main = document.createElement("span");
      main.textContent = request.other_option.label || "Other";
      otherTextArea = document.createElement("textarea");
      otherTextArea.placeholder = request.other_option.placeholder || "Type another answer";
      otherTextArea.rows = 2;
      otherTextArea.addEventListener("focus", () => {
        if (!selectedIds.has("other")) {
          setSelected("other");
        }
      });
      body.append(main, otherTextArea);
      other.append(input, body);
      list.append(other);
    }

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "choice-submit";
    submit.textContent = "Submit";
    choicePanel.append(submit);
    choicePanel.classList.add("open");
    choicePanel.setAttribute("aria-hidden", "false");
    syncInputs();

    choicePanel.onsubmit = (event) => {
      event.preventDefault();
      const ids = Array.from(selectedIds);
      const otherText = otherTextArea?.value.trim() ?? "";
      if (otherText && !ids.includes("other")) {
        ids.push("other");
      }
      const values = request.options
        .filter((option) => ids.includes(option.id))
        .map((option) => option.value || option.label)
        .filter(Boolean);
      if (otherText) {
        values.push(otherText);
      }
      hideChoicePanel();
      resolve({
        request_id: request.request_id,
        selected_option_id: ids[0] ?? "",
        selected_option_ids: ids,
        answer: values.join("\n"),
        other_text: otherText,
      });
    };
  });
}

function normalizeGraphBubbles(rawBubbles: unknown[]): ChatBubblePart[] {
  return rawBubbles
    .map((rawBubble) => {
      if (!rawBubble || typeof rawBubble !== "object") {
        return null;
      }
      const payload = rawBubble as Record<string, unknown>;
      const text = String(payload.text ?? "").trim();
      if (!text) {
        return null;
      }
      return {
        text,
        emoji: normalizeEmoji(String(payload.emoji ?? "soft")),
      };
    })
    .filter((part): part is ChatBubblePart => part !== null);
}

function playBubbleSequence(parts: ChatBubblePart[]) {
  clearBubbleSequence();
  if (parts.length === 0) {
    showChatBubble({ text: "我刚才有点走神了，再说一次好吗？", emoji: "confused" }, 5200);
    return;
  }

  let index = 0;
  const showNext = () => {
    const part = parts[index];
    if (!part) {
      return;
    }
    const ttlMs = bubbleDurationMs(part.text);
    showChatBubble(part, ttlMs);
    index += 1;
    if (index < parts.length) {
      bubbleSequenceTimer = window.setTimeout(showNext, ttlMs + 420);
    } else {
      bubbleSequenceTimer = window.setTimeout(() => {
        bubbleSequenceTimer = null;
        resetElfExpression();
      }, ttlMs + 420);
    }
  };
  showNext();
}

function clearBubbleSequence() {
  if (bubbleSequenceTimer !== null) {
    window.clearTimeout(bubbleSequenceTimer);
    bubbleSequenceTimer = null;
  }
}

function splitAnswerIntoBubbleParts(answer: string): ChatBubblePart[] {
  const normalized = answer.replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const paragraphParts = normalized
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
  const semanticParts = paragraphParts.length > 1 ? paragraphParts : splitBySentence(normalized);

  return semanticParts.flatMap((part) => splitLongText(part, 96)).map((text) => ({
    text,
    emoji: inferEmoji(text),
  }));
}

function splitBySentence(text: string): string[] {
  const parts = text.match(/[^。！？!?；;]+[。！？!?；;]?/g) ?? [text];
  const merged: string[] = [];
  let current = "";
  for (const part of parts) {
    const next = `${current}${part}`.trim();
    if (next.length < 72) {
      current = next;
      continue;
    }
    if (current) {
      merged.push(current);
    }
    current = part.trim();
  }
  if (current) {
    merged.push(current);
  }
  return merged;
}

function splitLongText(text: string, maxLength: number): string[] {
  if (text.length <= maxLength) {
    return [text];
  }
  const result: string[] = [];
  for (let index = 0; index < text.length; index += maxLength) {
    result.push(text.slice(index, index + maxLength));
  }
  return result;
}

function inferEmoji(text: string): ElfBubbleEmoji {
  if (/无语|尴尬|愣住|不知道说什么/.test(text)) {
    return "speechless";
  }
  if (/傲娇|嘴硬|才不是|哼/.test(text)) {
    return "tsundere_pout";
  }
  if (/坏笑|偷笑|得逞|小算盘/.test(text)) {
    return "smug_grin";
  }
  if (/托腮|琢磨|沉思/.test(text)) {
    return "chin_thinking";
  }
  if (/星星眼|崇拜|闪闪发光|好厉害/.test(text)) {
    return "starry_eyes";
  }
  if (/慌了|糟糕|怎么办|来不及/.test(text)) {
    return "panicked";
  }
  if (/拜托|求你|可以嘛|お願い/.test(text)) {
    return "praying_please";
  }
  if (/惊讶|没想到|突然|居然|哇|诶|咦/.test(text)) {
    return "surprised";
  }
  if (/委屈|冤枉|被误解|想被安慰/.test(text)) {
    return "wronged_pout";
  }
  if (/难过|伤心|失落|低落|想哭/.test(text)) {
    return "sad_teary";
  }
  if (/[？?]/.test(text)) {
    return "curious";
  }
  if (/抱歉|失败|不能|没法|错误|担心|不安/.test(text)) {
    return "error_worried";
  }
  if (/生气|气鼓鼓|不满|吐槽|哼/.test(text)) {
    return "angry_pout";
  }
  if (/害羞|不好意思|脸红|被夸/.test(text)) {
    return "shy_blush";
  }
  if (/困|想睡|睡觉|疲惫/.test(text)) {
    return "sleepy";
  }
  if (/严肃|认真|重要|风险|必须|需要注意/.test(text)) {
    return "serious";
  }
  if (/鼓励|加油|可以的|支持你|别急|慢慢来/.test(text)) {
    return "encouraging";
  }
  if (/开心|很好|太好了|完成|喜欢|当然/.test(text)) {
    return "success_smile";
  }
  if (/记得|记忆|笔记|想起|回忆/.test(text)) {
    return "memory_glow";
  }
  if (/开玩笑|嘿嘿|逗你|调皮/.test(text)) {
    return "playful_wink";
  }
  if (/骄傲|得意|厉害吧/.test(text)) {
    return "proud";
  }
  if (/放松|安心|平静|舒服/.test(text)) {
    return "relaxed";
  }
  if (/处理|执行|读取|写入|正在|稍等/.test(text)) {
    return "working_focus";
  }
  return "idle_soft";
}

function normalizeEmoji(emoji: string): ElfBubbleEmoji {
  // 兼容旧版 graph/checkpoint 里可能残留的 soft/happy/worried/memory。
  const aliases: Record<string, ElfBubbleEmoji> = {
    soft: "idle_soft",
    happy: "success_smile",
    worried: "error_worried",
    memory: "memory_glow",
  };
  if (aliases[emoji]) {
    return aliases[emoji];
  }
  const allowed: ElfBubbleEmoji[] = [
    "idle_soft",
    "thinking",
    "working_focus",
    "success_smile",
    "error_worried",
    "sleepy",
    "curious",
    "memory_glow",
    "shy_blush",
    "angry_pout",
    "surprised",
    "sad_teary",
    "wronged_pout",
    "confused",
    "proud",
    "playful_wink",
    "serious",
    "relaxed",
    "encouraging",
    "speechless",
    "tsundere_pout",
    "smug_grin",
    "chin_thinking",
    "head_tilt_curious",
    "starry_eyes",
    "deadpan",
    "teasing_smile",
    "determined",
    "panicked",
    "comforting_soft",
    "praying_please",
    "tongue_out",
    "mouth_x",
    "dark_aura",
    "sparkle_success",
  ];
  return allowed.includes(emoji as ElfBubbleEmoji) ? (emoji as ElfBubbleEmoji) : "idle_soft";
}

function moodFromEmoji(emoji: ElfBubbleEmoji) {
  if (["error_worried", "sad_teary", "wronged_pout", "panicked", "dark_aura"].includes(emoji)) {
    return "error";
  }
  if (["curious", "thinking", "confused", "surprised", "speechless", "chin_thinking", "head_tilt_curious", "deadpan", "mouth_x"].includes(emoji)) {
    return "thinking";
  }
  if (["success_smile", "memory_glow", "proud", "encouraging", "playful_wink", "starry_eyes", "smug_grin", "teasing_smile", "comforting_soft", "sparkle_success"].includes(emoji)) {
    return "success";
  }
  if (emoji === "working_focus" || emoji === "serious" || emoji === "determined") {
    return "working";
  }
  return "talking";
}

function motionFromEmoji(emoji: ElfBubbleEmoji) {
  if (["error_worried", "sad_teary", "wronged_pout", "panicked", "dark_aura"].includes(emoji)) {
    return "error";
  }
  if (["curious", "thinking", "confused", "surprised", "speechless", "chin_thinking", "head_tilt_curious", "deadpan", "mouth_x"].includes(emoji)) {
    return "thinking";
  }
  if (["success_smile", "memory_glow", "proud", "encouraging", "playful_wink", "starry_eyes", "smug_grin", "teasing_smile", "comforting_soft", "sparkle_success"].includes(emoji)) {
    return "success";
  }
  if (emoji === "working_focus" || emoji === "serious" || emoji === "determined") {
    return "working";
  }
  return "nod";
}

function expressionFromEmoji(emoji: ElfBubbleEmoji): string {
  switch (emoji) {
    case "thinking":
      return "/elf/memo/02_thinking.png";
    case "working_focus":
      return "/elf/memo/03_working_focus.png";
    case "success_smile":
      return "/elf/memo/04_success_smile.png";
    case "error_worried":
      return "/elf/memo/05_error_worried.png";
    case "sleepy":
      return "/elf/memo/06_sleepy.png";
    case "curious":
      return "/elf/memo/07_curious.png";
    case "memory_glow":
      return "/elf/memo/08_memory_glow.png";
    case "shy_blush":
      return "/elf/memo/09_shy_blush.png";
    case "angry_pout":
      return "/elf/memo/10_angry_pout.png";
    case "surprised":
      return "/elf/memo/11_surprised.png";
    case "sad_teary":
      return "/elf/memo/12_sad_teary.png";
    case "wronged_pout":
      return "/elf/memo/13_wronged_pout.png";
    case "confused":
      return "/elf/memo/14_confused.png";
    case "proud":
      return "/elf/memo/15_proud.png";
    case "playful_wink":
      return "/elf/memo/16_playful_wink.png";
    case "serious":
      return "/elf/memo/17_serious.png";
    case "relaxed":
      return "/elf/memo/18_relaxed.png";
    case "encouraging":
      return "/elf/memo/19_encouraging.png";
    case "speechless":
      return "/elf/memo/20_speechless.png";
    case "tsundere_pout":
      return "/elf/memo/21_tsundere_pout.png";
    case "smug_grin":
      return "/elf/memo/22_smug_grin.png";
    case "chin_thinking":
      return "/elf/memo/23_chin_thinking.png";
    case "head_tilt_curious":
      return "/elf/memo/24_head_tilt_curious.png";
    case "starry_eyes":
      return "/elf/memo/25_starry_eyes.png";
    case "deadpan":
      return "/elf/memo/26_deadpan.png";
    case "teasing_smile":
      return "/elf/memo/27_teasing_smile.png";
    case "determined":
      return "/elf/memo/28_determined.png";
    case "panicked":
      return "/elf/memo/29_panicked.png";
    case "comforting_soft":
      return "/elf/memo/30_comforting_soft.png";
    case "praying_please":
      return "/elf/memo/31_praying_please.png";
    case "tongue_out":
      return "/elf/memo/32_tongue_out.png";
    case "mouth_x":
      return "/elf/memo/33_mouth_x.png";
    case "dark_aura":
      return "/elf/memo/34_dark_aura.png";
    case "sparkle_success":
      return "/elf/memo/35_sparkle_success.png";
    case "idle_soft":
    default:
      return "/elf/memo/01_idle_soft.png";
  }
}

function expressionFromMood(mood: string): string {
  switch (mood) {
    case "thinking":
      return "/elf/memo/02_thinking.png";
    case "working":
      return "/elf/memo/03_working_focus.png";
    case "success":
      return "/elf/memo/04_success_smile.png";
    case "warning":
    case "error":
      return "/elf/memo/05_error_worried.png";
    case "talking":
      return "/elf/memo/07_curious.png";
    case "idle":
    default:
      return "/elf/memo/01_idle_soft.png";
  }
}

function setElfExpression(src: string) {
  if (!elfImage || elfImage.getAttribute("src") === src) {
    return;
  }
  elfImage.src = src;
}

function resetElfExpression() {
  if (elf) {
    elf.dataset.mood = "idle";
    delete elf.dataset.motion;
  }
  setElfExpression("/elf/memo/01_idle_soft.png");
}

function bubbleDurationMs(text: string) {
  const normalizedLength = Array.from(text.trim()).length;
  // 气泡阅读时间按字数增长：短句不闪走，长句给足阅读时间，但避免长时间遮挡。
  return Math.min(12000, Math.max(3200, 1400 + normalizedLength * 115));
}

function setChatInputEnabled(enabled: boolean) {
  if (chatInput) {
    chatInput.disabled = !enabled;
  }
  if (chatSendButton) {
    chatSendButton.disabled = !enabled;
  }
}

function autoResizeChatInput() {
  if (!chatInput) {
    return;
  }
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 110)}px`;
}

function parseSseEvent(rawEvent: string): SseEvent | null {
  const lines = rawEvent.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) {
    return null;
  }
  return {
    event: eventLine.slice("event:".length).trim(),
    data: JSON.parse(dataLine.slice("data:".length).trim()),
  };
}

interface ElfEvent {
  id: number;
  source: string;
  mood: string;
  motion?: string | null;
  message?: string | null;
  priority: number;
  ttl_ms?: number | null;
  dedupe_key?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
}

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

interface ElfChatStreamResult {
  answer: string;
  bubbles: ChatBubblePart[];
}

interface ElfInterruptEvent {
  turn_id?: number | string;
  request?: unknown;
}

interface UserInputOption {
  id: string;
  label: string;
  value: string;
  description?: string;
  recommended?: boolean;
}

interface UserInputRequest {
  kind: "user_input";
  request_id: string;
  question: string;
  selection_mode: "single" | "multiple";
  options: UserInputOption[];
  allow_other: boolean;
  other_option: UserInputOption & { placeholder?: string };
}

interface UserInputAnswer {
  request_id: string;
  selected_option_id: string;
  selected_option_ids: string[];
  answer: string;
  other_text?: string;
}

type ElfBubbleEmoji =
  | "idle_soft"
  | "thinking"
  | "working_focus"
  | "success_smile"
  | "error_worried"
  | "sleepy"
  | "curious"
  | "memory_glow"
  | "shy_blush"
  | "angry_pout"
  | "surprised"
  | "sad_teary"
  | "wronged_pout"
  | "confused"
  | "proud"
  | "playful_wink"
  | "serious"
  | "relaxed"
  | "encouraging"
  | "speechless"
  | "tsundere_pout"
  | "smug_grin"
  | "chin_thinking"
  | "head_tilt_curious"
  | "starry_eyes"
  | "deadpan"
  | "teasing_smile"
  | "determined"
  | "panicked"
  | "comforting_soft"
  | "praying_please"
  | "tongue_out"
  | "mouth_x"
  | "dark_aura"
  | "sparkle_success";

interface ChatBubblePart {
  text: string;
  emoji: ElfBubbleEmoji;
}
