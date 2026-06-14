import { Check, GitBranch, MessageCircleQuestion, MoreHorizontal, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type RefObject } from "react";

import { Button, EmptyState } from "../../shared/ui";
import {
  ChatMessageBody,
  hasVisibleAssistantWork as sharedHasVisibleAssistantWork,
  TypingIndicator as SharedTypingIndicator,
} from "../chat_view/ChatMessageBody";
import { resolveAttachmentUrl } from "./chatApi";
import { MarkdownMessage } from "./MarkdownMessage";
import type {
  ChatThought,
  ChatAttachment,
  DraftAssistantMessage,
  SegmentFollowupRequest,
  UserInputAnswer,
  UserInputQuestion,
  UserInputRequest,
} from "./types";

interface MessageListProps {
  endRef: RefObject<HTMLDivElement | null>;
  listRef?: RefObject<HTMLDivElement | null>;
  messages: DraftAssistantMessage[];
  onScroll?: () => void;
  activeFollowupSourceId?: number | null;
  activeFollowupSegmentId?: string | null;
  exportMode?: boolean;
  isStreaming?: boolean;
  onDeleteMessage: (message: DraftAssistantMessage) => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onOpenFollowups: (message: DraftAssistantMessage, segmentId?: string | null) => void;
  onExecuteCommandSuggestion?: (command: string) => Promise<void> | void;
  onSegmentFollowup: (request: SegmentFollowupRequest) => void;
  onStopGeneration: () => void;
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
  onToggleExportMessage?: (messageId: number) => void;
  selectedExportMessageIds?: Set<number>;
  thoughts?: ChatThought[];
}

