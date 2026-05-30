import { invoke } from "@tauri-apps/api/core";
import { LogicalSize } from "@tauri-apps/api/dpi";
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
const voiceHoldButton = document.querySelector<HTMLButtonElement>("#voice-hold");
const choicePanel = document.querySelector<HTMLFormElement>("#choice-panel");
const currentWindow = getCurrentWindow();
const AIMEMO_BACKEND_URL = import.meta.env.VITE_AIMEMO_BACKEND_URL ?? "http://127.0.0.1:8000";
const ELF_EVENTS_URL = `${AIMEMO_BACKEND_URL}/api/elf/events`;
const ELF_CHAT_STREAM_URL = `${AIMEMO_BACKEND_URL}/api/elf/chat/stream`;
const ELF_VOICE_MODE_URL = `${AIMEMO_BACKEND_URL}/api/elf/voice/mode`;
const ELF_VOICE_SPEAK_URL = `${AIMEMO_BACKEND_URL}/api/elf/voice/speak`;
const ELF_VOICE_TRANSCRIBE_URL = `${AIMEMO_BACKEND_URL}/api/elf/voice/transcribe`;
const RUNTIME_CONFIG_URL = `${AIMEMO_BACKEND_URL}/api/config/runtime`;
const ELF_CHAT_RESUME_STREAM_URL = (turnId: number) =>
  `${AIMEMO_BACKEND_URL}/api/elf/chat/turns/${turnId}/resume/stream`;
const isLinuxElfWindow = navigator.userAgent.includes("Linux");
const ELF_WINDOW_HEIGHT = 420;
const ELF_COMPACT_WINDOW_WIDTH = 300;
const ELF_CHOICE_WINDOW_WIDTH = 560;
const ELF_VOICE_REQUEST_TIMEOUT_MS = 45_000;
const ELF_CONFIG_RETRY_MS = 1500;

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
let isElfSpeaking = false;
let isElfVoiceModeEnabled = false;
let isVoiceRecording = false;
let mediaRecorder: MediaRecorder | null = null;
let recordedAudioChunks: Blob[] = [];
let activeToolCallIds = new Set<string>();
let toolProgressCount = 0;
let choiceCloseTimer: number | null = null;
let elfWindowMode: "compact" | "choice" | null = null;
let voiceQueue: QueuedVoiceBubble[] = [];
let activeVoiceAudio: HTMLAudioElement | null = null;
let activeVoiceUrl: string | null = null;
let voicePlaybackGeneration = 0;
let streamingBubbleJsonBuffer = "";
let streamedBubbleCount = 0;
let queuedVoiceBubbleTextKeys = new Set<string>();

void setElfWindowMode("compact");

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
  if (!message || isElfBusy()) {
    return;
  }
  if (chatInput) {
    chatInput.value = "";
    autoResizeChatInput();
  }
  streamElfChat(message, { voiceReply: isElfVoiceModeEnabled }).catch((error) => {
    console.error("[memo-elf] chat stream failed", error);
    setBubble(formatElfErrorBubble(error, "刚才没连上对话服务。"));
    clearBubbleAfter(4500);
  });
});

voiceHoldButton?.addEventListener("pointerdown", (event) => {
  if (!isElfVoiceModeEnabled || isElfBusy() || isVoiceRecording) {
    return;
  }
  event.preventDefault();
  voiceHoldButton.setPointerCapture(event.pointerId);
  startVoiceRecording().catch((error) => {
    console.error("[memo-elf] microphone failed", error);
    setBubble("麦克风暂时没连上，请检查浏览器/系统录音权限。");
    clearBubbleAfter(4200);
  });
});

voiceHoldButton?.addEventListener("pointerup", (event) => {
  if (!isVoiceRecording) {
    return;
  }
  event.preventDefault();
  stopVoiceRecording();
});

voiceHoldButton?.addEventListener("pointercancel", stopVoiceRecording);
voiceHoldButton?.addEventListener("lostpointercapture", stopVoiceRecording);

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

void bootstrapElfDesktop();

