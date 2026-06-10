import { getCurrentWindow } from "@tauri-apps/api/window";

import "./overlay.css";

const bubble = document.querySelector<HTMLDivElement>("#bubble");
const elfMenu = document.querySelector<HTMLElement>("#elf-menu");
const openAppButton = document.querySelector<HTMLButtonElement>("#open-app");
const chatToggleButton = document.querySelector<HTMLButtonElement>("#chat-toggle");
const chatPanel = document.querySelector<HTMLFormElement>("#chat-panel");
const chatInput = document.querySelector<HTMLTextAreaElement>("#chat-input");
const chatSendButton = document.querySelector<HTMLButtonElement>("#chat-send");
const voiceHoldButton = document.querySelector<HTMLButtonElement>("#voice-hold");
const choicePanel = document.querySelector<HTMLFormElement>("#choice-panel");
const currentWindow = getCurrentWindow();

let currentMode: OverlayMode = "hidden";
let choiceCloseTimer: number | null = null;
let isOverlayVoiceRecording = false;
let isOverlayVoiceProcessing = false;

void currentWindow.listen<OverlayState>("elf-overlay-state", (event) => {
  applyOverlayState(event.payload);
});

void currentWindow.onFocusChanged(({ payload: focused }) => {
  if (!focused && (isOverlayVoiceRecording || isOverlayVoiceProcessing)) {
    return;
  }
  if (!focused && (currentMode === "menu" || currentMode === "chat")) {
    void emitCommand({ type: "close-panels" });
  }
});

openAppButton?.addEventListener("click", () => {
  void emitCommand({ type: "open-app" });
});

chatToggleButton?.addEventListener("click", () => {
  void emitCommand({ type: "show-chat" });
});

chatPanel?.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput?.value.trim() ?? "";
  if (!message) {
    return;
  }
  if (chatInput) {
    chatInput.value = "";
    autoResizeChatInput();
  }
  void emitCommand({ type: "chat-submit", message });
});

chatInput?.addEventListener("input", autoResizeChatInput);
chatInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chatPanel?.requestSubmit();
  }
});

voiceHoldButton?.addEventListener("click", (event) => {
  event.preventDefault();
  void emitCommand({ type: isOverlayVoiceRecording ? "voice-stop" : "voice-start" });
});

function applyOverlayState(state: OverlayState) {
  currentMode = state.mode;
  document.documentElement.dataset.overlayMode = state.mode;
  if (state.mode !== "chat") {
    isOverlayVoiceRecording = false;
    isOverlayVoiceProcessing = false;
  }
  hideAll();

  if (state.mode === "hidden") {
    return;
  }
  if (state.mode === "bubble") {
    const text = (state.bubbleText ?? "").trim();
    if (!text) {
      currentMode = "hidden";
      document.documentElement.dataset.overlayMode = "hidden";
      return;
    }
    showBubble(text);
    return;
  }
  if (state.mode === "menu") {
    elfMenu?.classList.add("open");
    elfMenu?.setAttribute("aria-hidden", "false");
    return;
  }
  if (state.mode === "chat") {
    const voiceModeEnabled = state.voiceModeEnabled === true;
    const voiceProcessing = state.voiceProcessing === true;
    isOverlayVoiceRecording = state.voiceRecording === true;
    isOverlayVoiceProcessing = voiceProcessing;
    chatPanel?.classList.add("open");
    chatPanel?.classList.toggle("voice-mode", voiceModeEnabled);
    chatPanel?.setAttribute("aria-hidden", "false");
    if (chatInput) {
      chatInput.disabled = state.chatDisabled === true;
      chatInput.placeholder = voiceProcessing
        ? "正在识别..."
        : isOverlayVoiceRecording
          ? "正在听，说完会自动发送"
          : "想和我说什么？";
    }
    if (chatSendButton) {
      chatSendButton.disabled = state.chatDisabled === true;
    }
    if (voiceHoldButton) {
      voiceHoldButton.hidden = !voiceModeEnabled;
      voiceHoldButton.disabled = state.chatDisabled === true || !voiceModeEnabled || voiceProcessing;
      voiceHoldButton.classList.toggle("recording", isOverlayVoiceRecording);
      updateVoiceButtonLabel(isOverlayVoiceRecording, voiceProcessing);
    }
    window.setTimeout(() => {
      chatInput?.focus();
      autoResizeChatInput();
    }, 0);
    return;
  }
  if (state.mode === "choice" && state.choiceRequest) {
    showChoicePanel(state.choiceRequest);
  }
}