export function MessageList({
  endRef,
  listRef,
  messages,
  onScroll,
  activeFollowupSourceId,
  activeFollowupSegmentId,
  exportMode = false,
  isStreaming = false,
  onDeleteMessage,
  onOpenGraph,
  onOpenFollowups,
  onExecuteCommandSuggestion,
  onSegmentFollowup,
  onStopGeneration,
  onSubmitUserInput,
  onToggleExportMessage,
  selectedExportMessageIds = new Set(),
  thoughts = [],
}: MessageListProps) {
  const [selectionMenu, setSelectionMenu] = useState<{
    message: DraftAssistantMessage;
    original_text: string;
    position: { start: number; end: number } | null;
    anchor: { x: number; y: number };
  } | null>(null);
  const [messageMenu, setMessageMenu] = useState<{
    message: DraftAssistantMessage;
    anchor: { x: number; y: number };
    original_text?: string;
    position?: { start: number; end: number } | null;
  } | null>(null);
  const [draftFollowup, setDraftFollowup] = useState<{
    message: DraftAssistantMessage;
    original_text: string;
    position: { start: number; end: number } | null;
    anchor: { x: number; y: number };
  } | null>(null);
  const [imagePreview, setImagePreview] = useState<{
    alt: string;
    name: string;
    src: string;
  } | null>(null);
  const followupInputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!selectionMenu && !messageMenu) {
      return;
    }
    function closeFloatingMenus(event: PointerEvent) {
      if (event.target instanceof HTMLElement && event.target.closest(".segment-followup-menu")) {
        return;
      }
      setSelectionMenu(null);
      setMessageMenu(null);
      window.getSelection()?.removeAllRanges();
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") {
        return;
      }
      setSelectionMenu(null);
      setMessageMenu(null);
      window.getSelection()?.removeAllRanges();
    }
    document.addEventListener("pointerdown", closeFloatingMenus);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeFloatingMenus);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [messageMenu, selectionMenu]);

  function handleAssistantSelection(
    message: DraftAssistantMessage,
    eventTarget: EventTarget | null,
    pointer: { x: number; y: number },
  ) {
    if (exportMode) {
      return;
    }
    if (message.isStreaming || message.role !== "assistant") {
      return;
    }
    const selection = window.getSelection();
    if (!selection || selectionTouchesCode(selection)) {
      setDraftFollowup(null);
      setSelectionMenu(null);
      return;
    }
    const selectedText = selection.toString().replace(/\s+/g, " ").trim();
    if (selectedText.length < 2) {
      return;
    }
    const target = eventTarget instanceof HTMLElement
      ? eventTarget.closest<HTMLElement>(".chat-message-content")
      : null;
    if (!target || !target.contains(selection.anchorNode) || !target.contains(selection.focusNode)) {
      return;
    }
    if (selectionIntersectsFollowupMark(selection, target)) {
      setDraftFollowup(null);
      setSelectionMenu(null);
      return;
    }
    const selectionRoot = closestElement(selection.anchorNode)?.closest(".markdown-message") as HTMLElement | null;
    const position = selectionRoot && selectionRoot.contains(selection.focusNode)
      ? resolveSelectionTextPosition(selectionRoot, selection)
      : resolveTextPosition(message.content, selectedText);
    if (selectionOverlapsFollowupThread(message, selectedText, position)) {
      setDraftFollowup(null);
      setSelectionMenu(null);
      return;
    }
    setDraftFollowup(null);
    setMessageMenu(null);
    setSelectionMenu({
      message,
      original_text: selectedText,
      position,
      anchor: selectionFollowupAnchor(selection, pointer, { width: 156, height: 46 }),
    });
  }

  function openDraftFollowup() {
    const source = selectionMenu ?? messageMenu;
    if (!source?.original_text) {
      return;
    }
    setDraftFollowup({
      message: source.message,
      original_text: source.original_text,
      position: source.position ?? null,
      anchor: clampFloatingAnchor(source.anchor, { width: 390, height: 230 }),
    });
    setSelectionMenu(null);
    setMessageMenu(null);
    window.setTimeout(() => followupInputRef.current?.focus(), 0);
  }

  function openMessageMenu(message: DraftAssistantMessage, event: ReactMouseEvent<HTMLElement>) {
    event.preventDefault();
    setSelectionMenu(null);
    setDraftFollowup(null);
    setMessageMenu({
      message,
      anchor: clampFloatingAnchor(
        { x: event.clientX + 8, y: event.clientY + 8 },
        { width: 132, height: 46 },
      ),
    });
  }

  function submitDraftFollowup(question: string) {
    if (!draftFollowup || !question.trim()) {
      return;
    }
    const existingThread = findMatchingFollowupThread(
      draftFollowup.message,
      draftFollowup.original_text,
      draftFollowup.position ?? null,
    );
    onSegmentFollowup({
      source_message_id: draftFollowup.message.id,
      segment_id: existingThread?.segment_id ?? null,
      original_text: draftFollowup.original_text,
      user_question: question.trim(),
      position: draftFollowup.position,
    });
    setDraftFollowup(null);
    window.getSelection()?.removeAllRanges();
  }

  return (
    <div className="chat-message-list" onScroll={onScroll} ref={listRef}>
      {messages.length === 0 ? (
        <EmptyState className="chat-empty">向 AiMemo 提一个关于笔记或记忆的问题</EmptyState>
      ) : null}
      {messages.filter((message) => !message.ui_hidden).map((message) => {
        const isAssistant = message.role === "assistant";
        const isStreaming = isAssistant && message.isStreaming === true;
        const isExportSelected = selectedExportMessageIds.has(message.id);
        const canSelectForExport = exportMode && message.id > 0;
        // 优先使用 segments：streaming 期间由 streamingStore 实时拼装；done 后从落库消息恢复。
        const segments = message.segments ?? [];
        const hasVisibleWork = sharedHasVisibleAssistantWork(segments, thoughts, message.content);
        const isAssistantWarmingUp =
          isStreaming &&
          !hasVisibleWork;

        return (
          <article
            className={`chat-message ${message.role}${exportMode ? " chat-message--export-mode" : ""}${isExportSelected ? " chat-message--export-selected" : ""}`}
            key={message.id}
            onContextMenu={(event) => {
              if (exportMode) {
                return;
              }
              openMessageMenu(message, event);
            }}
          >
            {exportMode ? (
              <button
                aria-label={isExportSelected ? "取消选择这条消息" : "选择这条消息"}
                aria-pressed={isExportSelected}
                className="chat-message-export-check"
                disabled={!canSelectForExport}
                onClick={() => onToggleExportMessage?.(message.id)}
                title={isExportSelected ? "取消选择" : "选择导出"}
                type="button"
              >
                {isExportSelected ? <Check aria-hidden="true" size={14} /> : null}
              </button>
            ) : null}
            <div className="chat-message-bubble">
              <div
                className="chat-message-content"
                onMouseUp={(event) =>
                  handleAssistantSelection(message, event.target, {
                    x: event.clientX + 8,
                    y: event.clientY + 10,
                  })
                }
              >
                {isAssistantWarmingUp ? <SharedTypingIndicator /> : null}
                {isAssistant ? (
                  <ChatMessageBody
                    activeSegmentId={activeFollowupSourceId === message.id ? activeFollowupSegmentId : null}
                    message={message}
                    onExecuteCommandSuggestion={onExecuteCommandSuggestion}
                    onOpenSegment={(segmentId) => onOpenFollowups(message, segmentId)}
                    commandActionsDisabled={isStreaming}
                    thoughts={isStreaming ? thoughts : message.thoughts}
                    isWarmingUp={isAssistantWarmingUp}
                  />
                ) : (
                  <MarkdownMessage content={message.content} fallback="" />
                )}
                {message.attachments && message.attachments.length > 0 ? (
                  <MessageAttachments attachments={message.attachments} onPreviewImage={setImagePreview} />
                ) : null}
                {isAssistant && message.pending_interrupt ? (
                  <UserInputInterruptCard
                    disabled={message.isStreaming === true}
                    message={message}
                    request={message.pending_interrupt}
                    onSubmit={onSubmitUserInput}
                  />
                ) : null}
              </div>
            </div>
            {isAssistant && message.turn_id ? (
              <div className="chat-message-actions">
                <Button
                  aria-label="查看片段追问"
                  className={`chat-message-action--followups ${activeFollowupSourceId === message.id ? "is-active" : ""}`}
                  onClick={() => onOpenFollowups(message)}
                  size="icon"
                  title="查看片段追问"
                >
                  <MessageCircleQuestion aria-hidden="true" size={16} />
                  {hasMessageFollowups(message) ? (
                    <span className="chat-message-action-dot" aria-hidden="true">
                      <MoreHorizontal size={12} />
                    </span>
                  ) : null}
                </Button>
                <Button
                  aria-label="查看本轮 graph"
                  onClick={() => onOpenGraph(message)}
                  size="icon"
                  title="查看本轮 graph"
                >
                  <GitBranch aria-hidden="true" size={16} />
                </Button>
              </div>
            ) : null}
          </article>
        );
      })}
      {selectionMenu ? (
        <SegmentFollowupMenu
          anchor={selectionMenu.anchor}
          onDelete={() => {
            onDeleteMessage(selectionMenu.message);
            setSelectionMenu(null);
          }}
          onOpen={openDraftFollowup}
          canFollowup
        />
      ) : null}
      {messageMenu ? (
        <SegmentFollowupMenu
          anchor={messageMenu.anchor}
          canDelete={!isStreaming && messageMenu.message.isStreaming !== true}
          canFollowup={false}
          onDelete={() => {
            onDeleteMessage(messageMenu.message);
            setMessageMenu(null);
          }}
          onOpen={openDraftFollowup}
        />
      ) : null}
      {draftFollowup ? (
        <SegmentFollowupComposer
          anchor={draftFollowup.anchor}
          inputRef={followupInputRef}
          originalText={draftFollowup.original_text}
          onCancel={() => setDraftFollowup(null)}
          onSubmit={submitDraftFollowup}
        />
      ) : null}
      {imagePreview ? (
        <ImagePreviewModal image={imagePreview} onClose={() => setImagePreview(null)} />
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function MessageAttachments({
  attachments,
  onPreviewImage,
}: {
  attachments: ChatAttachment[];
  onPreviewImage: (image: { alt: string; name: string; src: string }) => void;
}) {
  return (
    <div className="chat-message-attachments">
      {attachments.map((attachment, index) => {
        const key = attachment.id ? String(attachment.id) : `${attachment.original_name}-${index}`;
        if (attachment.kind === "image" && attachment.url) {
          const src = resolveAttachmentUrl(attachment.url);
          return (
            <button
              className="chat-message-attachment chat-message-attachment--image"
              key={key}
              onClick={() => onPreviewImage({ alt: attachment.original_name, name: attachment.original_name, src })}
              title={attachment.original_name}
              type="button"
            >
              <img alt={attachment.original_name} src={src} />
              <span>{attachment.original_name}</span>
            </button>
          );
        }
        return (
          <a
            className="chat-message-attachment"
            href={attachment.url ? resolveAttachmentUrl(attachment.url) : undefined}
            key={key}
            rel="noreferrer"
            target="_blank"
            title={attachment.original_name}
          >
            <span>{attachment.original_name}</span>
            <small>{formatAttachmentSize(attachment.size_bytes)}</small>
          </a>
        );
      })}
    </div>
  );
}

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${sizeBytes} B`;
}

function SegmentFollowupMenu({
  anchor,
  canDelete,
  canFollowup,
  onDelete,
  onOpen,
}: {
  anchor: { x: number; y: number };
  canDelete?: boolean;
  canFollowup?: boolean;
  onDelete?: () => void;
  onOpen: () => void;
}) {
  return (
    <div className="segment-followup-menu" style={{ left: anchor.x, top: anchor.y }}>
      {canFollowup ? (
        <button onClick={onOpen} type="button">
          <MessageCircleQuestion aria-hidden="true" size={15} />
          追问片段
        </button>
      ) : null}
      {canDelete ? (
        <button className="segment-followup-menu__danger" onClick={onDelete} type="button">
          <Trash2 aria-hidden="true" size={15} />
          删除对话
        </button>
      ) : null}
    </div>
  );
}

function SegmentFollowupComposer({
  anchor,
  inputRef,
  originalText,
  onCancel,
  onSubmit,
}: {
  anchor: { x: number; y: number };
  inputRef: RefObject<HTMLTextAreaElement | null>;
  originalText: string;
  onCancel: () => void;
  onSubmit: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");
  return (
    <div
      className="segment-followup-composer"
      role="dialog"
      aria-label="针对选中片段追问"
      style={{ left: anchor.x, top: anchor.y }}
    >
      <div className="segment-followup-composer__quote">
        <MessageCircleQuestion aria-hidden="true" size={16} />
        <span>{originalText}</span>
      </div>
      <textarea
        ref={inputRef}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            onCancel();
          }
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            onSubmit(question);
          }
        }}
        placeholder="针对这段内容追问..."
        value={question}
      />
      <div className="segment-followup-composer__actions">
        <Button onClick={onCancel} size="sm" type="button" variant="secondary">
          取消
        </Button>
        <Button disabled={!question.trim()} onClick={() => onSubmit(question)} size="sm" type="button" variant="primary">
          追问
        </Button>
      </div>
    </div>
  );
}

function clampFloatingAnchor(
  anchor: { x: number; y: number },
  size: { width: number; height: number },
): { x: number; y: number } {
  const margin = 12;
  const maxX = Math.max(margin, window.innerWidth - size.width - margin);
  const maxY = Math.max(margin, window.innerHeight - size.height - margin);
  return {
    x: Math.min(Math.max(anchor.x, margin), maxX),
    y: Math.min(Math.max(anchor.y, margin), maxY),
  };
}

function selectionFollowupAnchor(
  selection: Selection,
  pointer: { x: number; y: number },
  size: { width: number; height: number },
): { x: number; y: number } {
  const rect = selectionBoundingRect(selection);
  if (!rect) {
    return clampFloatingAnchor({ x: pointer.x + 18, y: pointer.y + 64 }, size);
  }
  const preferredY = rect.bottom + 54;
  const canPlaceBelow = preferredY + size.height <= window.innerHeight - 12;
  const y = canPlaceBelow ? preferredY : Math.max(12, rect.top - size.height - 54);
  const x = rect.left + rect.width / 2 - size.width / 2 + 72;
  return clampFloatingAnchor({ x, y }, size);
}

function selectionBoundingRect(selection: Selection): DOMRect | null {
  if (selection.rangeCount === 0) {
    return null;
  }
  const rects = Array.from({ length: selection.rangeCount }, (_, index) =>
    selection.getRangeAt(index).getBoundingClientRect(),
  ).filter((rect) => rect.width > 0 || rect.height > 0);
  if (rects.length === 0) {
    return null;
  }
  const left = Math.min(...rects.map((rect) => rect.left));
  const top = Math.min(...rects.map((rect) => rect.top));
  const right = Math.max(...rects.map((rect) => rect.right));
  const bottom = Math.max(...rects.map((rect) => rect.bottom));
  return new DOMRect(left, top, right - left, bottom - top);
}

function resolveTextPosition(content: string, selectedText: string): { start: number; end: number } | null {
  const start = normalizeText(content).indexOf(normalizeText(selectedText));
  if (start < 0) {
    return null;
  }
  return { start, end: start + selectedText.length };
}

function resolveSelectionTextPosition(
  root: HTMLElement,
  selection: Selection,
): { start: number; end: number } | null {
  if (selection.rangeCount === 0) {
    return null;
  }
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) {
    return null;
  }

  const textNodes = followupTextNodes(root);
  let cursor = 0;
  let start: number | null = null;
  let end: number | null = null;
  for (const textNode of textNodes) {
    const text = textNode.textContent ?? "";
    if (!text) {
      continue;
    }
    if (range.startContainer === textNode) {
      start = cursor + range.startOffset;
    }
    if (range.endContainer === textNode) {
      end = cursor + range.endOffset;
    }
    cursor += text.length;
  }
  if (start === null || end === null || end <= start) {
    return null;
  }
  return { start, end };
}

function selectionOverlapsFollowupThread(
  message: DraftAssistantMessage,
  selectedText: string,
  position: { start: number; end: number } | null,
): boolean {
  const selected = normalizeText(selectedText);
  if (!selected) {
    return false;
  }
  return (message.followupThreads ?? []).some((thread) => {
    if (position && thread.position) {
      return position.start < thread.position.end && position.end > thread.position.start;
    }
    const marked = normalizeText(thread.original_text);
    if (marked.includes(selected) || selected.includes(marked)) {
      return true;
    }
    return false;
  });
}

function selectionIntersectsFollowupMark(selection: Selection, root: HTMLElement): boolean {
  if (selection.rangeCount === 0) {
    return false;
  }
  const range = selection.getRangeAt(0);
  return Array.from(root.querySelectorAll(".segment-followup-mark")).some((mark) => range.intersectsNode(mark));
}

function closestElement(node: Node | null): Element | null {
  if (!node) {
    return null;
  }
  return node instanceof Element ? node : node.parentElement;
}

function selectionTouchesCode(selection: Selection): boolean {
  return Boolean(
    closestElement(selection.anchorNode)?.closest("[data-followup-code='true'], pre, code") ||
      closestElement(selection.focusNode)?.closest("[data-followup-code='true'], pre, code"),
  );
}

function ImagePreviewModal({
  image,
  onClose,
}: {
  image: { alt: string; name: string; src: string };
  onClose: () => void;
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="image-preview-backdrop" onMouseDown={onClose} role="presentation">
      <section
        aria-label="图片预览"
        aria-modal="true"
        className="image-preview-modal"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header>
          <strong title={image.name}>{image.name}</strong>
          <button aria-label="关闭图片预览" onClick={onClose} title="关闭" type="button">
            <X aria-hidden="true" size={18} />
          </button>
        </header>
        <div className="image-preview-modal__body">
          <img alt={image.alt} src={image.src} />
        </div>
      </section>
    </div>
  );
}

function followupTextNodes(root: HTMLElement): Text[] {
  const nodes: Text[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || !node.textContent) {
        return NodeFilter.FILTER_REJECT;
      }
      if (!node.textContent.trim()) {
        return NodeFilter.FILTER_REJECT;
      }
      if (
        parent.closest(
          [
            "pre",
            "code",
            "button",
            ".markdown-code-block__toolbar",
            ".segment-followup-mark",
            ".mermaid-viewer",
            ".markdown-mermaid-error",
          ].join(", "),
        )
      ) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node = walker.nextNode();
  while (node) {
    nodes.push(node as Text);
    node = walker.nextNode();
  }
  return nodes;
}

function findMatchingFollowupThread(
  message: DraftAssistantMessage,
  originalText: string,
  position: { start: number; end: number } | null,
) {
  const threads = message.followupThreads ?? [];
  if (position) {
    const byPosition = threads.find(
      (thread) => thread.position && position.start < thread.position.end && position.end > thread.position.start,
    );
    if (byPosition) {
      return byPosition;
    }
  }
  return threads.find((thread) => normalizeText(thread.original_text) === normalizeText(originalText));
}

function hasMessageFollowups(message: DraftAssistantMessage): boolean {
  return (message.followupThreads ?? []).some((thread) => thread.followups.length > 0);
}

function normalizeText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

interface UserInputInterruptCardProps {
  disabled: boolean;
  message: DraftAssistantMessage;
  request: UserInputRequest;
  onSubmit: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
}

export function UserInputInterruptCard({
  disabled,
  message,
  request,
  onSubmit,
}: UserInputInterruptCardProps) {
  const questions = request.questions?.length
    ? request.questions
    : [
        {
          id: `${request.request_id}-question-1`,
          question: request.question,
          options: request.options,
          selection_mode: request.selection_mode,
          allow_other: request.allow_other,
          other_placeholder: request.other_option?.placeholder || "在这里补充你的答案",
        },
      ];
  const [activeIndex, setActiveIndex] = useState(0);
  type QuestionAnswer = NonNullable<UserInputAnswer["question_answers"]>[number];
  const [answers, setAnswers] = useState<Record<string, QuestionAnswer>>({});

  const current = questions[activeIndex];
  const currentAnswer = current ? answers[current.id] : undefined;
  const canGoBack = activeIndex > 0;
  const canGoNext = Boolean(currentAnswer);
  const canSubmit = questions.length > 0 && questions.every((question) => answers[question.id]);

  function updateCurrentAnswer(answer: QuestionAnswer) {
    if (!current) {
      return;
    }
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [current.id]: answer,
    }));
  }

  function handleSubmitCurrent(selection: {
    selected_option_id: string;
    selected_option_ids: string[];
    answer: string;
    other_text?: string;
    selected_option_labels: string[];
    is_other?: boolean;
  }) {
    if (!current) {
      return;
    }
    updateCurrentAnswer({
      question_id: current.id,
      question: current.question,
      ...selection,
    });
  }

  function goNext() {
    if (!canGoNext || disabled) {
      return;
    }
    setActiveIndex((index) => Math.min(index + 1, questions.length - 1));
  }

  function goBack() {
    if (!canGoBack || disabled) {
      return;
    }
    setActiveIndex((index) => Math.max(index - 1, 0));
  }

  function handleFinalSubmit() {
    if (disabled || !canSubmit) {
      return;
    }
    const question_answers = questions
      .map((question) => answers[question.id])
      .filter(Boolean)
      .map((item) => item!);
    const answer = question_answers.map((item, index) => `${index + 1}. ${item.question}\n答：${item.answer}`).join("\n");
    const selected_option_ids = question_answers.flatMap((item) => item.selected_option_ids);
    const selected_option_labels = question_answers.flatMap((item) => item.selected_option_labels);
    onSubmit(message, {
      request_id: request.request_id,
      request_ids: questions.map((question) => question.id),
      selected_option_id: selected_option_ids[0] ?? "",
      selected_option_ids,
      answer,
      answers: question_answers.map((item) => item.answer),
      question_answers,
      other_text: question_answers.find((item) => item.other_text)?.other_text,
    });
  }

  return (
    <div className="chat-interrupt-card" role="group" aria-label={request.question}>
      <div className="chat-interrupt-card__header">
        <p className="chat-interrupt-card__question">{current?.question ?? request.question}</p>
        {questions.length > 1 ? (
          <span className="chat-interrupt-card__step">
            {activeIndex + 1}/{questions.length}
          </span>
        ) : null}
      </div>
      <div className="chat-interrupt-options">
        {current?.options.map((option, index) => {
          const selectedIds = currentAnswer?.selected_option_ids ?? [];
          const checked = selectedIds.includes(option.id);
          return (
            <button
              className={`chat-interrupt-option ${checked ? "selected" : ""}`}
              key={option.id}
              onClick={() => {
                if (disabled || !current) {
                  return;
                }
                const mode = current.selection_mode === "multiple" ? "multiple" : "single";
                const nextSelected = mode === "single"
                  ? [option.id]
                  : checked
                    ? selectedIds.filter((item) => item !== option.id)
                    : [...selectedIds, option.id];
                handleSubmitCurrent({
                  ...selectedAnswerFromQuestion(current, nextSelected, currentAnswer?.other_text ?? ""),
                });
              }}
              type="button"
            >
              <input
                aria-hidden="true"
                checked={checked}
                disabled={disabled}
                name={`interrupt-${request.request_id}-${current?.id ?? "current"}`}
                onChange={() => undefined}
                tabIndex={-1}
                type={current?.selection_mode === "multiple" ? "checkbox" : "radio"}
              />
              <span className="chat-interrupt-option__mark" aria-hidden="true" />
              <span className="chat-interrupt-option__body">
                <span>
                  {option.label}
                  {option.recommended || index === 0 ? <em>推荐</em> : null}
                </span>
                {option.description ? <small>{option.description}</small> : null}
              </span>
            </button>
          );
        })}
        {current?.allow_other ? (
          <div
            className={`chat-interrupt-option chat-interrupt-option--other ${currentAnswer?.selected_option_ids?.includes("other") ? "selected" : ""}`}
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("input")) {
                return;
              }
              if (disabled || !current) {
                return;
              }
              const mode = current.selection_mode === "multiple" ? "multiple" : "single";
              const nextIds = mode === "single"
                ? ["other"]
                : currentAnswer?.selected_option_ids?.includes("other")
                  ? currentAnswer.selected_option_ids
                  : [...(currentAnswer?.selected_option_ids ?? []), "other"];
              updateCurrentAnswer({
                question_id: current.id,
                question: current.question,
                selected_option_id: nextIds[0] ?? "other",
                selected_option_ids: nextIds,
                answer: currentAnswer?.other_text?.trim() || "",
                selected_option_labels: ["其他"],
                other_text: currentAnswer?.other_text,
                is_other: true,
              });
            }}
          >
            <input
              aria-hidden="true"
              checked={currentAnswer?.selected_option_ids?.includes("other") ?? false}
              disabled={disabled}
              name={`interrupt-${request.request_id}-${current?.id ?? "current"}`}
              onChange={() => undefined}
              tabIndex={-1}
              type={current?.selection_mode === "multiple" ? "checkbox" : "radio"}
            />
            <span className="chat-interrupt-option__mark" aria-hidden="true" />
            <span className="chat-interrupt-option__body chat-interrupt-option__body--other">
              <div className="chat-interrupt-option__inline-row">
                <input
                  className="chat-interrupt-option__inline-input"
                  disabled={disabled}
                  onFocus={() => {
                    if (!current) {
                      return;
                    }
                    const mode = current.selection_mode === "multiple" ? "multiple" : "single";
                    const nextIds = mode === "single"
                      ? ["other"]
                      : currentAnswer?.selected_option_ids?.includes("other")
                        ? currentAnswer.selected_option_ids
                        : [...(currentAnswer?.selected_option_ids ?? []), "other"];
                    updateCurrentAnswer({
                      question_id: current.id,
                      question: current.question,
                      selected_option_id: nextIds[0] ?? "other",
                      selected_option_ids: nextIds,
                      answer: currentAnswer?.other_text?.trim() || "",
                      selected_option_labels: ["其他"],
                      other_text: currentAnswer?.other_text,
                      is_other: true,
                    });
                  }}
                  onChange={(event) => {
                    if (!current) {
                      return;
                    }
                    const mode = current.selection_mode === "multiple" ? "multiple" : "single";
                    const nextIds = mode === "single"
                      ? ["other"]
                      : currentAnswer?.selected_option_ids?.includes("other")
                        ? currentAnswer.selected_option_ids
                        : [...(currentAnswer?.selected_option_ids ?? []), "other"];
                    updateCurrentAnswer({
                      question_id: current.id,
                      question: current.question,
                      selected_option_id: nextIds[0] ?? "other",
                      selected_option_ids: nextIds,
                      answer: event.target.value.trim(),
                      selected_option_labels: ["其他"],
                      other_text: event.target.value,
                      is_other: true,
                    });
                  }}
                  placeholder={current?.other_placeholder || request.other_option?.placeholder || "请输入其他答案"}
                  value={currentAnswer?.other_text ?? ""}
                />
              </div>
            </span>
          </div>
        ) : null}
      </div>
      <div className="chat-interrupt-card__nav">
        {questions.length > 1 ? (
          <>
            <Button disabled={disabled || !canGoBack} onClick={goBack} size="sm" type="button" variant="secondary">
              上一题
            </Button>
            <Button disabled={disabled || !canGoNext} onClick={goNext} size="sm" type="button" variant="primary">
              下一题
            </Button>
          </>
        ) : null}
      </div>
      <div className="chat-interrupt-card__submit">
        <Button disabled={disabled || !canSubmit} onClick={handleFinalSubmit} size="md" type="button" variant="primary">
          <Check aria-hidden="true" size={16} />
          {canSubmit ? "确认提交" : "请选择一项"}
        </Button>
      </div>
    </div>
  );
}

function selectedAnswerFromQuestion(
  question: UserInputQuestion,
  selectedIds: string[],
  otherText = "",
) {
  const selectedOptions = question.options.filter((item) => selectedIds.includes(item.id));
  const answerParts = selectedOptions.map((item) => item.value || item.label).filter(Boolean);
  const trimmedOther = otherText.trim();
  if (trimmedOther) {
    answerParts.push(trimmedOther);
  }
  return {
    selected_option_id: selectedIds[0] ?? "",
    selected_option_ids: selectedIds,
    answer: answerParts.join("\n"),
    selected_option_labels: selectedOptions.map((item) => item.label).concat(selectedIds.includes("other") ? ["其他"] : []),
    other_text: otherText,
    is_other: selectedIds.includes("other"),
  };
}