async function bootstrapElfDesktop() {
  const enabled = await waitForElfEnabled();
  if (enabled === false) {
    await currentWindow.hide().catch(() => {});
    return;
  }

  await currentWindow.show().catch(() => {});
  checkBackendHealth().catch(() => {
    setBubble("后端还没连上。");
    clearBubbleAfter(3200);
  });
  startElfEventPolling();
  startElfVoiceModePolling();
}

async function waitForElfEnabled(): Promise<boolean> {
  while (true) {
    const enabled = await fetchElfEnabled();
    if (enabled !== null) {
      return enabled;
    }
    await sleep(ELF_CONFIG_RETRY_MS);
  }
}

async function fetchElfEnabled(): Promise<boolean | null> {
  try {
    const response = await fetch(RUNTIME_CONFIG_URL, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as { elf?: { enabled?: boolean } };
    return payload.elf?.enabled === true;
  } catch {
    return null;
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function checkBackendHealth() {
  try {
    const isHealthy = await invoke<boolean>("check_backend_health");
    if (!isHealthy) {
      setBubble("后端还没准备好。");
      clearBubbleAfter(3600);
      return;
    }
    setBubble("我在这里，点我打开 AiMemo。");
    clearBubbleAfter(3200);
  } catch {
    setBubble("后端还没启动。");
    clearBubbleAfter(3600);
  }
}

async function openAiMemo() {
  setBubble("我把 AiMemo 打开给你。");
  clearBubbleAfter(2400);
  await invoke("open_aimemo");
}

function setBubble(message: string) {
  if (bubble) {
    bubble.textContent = message;
    bubble.classList.add("visible");
  }
}

class ElfClientError extends Error {
  code: string;
  status?: number;

  constructor(code: string, message: string, status?: number) {
    super(message);
    this.name = "ElfClientError";
    this.code = code;
    this.status = status;
  }
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs = ELF_VOICE_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ElfClientError("REQUEST_TIMEOUT", `请求超过 ${Math.round(timeoutMs / 1000)} 秒未返回。`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function responseError(response: Response, fallbackCode: string) {
  const payload = await readErrorPayload(response);
  const detail = payload && isRecord(payload.detail) ? (payload.detail as Record<string, unknown>) : payload;
  const code = String((detail && detail.code) ?? fallbackCode);
  const message = String((detail && detail.message) ?? response.statusText ?? "请求失败");
  return new ElfClientError(code, message, response.status);
}

async function readErrorPayload(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const text = await response.text();
    if (!text.trim()) {
      return null;
    }
    const parsed = JSON.parse(text) as unknown;
    return isRecord(parsed) ? parsed : { message: text };
  } catch {
    return null;
  }
}

function formatElfErrorBubble(error: unknown, fallback: string) {
  if (error instanceof ElfClientError) {
    if (error.code === "REQUEST_TIMEOUT") {
      return `语音/对话接口超时：${error.message}`;
    }
    if (error.code.includes("ASR") || error.code.includes("TRANSCRIBE")) {
      return `语音识别失败：${compactErrorMessage(error.message)}`;
    }
    if (error.code.includes("TTS") || error.code.includes("SPEAK") || error.code.includes("VOICE_PROFILE")) {
      return `语音播放失败：${compactErrorMessage(error.message)}`;
    }
    if (error.code.includes("DASHSCOPE") || error.code.includes("VOICE")) {
      return `语音接口失败：${compactErrorMessage(error.message)}`;
    }
    return `${fallback}（${error.status ?? "?"} ${error.code}）`;
  }
  if (error instanceof Error && error.message) {
    return `${fallback}（${compactErrorMessage(error.message)}）`;
  }
  return fallback;
}

function compactErrorMessage(message: string) {
  const normalized = message.replace(/\s+/g, " ").trim();
  return normalized.length > 96 ? `${normalized.slice(0, 96)}...` : normalized;
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

function showVoiceBubble(part: ChatBubblePart) {
  if (bubbleHideTimer !== null) {
    window.clearTimeout(bubbleHideTimer);
    bubbleHideTimer = null;
  }
  setBubble(part.text);
  if (elf) {
    elf.dataset.mood = moodFromEmoji(part.emoji);
    elf.dataset.motion = motionFromEmoji(part.emoji);
  }
  setElfExpression(expressionFromEmoji(part.emoji));
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
  if (choiceCloseTimer !== null) {
    window.clearTimeout(choiceCloseTimer);
    choiceCloseTimer = null;
  }
  choicePanel.classList.remove("open");
  choicePanel.classList.remove("closing");
  choicePanel.setAttribute("aria-hidden", "true");
  choicePanel.onsubmit = null;
  choicePanel.replaceChildren();
  void setElfWindowMode("compact");
}

function closeChoicePanelWithAnimation(onClosed: () => void) {
  if (!choicePanel) {
    void setElfWindowMode("compact");
    onClosed();
    return;
  }
  if (choiceCloseTimer !== null) {
    window.clearTimeout(choiceCloseTimer);
  }
  choicePanel.classList.remove("open");
  choicePanel.classList.add("closing");
  choicePanel.setAttribute("aria-hidden", "true");
  choicePanel.onsubmit = null;
  choiceCloseTimer = window.setTimeout(() => {
    choicePanel.classList.remove("closing");
    choicePanel.replaceChildren();
    choiceCloseTimer = null;
    void setElfWindowMode("compact");
    onClosed();
  }, 190);
}

async function setElfWindowMode(mode: "compact" | "choice") {
  if (isLinuxElfWindow || elfWindowMode === mode) {
    return;
  }
  elfWindowMode = mode;
  const width = mode === "choice" ? ELF_CHOICE_WINDOW_WIDTH : ELF_COMPACT_WINDOW_WIDTH;
  try {
    await currentWindow.setSize(new LogicalSize(width, ELF_WINDOW_HEIGHT));
  } catch {
    // Older desktop permission/config states may reject resizing; the elf still remains usable.
  }
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

async function startElfVoiceModePolling() {
  await pollElfVoiceMode().catch(() => {
    setElfVoiceMode(false);
  });
  window.setInterval(() => {
    pollElfVoiceMode().catch(() => {
      // 模式轮询失败时保持上一次状态，不刷屏。
    });
  }, 2500);
}

async function pollElfVoiceMode() {
  const response = await fetch(ELF_VOICE_MODE_URL);
  if (!response.ok) {
    return;
  }
  const payload = (await response.json()) as { enabled?: boolean };
  setElfVoiceMode(Boolean(payload.enabled));
}

function setElfVoiceMode(enabled: boolean) {
  isElfVoiceModeEnabled = enabled;
  chatPanel?.classList.toggle("voice-mode", enabled);
  if (voiceHoldButton) {
    voiceHoldButton.hidden = !enabled;
    voiceHoldButton.disabled = isElfBusy() || !enabled;
    voiceHoldButton.textContent = isVoiceRecording ? "松开发送" : "按住说话";
  }
}

async function startVoiceRecording() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    throw new Error("MediaRecorder is not available.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  recordedAudioChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) {
      recordedAudioChunks.push(event.data);
    }
  };
  mediaRecorder.onstop = () => {
    for (const track of stream.getTracks()) {
      track.stop();
    }
    const chunks = recordedAudioChunks;
    recordedAudioChunks = [];
    void transcribeAndSendVoice(chunks);
  };
  isVoiceRecording = true;
  voiceHoldButton?.classList.add("recording");
  if (voiceHoldButton) {
    voiceHoldButton.textContent = "松开发送";
  }
  setBubble("我在听，松开就发给我。");
  mediaRecorder.start();
}

function stopVoiceRecording() {
  if (!isVoiceRecording) {
    return;
  }
  isVoiceRecording = false;
  voiceHoldButton?.classList.remove("recording");
  if (voiceHoldButton) {
    voiceHoldButton.textContent = "正在识别...";
  }
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
}

async function transcribeAndSendVoice(chunks: Blob[]) {
  try {
    if (!chunks.length) {
      throw new Error("No audio recorded.");
    }
    const audioBlob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
    const form = new FormData();
    form.append("file", audioBlob, "elf-voice.webm");
    const response = await fetchWithTimeout(ELF_VOICE_TRANSCRIBE_URL, {
      body: form,
      method: "POST",
    });
    if (!response.ok) {
      throw await responseError(response, "VOICE_TRANSCRIBE_FAILED");
    }
    const payload = (await response.json()) as { text?: string };
    const text = String(payload.text ?? "").trim();
    if (!text) {
      throw new ElfClientError("VOICE_TRANSCRIBE_EMPTY", "语音接口返回了空文本，可能是声音太短或环境噪声太大。");
    }
    if (chatInput) {
      chatInput.value = text;
      autoResizeChatInput();
    }
    setBubble(`我听到：${text}`);
    await streamElfChat(text, { voiceReply: true });
  } catch (error) {
    console.error("[memo-elf] voice turn failed", error);
    setBubble(formatElfErrorBubble(error, "这段语音我没听清，再按住说一次吧。"));
    clearBubbleAfter(6200);
  } finally {
    mediaRecorder = null;
    if (voiceHoldButton) {
      voiceHoldButton.textContent = "按住说话";
    }
  }
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
  if (isElfBusy()) {
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

async function streamElfChat(message: string, options: { voiceReply: boolean } = { voiceReply: false }) {
  isElfChatRunning = true;
  isElfSpeaking = false;
  activeToolCallIds = new Set<string>();
  toolProgressCount = 0;
  streamingBubbleJsonBuffer = "";
  streamedBubbleCount = 0;
  queuedVoiceBubbleTextKeys = new Set<string>();
  setChatInputEnabled(false);
  hideChatPanel();
  clearBubbleSequence();
  clearVoicePlayback();
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
      throw await responseError(response, "ELF_CHAT_STREAM_FAILED");
    }

    const result = await readElfChatStream(response, { voiceReply: options.voiceReply });

    // 回答流结束后稍等片刻再切气泡，避免用户看到气泡在 token 级别闪烁。
    const finalParts = result.bubbles.length > 0 ? result.bubbles : splitAnswerIntoBubbleParts(result.answer);
    window.setTimeout(() => {
      isElfChatRunning = false;
      if (!options.voiceReply) {
        void playBubbleSequence(finalParts, { voiceReply: options.voiceReply });
        return;
      }
      if (queuedVoiceBubbleTextKeys.size === 0) {
        void playBubbleSequence(finalParts, { voiceReply: true });
        return;
      }
      if (!isElfSpeaking && voiceQueue.length === 0) {
        finishElfTurn();
      }
    }, 650);
  } catch (error) {
    isElfChatRunning = false;
    activeToolCallIds = new Set<string>();
    clearVoicePlayback();
    setChatInputEnabled(true);
    throw error;
  }
}

async function readElfChatStream(
  response: Response,
  options: { voiceReply: boolean } = { voiceReply: false },
): Promise<ElfChatStreamResult> {
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
    if (event.event === "bubble_delta" && options.voiceReply) {
      enqueueCompleteStreamingBubbles(String(event.data.content ?? ""), { voiceReply: options.voiceReply });
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
  if (isElfSpeaking) {
    return;
  }
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
  showChatBubble({ text: request.question, emoji: "curious" }, 90000);
  const answer = await showChoicePanel(request);
  showChatBubble({ text: choiceAckText(answer), emoji: "working_focus" }, 2400);
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
  return readElfChatStream(response, { voiceReply: isElfVoiceModeEnabled });
}

function choiceAckText(answer: UserInputAnswer) {
  const target = answer.other_text?.trim() || answer.answer.trim();
  if (!target) {
    return "收到，我继续处理。";
  }
  const compact = target.length > 28 ? `${target.slice(0, 28)}...` : target;
  return `好，就按「${compact}」来。`;
}

function normalizeUserInputRequest(raw: unknown): UserInputRequest {
  const payload = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const options = normalizeUserInputOptions(payload.options, "option");
  const questions = Array.isArray(payload.questions)
    ? payload.questions
        .map((rawQuestion, index): UserInputQuestion | null => {
          if (!rawQuestion || typeof rawQuestion !== "object") {
            return null;
          }
          const item = rawQuestion as Record<string, unknown>;
          const question = String(item.question ?? "").trim();
          const nestedOptions = normalizeUserInputOptions(item.options, `question-${index + 1}-option`);
          if (!question || nestedOptions.length < 2) {
            return null;
          }
          return {
            id: String(item.id ?? `question-${index + 1}`),
            question,
            selection_mode: item.selection_mode === "multiple" ? "multiple" : "single",
            options: nestedOptions,
            allow_other: item.allow_other !== false,
            other_placeholder: String(item.other_placeholder ?? "请输入其他答案"),
          };
        })
        .filter((question): question is UserInputQuestion => question !== null)
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
    questions,
  };
}

function normalizeUserInputOptions(rawOptions: unknown, idPrefix: string): UserInputOption[] {
  if (!Array.isArray(rawOptions)) {
    return [];
  }
  return rawOptions
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
        id: String(item.id ?? `${idPrefix}-${index + 1}`),
        label: label || value,
        value: value || label,
        description: String(item.description ?? ""),
        recommended: Boolean(item.recommended ?? index === 0),
      };
    })
    .filter((option): option is UserInputOption => option !== null);
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
    void setElfWindowMode("choice");
    if (choiceCloseTimer !== null) {
      window.clearTimeout(choiceCloseTimer);
      choiceCloseTimer = null;
    }
    choicePanel.classList.remove("closing");
    choicePanel.replaceChildren();
    const questions = request.questions?.length
      ? request.questions
      : [
          {
            id: `${request.request_id}-question-1`,
            question: request.question,
            selection_mode: request.selection_mode,
            options: request.options,
            allow_other: request.allow_other,
            other_placeholder: request.other_option.placeholder || "在这里补充你的答案",
          },
        ];
    const answers = new Map<string, NonNullable<UserInputAnswer["question_answers"]>[number]>();
    let activeIndex = 0;

    const header = document.createElement("div");
    header.className = "choice-panel-title";
    const title = document.createElement("strong");
    const subtitle = document.createElement("span");
    header.append(title, subtitle);
    const list = document.createElement("div");
    list.className = "choice-options";
    const actions = document.createElement("div");
    actions.className = "choice-actions";
    choicePanel.append(header);
    choicePanel.append(list);
    choicePanel.append(actions);

    const syncInputs = (selectedIds: Set<string>) => {
      list.querySelectorAll<HTMLInputElement>('input[type="radio"], input[type="checkbox"]').forEach((input) => {
        input.checked = selectedIds.has(input.value);
        input.closest("label")?.classList.toggle("selected", input.checked);
      });
    };

    function renderQuestion() {
      const question = questions[activeIndex] ?? questions[0];
      const stored = answers.get(question.id);
      const mode = question.selection_mode === "multiple" ? "multiple" : "single";
      const selectedIds = new Set<string>(stored?.selected_option_ids ?? []);
      if (!selectedIds.size && question.options[0]?.id) {
        selectedIds.add(question.options[0].id);
      }
      title.textContent = question.question;
      subtitle.textContent = questions.length > 1 ? `${activeIndex + 1}/${questions.length}` : "";
      list.replaceChildren();
      actions.replaceChildren();

      function saveCurrentAnswer(otherText = "") {
        const ids = Array.from(selectedIds);
        const selectedOptions = question.options.filter((option) => ids.includes(option.id));
        const values = selectedOptions.map((option) => option.value || option.label).filter(Boolean);
        const trimmedOther = otherText.trim();
        if (trimmedOther) {
          values.push(trimmedOther);
        }
        answers.set(question.id, {
          question_id: question.id,
          question: question.question,
          selected_option_id: ids[0] ?? "other",
          selected_option_ids: ids.length ? ids : ["other"],
          selected_option_labels: selectedOptions
            .map((option) => option.label)
            .concat(ids.includes("other") ? ["其他"] : []),
          answer: values.join("\n"),
          other_text: otherText,
          is_other: ids.includes("other"),
        });
        updateActions();
      }

      function setSelected(id: string, otherText = "") {
      if (mode === "single") {
        selectedIds.clear();
        selectedIds.add(id);
      } else if (selectedIds.has(id)) {
        selectedIds.delete(id);
      } else {
        selectedIds.add(id);
      }
        syncInputs(selectedIds);
        saveCurrentAnswer(otherText);
      }

      for (const option of question.options) {
      const label = document.createElement("label");
      label.className = "choice-option";
      const input = document.createElement("input");
      input.type = mode === "multiple" ? "checkbox" : "radio";
        input.name = `choice-${request.request_id}-${question.id}`;
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

      let otherInput: HTMLInputElement | null = null;
      if (question.allow_other) {
      const other = document.createElement("label");
      other.className = "choice-option choice-option-other";
      const input = document.createElement("input");
      input.type = mode === "multiple" ? "checkbox" : "radio";
        input.name = `choice-${request.request_id}-${question.id}`;
      input.value = "other";
        input.addEventListener("change", () => setSelected("other", otherInput?.value ?? ""));
      const body = document.createElement("span");
      const main = document.createElement("span");
      main.textContent = "其他";
      otherInput = document.createElement("input");
      otherInput.type = "text";
        otherInput.placeholder = question.other_placeholder || "在这里补充你的答案";
        otherInput.value = stored?.other_text ?? "";
      otherInput.addEventListener("focus", () => {
        if (!selectedIds.has("other")) {
            setSelected("other", otherInput?.value ?? "");
        }
      });
        otherInput.addEventListener("input", () => {
          selectedIds.add("other");
          syncInputs(selectedIds);
          saveCurrentAnswer(otherInput?.value ?? "");
        });
      body.append(main, otherInput);
      other.append(input, body);
      list.append(other);
    }

      function updateActions() {
        actions.replaceChildren();
        if (activeIndex > 0) {
          const back = document.createElement("button");
          back.type = "button";
          back.className = "choice-submit choice-submit--secondary";
          back.textContent = "上一题";
          back.addEventListener("click", () => {
            activeIndex -= 1;
            renderQuestion();
          });
          actions.append(back);
        }
        if (activeIndex < questions.length - 1) {
          const next = document.createElement("button");
          next.type = "button";
          next.className = "choice-submit";
          next.textContent = "下一题";
          next.disabled = !answers.has(question.id);
          next.addEventListener("click", () => {
            if (!answers.has(question.id)) {
              return;
            }
            activeIndex += 1;
            renderQuestion();
          });
          actions.append(next);
        } else {
          const submit = document.createElement("button");
          submit.type = "submit";
          submit.className = "choice-submit";
          submit.textContent = "Submit";
          submit.disabled = questions.some((item) => !answers.has(item.id));
          actions.append(submit);
        }
      }

      syncInputs(selectedIds);
      saveCurrentAnswer(stored?.other_text ?? "");
      updateActions();
    }

    choicePanel.onsubmit = (event) => {
      event.preventDefault();
      const questionAnswers = questions
        .map((question) => answers.get(question.id))
        .filter((item): item is NonNullable<typeof item> => Boolean(item));
      const ids = questionAnswers.flatMap((item) => item.selected_option_ids);
      const answer: UserInputAnswer = {
        request_id: request.request_id,
        request_ids: questions.map((question) => question.id),
        selected_option_id: ids[0] ?? "",
        selected_option_ids: ids,
        answer: questionAnswers.map((item, index) => `${index + 1}. ${item.question}\n答：${item.answer}`).join("\n"),
        answers: questionAnswers.map((item) => item.answer),
        question_answers: questionAnswers,
        other_text: questionAnswers.find((item) => item.other_text)?.other_text,
      };
      closeChoicePanelWithAnimation(() => {
        resolve(answer);
      });
    };
    choicePanel.classList.add("open");
    choicePanel.setAttribute("aria-hidden", "false");
    renderQuestion();
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

async function playBubbleSequence(parts: ChatBubblePart[], options: { voiceReply: boolean } = { voiceReply: false }) {
  clearBubbleSequence();
  stopCurrentVoicePlayback();
  if (parts.length === 0) {
    showChatBubble({ text: "我刚才有点走神了，再说一次好吗？", emoji: "confused" }, 5200);
    finishElfTurn();
    return;
  }

  if (!options.voiceReply) {
    isElfSpeaking = false;
    playTextBubbleSequence(parts);
    return;
  }

  voicePlaybackGeneration += 1;
  const generation = voicePlaybackGeneration;
  voiceQueue = parts.map((part) => ({
    part,
    audioPromise: prefetchBubbleAudio(part),
  }));
  isElfSpeaking = true;
  setChatInputEnabled(false);
  await drainVoiceQueue(generation);
}

function playTextBubbleSequence(parts: ChatBubblePart[]) {
  let index = 0;
  const showNext = () => {
    const part = parts[index];
    if (!part) {
      finishElfTurn();
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
        finishElfTurn();
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

function isElfBusy() {
  return isElfChatRunning || isElfSpeaking;
}

function enqueueVoiceBubbles(parts: ChatBubblePart[], options: { voiceReply: boolean } = { voiceReply: false }) {
  if (!options.voiceReply) {
    return;
  }
  const normalizedParts = uniqueUnqueuedVoiceParts(parts);
  if (normalizedParts.length === 0) {
    if (!isElfSpeaking && !isElfChatRunning) {
      finishElfTurn();
    }
    return;
  }
  voiceQueue.push(
    ...normalizedParts.map((part) => ({
      part,
      audioPromise: prefetchBubbleAudio(part),
    })),
  );
  if (isElfSpeaking) {
    return;
  }
  isElfSpeaking = true;
  setChatInputEnabled(false);
  const generation = voicePlaybackGeneration;
  void drainVoiceQueue(generation);
}

async function drainVoiceQueue(generation: number) {
  let lastPart: ChatBubblePart | null = null;
  while (voiceQueue.length > 0 && generation === voicePlaybackGeneration) {
    const item = voiceQueue.shift();
    if (!item) {
      continue;
    }
    lastPart = item.part;
    await playSingleBubble(item, generation);
  }

  if (generation !== voicePlaybackGeneration) {
    return;
  }
  if (isElfChatRunning) {
    isElfSpeaking = false;
    return;
  }
  const tailDelayMs = lastPart ? Math.min(3200, Math.max(900, bubbleDurationMs(lastPart.text) * 0.35)) : 420;
  bubbleSequenceTimer = window.setTimeout(() => {
    bubbleSequenceTimer = null;
    bubble?.classList.remove("visible");
    resetElfExpression();
    finishElfTurn();
  }, tailDelayMs);
}

function enqueueCompleteStreamingBubbles(delta: string, options: { voiceReply: boolean } = { voiceReply: false }) {
  if (!delta) {
    return;
  }
  streamingBubbleJsonBuffer += delta;
  const parts = parseStreamingBubbleParts(streamingBubbleJsonBuffer);
  if (parts.length === 0) {
    return;
  }
  streamedBubbleCount = parts.length;
  enqueueVoiceBubbles(parts, options);
}

function uniqueUnqueuedVoiceParts(parts: ChatBubblePart[]) {
  const uniqueParts: ChatBubblePart[] = [];
  for (const part of parts) {
    if (!part.text.trim()) {
      continue;
    }
    const key = bubbleTextKey(part);
    if (!key || queuedVoiceBubbleTextKeys.has(key)) {
      continue;
    }
    queuedVoiceBubbleTextKeys.add(key);
    uniqueParts.push(part);
  }
  return uniqueParts;
}

function bubbleTextKey(part: ChatBubblePart) {
  return normalizeBubbleText(part.text);
}

function normalizeBubbleText(text: string) {
  return text.replace(/\s+/g, "").trim();
}

function parseStreamingBubbleParts(raw: string): ChatBubblePart[] {
  const parts: ChatBubblePart[] = [];
  const pattern = /\{\s*"text"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"emoji"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(raw)) !== null) {
    const text = decodeJsonString(match[1]);
    if (!text.trim()) {
      continue;
    }
    parts.push({
      text,
      emoji: normalizeEmoji(decodeJsonString(match[2])),
    });
  }
  return parts;
}

function decodeJsonString(value: string) {
  try {
    return JSON.parse(`"${value}"`) as string;
  } catch {
    return value;
  }
}

async function playSingleBubble(item: QueuedVoiceBubble, generation: number) {
  const part = item.part;
  const fallbackTtlMs = bubbleDurationMs(part.text);
  try {
    const audio = await item.audioPromise;
    if (generation !== voicePlaybackGeneration) {
      URL.revokeObjectURL(audio.url);
      return;
    }
    showVoiceBubble(part);
    await playAudioBlob(audio.url, audio.mediaType, generation);
  } catch (error) {
    console.error("[memo-elf] tts playback failed", error);
    showVoiceBubble(part);
    clearBubbleAfter(fallbackTtlMs, { resetExpression: true });
    await waitForBubbleFallback(fallbackTtlMs, generation);
  }
}

function prefetchBubbleAudio(part: ChatBubblePart): Promise<{ url: string; mediaType: string }> {
  return fetchBubbleAudio(part);
}

async function fetchBubbleAudio(part: ChatBubblePart): Promise<{ url: string; mediaType: string }> {
  const response = await fetchWithTimeout(ELF_VOICE_SPEAK_URL, {
    body: JSON.stringify({
      text: part.text,
      emoji: part.emoji,
    }),
    headers: {
      "Content-Type": "application/json",
    },
    method: "POST",
  });
  if (!response.ok) {
    throw await responseError(response, "ELF_VOICE_SPEAK_FAILED");
  }
  const blob = await response.blob();
  return {
    url: URL.createObjectURL(blob),
    mediaType: blob.type || "audio/wav",
  };
}

function playAudioBlob(url: string, _mediaType: string, generation: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = new Audio();
    activeVoiceAudio = audio;
    activeVoiceUrl = url;
    audio.preload = "auto";
    audio.src = url;
    const cleanup = () => {
      audio.onended = null;
      audio.onerror = null;
      if (activeVoiceAudio === audio) {
        activeVoiceAudio = null;
      }
      if (activeVoiceUrl === url) {
        activeVoiceUrl = null;
      }
      URL.revokeObjectURL(url);
    };
    audio.onended = () => {
      cleanup();
      resolve();
    };
    audio.onerror = () => {
      cleanup();
      reject(new Error("Elf voice audio playback failed."));
    };
    if (generation !== voicePlaybackGeneration) {
      cleanup();
      resolve();
      return;
    }
    audio.play().catch((error) => {
      cleanup();
      reject(error);
    });
  });
}

function waitForBubbleFallback(ttlMs: number, generation: number): Promise<void> {
  return new Promise((resolve) => {
    bubbleSequenceTimer = window.setTimeout(() => {
      if (generation === voicePlaybackGeneration) {
        bubbleSequenceTimer = null;
      }
      resolve();
    }, ttlMs + 420);
  });
}

function clearVoicePlayback() {
  voicePlaybackGeneration += 1;
  stopCurrentVoicePlayback();
}

function stopCurrentVoicePlayback() {
  const pending = voiceQueue;
  voiceQueue = [];
  for (const item of pending) {
    item.audioPromise.then((audio) => URL.revokeObjectURL(audio.url)).catch(() => {});
  }
  if (activeVoiceAudio) {
    activeVoiceAudio.pause();
    activeVoiceAudio.removeAttribute("src");
    activeVoiceAudio.load();
    activeVoiceAudio = null;
  }
  if (activeVoiceUrl) {
    URL.revokeObjectURL(activeVoiceUrl);
    activeVoiceUrl = null;
  }
  isElfSpeaking = false;
}

function finishElfTurn() {
  isElfChatRunning = false;
  isElfSpeaking = false;
  setChatInputEnabled(true);
  hideChoicePanel();
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
  if (voiceHoldButton) {
    voiceHoldButton.disabled = !enabled || !isElfVoiceModeEnabled;
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
  questions?: UserInputQuestion[];
}

interface UserInputQuestion {
  id: string;
  question: string;
  selection_mode: "single" | "multiple";
  options: UserInputOption[];
  allow_other: boolean;
  other_placeholder: string;
}

interface UserInputAnswer {
  request_id: string;
  selected_option_id: string;
  selected_option_ids: string[];
  answer: string;
  other_text?: string;
  request_ids?: string[];
  answers?: string[];
  question_answers?: Array<{
    question_id: string;
    question: string;
    answer: string;
    selected_option_id: string;
    selected_option_ids: string[];
    selected_option_labels: string[];
    other_text?: string;
    is_other?: boolean;
  }>;
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

interface QueuedVoiceBubble {
  part: ChatBubblePart;
  audioPromise: Promise<{ url: string; mediaType: string }>;
}