function hideAll() {
  bubble?.classList.remove("visible", "scrollable");
  elfMenu?.classList.remove("open");
  elfMenu?.setAttribute("aria-hidden", "true");
  chatPanel?.classList.remove("open", "voice-mode");
  chatPanel?.setAttribute("aria-hidden", "true");
  voiceHoldButton?.classList.remove("recording");
  choicePanel?.classList.remove("open", "closing");
  choicePanel?.setAttribute("aria-hidden", "true");
  if (currentMode !== "choice") {
    choicePanel?.replaceChildren();
  }
}

function updateVoiceButtonLabel(recording: boolean, processing = false) {
  if (!voiceHoldButton) {
    return;
  }
  const label = processing ? "正在识别" : recording ? "结束录音" : "语音输入";
  voiceHoldButton.setAttribute("aria-label", label);
  voiceHoldButton.title = label;
}

function showBubble(message: string) {
  if (!bubble) {
    return;
  }
  const text = message.trim();
  if (!text) {
    bubble.classList.remove("visible", "scrollable");
    bubble.textContent = "";
    return;
  }
  bubble.textContent = text;
  bubble.classList.remove("scrollable");
  bubble.classList.add("visible");
  window.requestAnimationFrame(() => {
    if (!bubble.classList.contains("visible")) {
      return;
    }
    bubble.classList.toggle("scrollable", shouldBubbleScroll(bubble));
  });
}

function shouldBubbleScroll(element: HTMLElement) {
  const maxHeight = Number.parseFloat(window.getComputedStyle(element).maxHeight);
  const isHeightCapped = Number.isFinite(maxHeight) && element.clientHeight >= maxHeight - 2;
  return isHeightCapped && element.scrollHeight > element.clientHeight + 12;
}

function showChoicePanel(request: UserInputRequest) {
  if (!choicePanel) {
    return;
  }
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
  choicePanel.append(header, list, actions);

  function syncInputs(selectedIds: Set<string>) {
    choicePanel?.querySelectorAll<HTMLInputElement>('input[type="radio"], input[type="checkbox"]').forEach((input) => {
      input.checked = selectedIds.has(input.value);
      input.closest("label")?.classList.toggle("selected", input.checked);
    });
  }

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
        selected_option_labels: selectedOptions.map((option) => option.label).concat(ids.includes("other") ? ["其他"] : []),
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
    choicePanel.classList.remove("open");
    choicePanel.classList.add("closing");
    choiceCloseTimer = window.setTimeout(() => {
      choicePanel.classList.remove("closing");
      choicePanel.replaceChildren();
      choiceCloseTimer = null;
      void emitCommand({ type: "choice-submit", answer });
    }, 190);
  };
  choicePanel.classList.add("open");
  choicePanel.setAttribute("aria-hidden", "false");
  renderQuestion();
}

function autoResizeChatInput() {
  if (!chatInput) {
    return;
  }
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 110)}px`;
}

function emitCommand(command: OverlayCommand) {
  return currentWindow.emitTo("elf", "elf-overlay-command", command);
}

type OverlayMode = "hidden" | "bubble" | "menu" | "chat" | "choice";

interface OverlayState {
  mode: OverlayMode;
  bubbleText?: string;
  chatDisabled?: boolean;
  voiceModeEnabled?: boolean;
  voiceProcessing?: boolean;
  voiceRecording?: boolean;
  choiceRequest?: UserInputRequest;
}

type OverlayCommand =
  | { type: "open-app" }
  | { type: "show-chat" }
  | { type: "chat-submit"; message: string }
  | { type: "close-panels" }
  | { type: "choice-submit"; answer: UserInputAnswer }
  | { type: "voice-start" }
  | { type: "voice-stop" };

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
