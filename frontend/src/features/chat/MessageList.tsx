import { Check, GitBranch, Maximize2, MessageCircleQuestion, MoreHorizontal, Send, Square, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type RefObject } from "react";
import { createPortal } from "react-dom";

import { Button, EmptyState } from "../../shared/ui";
import { MarkdownMessage } from "./MarkdownMessage";
import { PulseGlyph } from "./PulseGlyph";
import { groupThoughtsByStep } from "./streamingStore";
import { ToolCallCard } from "./ToolCallCard";
import { VerbRotator } from "./VerbRotator";
import type {
  ChatThought,
  DraftAssistantMessage,
  MessageSegment,
  SegmentFollowupRequest,
  SegmentFollowupThread,
  ToolInvocation,
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
  isStreaming?: boolean;
  onDeleteMessage: (message: DraftAssistantMessage) => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onOpenFollowups: (message: DraftAssistantMessage, segmentId?: string | null) => void;
  onSegmentFollowup: (request: SegmentFollowupRequest) => void;
  onStopGeneration: () => void;
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
  thoughts?: ChatThought[];
}

export function MessageList({
  endRef,
  listRef,
  messages,
  onScroll,
  activeFollowupSourceId,
  activeFollowupSegmentId,
  isStreaming = false,
  onDeleteMessage,
  onOpenGraph,
  onOpenFollowups,
  onSegmentFollowup,
  onStopGeneration,
  onSubmitUserInput,
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
    if (message.isStreaming || message.role !== "assistant") {
      return;
    }
    const selection = window.getSelection();
    const selectedText = selection?.toString().replace(/\s+/g, " ").trim() ?? "";
    if (!selection || selectedText.length < 2) {
      return;
    }
    const target = eventTarget instanceof HTMLElement ? eventTarget.closest(".chat-message-content") : null;
    if (!target || !target.contains(selection.anchorNode) || !target.contains(selection.focusNode)) {
      return;
    }
    const position = resolveTextPosition(message.content, selectedText);
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
      anchor: clampFloatingAnchor(pointer, { width: 156, height: 46 }),
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
    const existingThread = draftFollowup.message.followupThreads?.find(
      (thread) => normalizeText(thread.original_text) === normalizeText(draftFollowup.original_text),
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
        // 优先使用 segments：streaming 期间由 streamingStore 实时拼装；done 后从落库消息恢复。
        const segments = message.segments ?? [];
        const hasVisibleWork = hasVisibleAssistantWork(segments, thoughts, message.content);
        const isAssistantWarmingUp =
          isStreaming &&
          !hasVisibleWork;

        return (
          <article
            className={`chat-message ${message.role}`}
            key={message.id}
            onContextMenu={(event) => openMessageMenu(message, event)}
          >
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
                {isAssistantWarmingUp ? <TypingIndicator /> : null}
                {isAssistant ? (
                  <AssistantMessageBody
                    activeSegmentId={activeFollowupSourceId === message.id ? activeFollowupSegmentId : null}
                    message={message}
                    onOpenSegment={(segmentId) => onOpenFollowups(message, segmentId)}
                    thoughts={isStreaming ? thoughts : message.thoughts}
                    isWarmingUp={isAssistantWarmingUp}
                  />
                ) : (
                  <p>{message.content}</p>
                )}
                {isAssistant && message.pending_interrupt ? (
                  <UserInputInterruptCard
                    disabled={message.isStreaming === true}
                    message={message}
                    request={message.pending_interrupt}
                    onSubmit={onSubmitUserInput}
                  />
                ) : null}
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
            </div>
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
      <div ref={endRef} />
    </div>
  );
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

export function SegmentFollowupPanel({
  activeSegmentId,
  messages,
  onClose,
  onDeleteMessage,
  onOpenGraph,
  onOpenSegment,
  onSegmentFollowup,
  onStopGeneration,
  sourceMessage,
  thoughts = [],
}: {
  activeSegmentId?: string | null;
  messages: DraftAssistantMessage[];
  onClose: () => void;
  onDeleteMessage: (message: DraftAssistantMessage) => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onOpenSegment?: (segmentId: string | null) => void;
  onSegmentFollowup: (request: SegmentFollowupRequest) => void;
  onStopGeneration: () => void;
  sourceMessage: DraftAssistantMessage | null;
  thoughts?: ChatThought[];
}) {
  const [expandedThreadKey, setExpandedThreadKey] = useState<string | null>(null);
  const itemRefs = useRef<Record<string, HTMLDetailsElement | null>>({});
  const threads = sourceMessage ? buildFollowupPanelThreads(sourceMessage, messages) : [];
  const expandedThread = expandedThreadKey == null
    ? null
    : threads.find((thread) => thread.key === expandedThreadKey) ?? null;
  const activeEntryKey =
    activeSegmentId == null
      ? null
      : threads.find((thread) => thread.segmentId === activeSegmentId)?.key ?? null;

  useEffect(() => {
    if (!activeEntryKey) {
      return;
    }
    const item = itemRefs.current[activeEntryKey];
    if (!item) {
      return;
    }
    item.open = true;
    item.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeEntryKey, threads.length]);

  function submitThreadFollowup(thread: FollowupPanelThread, question: string) {
    if (!sourceMessage || !question.trim()) {
      return;
    }
    onOpenSegment?.(thread.segmentId);
    onSegmentFollowup({
      source_message_id: sourceMessage.id,
      segment_id: thread.segmentId,
      original_text: thread.originalText,
      user_question: question.trim(),
      position: thread.position,
    });
  }

  return (
    <aside className="segment-followup-panel" aria-label="片段追问侧栏">
      <header className="segment-followup-panel__header">
        <div>
          <h3>片段追问</h3>
          <p>{sourceMessage ? "当前回复中的局部讨论" : "选择一条 AI 回复查看"}</p>
        </div>
        <Button aria-label="收起片段追问" onClick={onClose} size="icon" title="收起片段追问">
          <X aria-hidden="true" size={16} />
        </Button>
      </header>

      {!sourceMessage ? (
        <EmptyState className="segment-followup-panel__empty">选择一条 AI 回复查看追问</EmptyState>
      ) : null}
      {sourceMessage && threads.length === 0 ? (
        <EmptyState className="segment-followup-panel__empty">
          选中回复中的文字后，可以在这里继续追问。
        </EmptyState>
      ) : null}
      {threads.length > 0 ? (
        <div className="segment-followup-panel__list">
          {threads.map((thread, index) => (
            <details
              className={`segment-followup-panel__item ${activeEntryKey === thread.key ? "is-active" : ""}`}
              key={thread.key}
              ref={(node) => {
                itemRefs.current[thread.key] = node;
              }}
              open={index === threads.length - 1 || thread.status === "pending"}
            >
              <summary className="segment-followup-panel__summary">
                <span className="segment-followup-panel__badge">片段</span>
                <span className="segment-followup-panel__source-text">{thread.originalText}</span>
                <span className={`segment-followup-panel__status segment-followup-panel__status--${thread.status}`}>
                  {followupStatusText(thread.status)}
                </span>
                <strong>{thread.turns[thread.turns.length - 1]?.question ?? "片段追问"}</strong>
                <small>{thread.turns.length} 轮对话</small>
                <button
                  aria-label="放大片段追问"
                  className="segment-followup-panel__expand"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setExpandedThreadKey(thread.key);
                  }}
                  title="放大片段追问"
                  type="button"
                >
                  <Maximize2 aria-hidden="true" size={14} />
                </button>
              </summary>
              <div className="segment-followup-panel__answer">
                <SegmentFollowupThreadPreview thread={thread} thoughts={thoughts} />
              </div>
            </details>
          ))}
        </div>
      ) : null}
      {expandedThread
        ? createPortal(
        <SegmentFollowupModal
          onClose={() => setExpandedThreadKey(null)}
          onDeleteMessage={onDeleteMessage}
          onOpenGraph={onOpenGraph}
          onStopGeneration={onStopGeneration}
          onSubmitFollowup={(question) => submitThreadFollowup(expandedThread, question)}
          thread={expandedThread}
          thoughts={thoughts}
        />,
        document.body,
      )
        : null}
    </aside>
  );
}

function AssistantMessageBody({
  activeSegmentId,
  message,
  onOpenSegment,
  thoughts,
  isWarmingUp = false,
}: {
  activeSegmentId?: string | null;
  message: DraftAssistantMessage;
  onOpenSegment?: (segmentId: string) => void;
  thoughts?: ChatThought[];
  isWarmingUp?: boolean;
}) {
  const isStreaming = message.isStreaming === true;
  const segments = message.segments ?? [];
  const hasSegments = segments.length > 0;
  const stepThoughts = groupThoughtsByStep(isStreaming ? thoughts ?? [] : message.thoughts);
  return (
    <>
      {hasSegments ? (
        <ChronologicalTimeline
          activeSegmentId={activeSegmentId}
          segments={segments}
          followupThreads={message.followupThreads}
          thoughtsByStep={stepThoughts}
          isStreaming={isStreaming}
          onOpenSegment={onOpenSegment}
          showWarmingUp={isWarmingUp}
        />
      ) : (
        <StreamingMarkdown
          activeSegmentId={activeSegmentId}
          content={message.content}
          followupThreads={message.followupThreads}
          onOpenSegment={onOpenSegment}
          streaming={isStreaming}
        />
      )}
      {!isStreaming && !hasSegments && message.thoughts?.length ? (
        <ThoughtRecap thoughts={message.thoughts} />
      ) : null}
    </>
  );
}

interface FollowupPanelTurn {
  assistantMessage: DraftAssistantMessage | null;
  fallbackAnswer?: string;
  key: string;
  question: string;
  status: "pending" | "answered" | "failed";
}

interface FollowupPanelThread {
  key: string;
  originalText: string;
  position: { start: number; end: number } | null;
  segmentId: string;
  turns: FollowupPanelTurn[];
  status: "pending" | "answered" | "failed";
}

function buildFollowupPanelThreads(
  sourceMessage: DraftAssistantMessage,
  messages: DraftAssistantMessage[],
): FollowupPanelThread[] {
  const threads = new Map<string, FollowupPanelThread>();
  const seenTurns = new Set<string>();

  function ensureThread(input: {
    originalText: string;
    position: { start: number; end: number } | null;
    segmentId: string;
  }): FollowupPanelThread {
    const existing = threads.get(input.segmentId);
    if (existing) {
      return existing;
    }
    const created: FollowupPanelThread = {
      key: input.segmentId,
      originalText: input.originalText,
      position: input.position,
      segmentId: input.segmentId,
      turns: [],
      status: "answered",
    };
    threads.set(input.segmentId, created);
    return created;
  }

  for (const message of messages) {
    if (message.role !== "user") {
      continue;
    }
    const payload = parseSegmentFollowupPayload(message.content);
    if (!payload || payload.source_message_id !== sourceMessage.id) {
      continue;
    }
    const assistant = messages.find(
      (candidate) => candidate.role === "assistant" && candidate.parent_id === message.id,
    ) ?? null;
    const segmentId = payload.segment_id ?? createSegmentId(payload.original_text);
    const thread = ensureThread({
      originalText: payload.original_text,
      position: payload.position ?? null,
      segmentId,
    });
    const turnKey = `${segmentId}::${normalizeText(payload.user_question)}`;
    seenTurns.add(turnKey);
    thread.turns.push({
      assistantMessage: assistant,
      key: `${message.id}-${assistant?.id ?? "pending"}`,
      question: payload.user_question,
      status: assistant?.status === "failed" ? "failed" : assistant ? "answered" : "pending",
    });
  }
  for (const thread of sourceMessage.followupThreads ?? []) {
    const panelThread = ensureThread({
      originalText: thread.original_text,
      position: thread.position,
      segmentId: thread.segment_id,
    });
    for (const followup of thread.followups) {
      const turnKey = `${thread.segment_id}::${normalizeText(followup.user_question)}`;
      if (seenTurns.has(turnKey)) {
        continue;
      }
      seenTurns.add(turnKey);
      panelThread.turns.push({
        assistantMessage: null,
        fallbackAnswer: followup.assistant_answer,
        key: followup.followup_id,
        question: followup.user_question,
        status: followup.status,
      });
    }
  }
  for (const thread of threads.values()) {
    thread.status = thread.turns.some((turn) => turn.status === "pending")
      ? "pending"
      : thread.turns.some((turn) => turn.status === "failed")
        ? "failed"
        : "answered";
  }
  return [...threads.values()];
}

function SegmentFollowupAnswer({
  showGraph = false,
  thoughts,
  turn,
  onOpenGraph,
}: {
  showGraph?: boolean;
  thoughts: ChatThought[];
  turn: FollowupPanelTurn;
  onOpenGraph?: (message: DraftAssistantMessage) => void;
}) {
  if (turn.assistantMessage) {
    return (
      <div className="segment-followup-turn__assistant">
        <AssistantMessageBody
          message={turn.assistantMessage}
          thoughts={turn.assistantMessage.isStreaming ? thoughts : turn.assistantMessage.thoughts}
        />
        {showGraph && turn.assistantMessage.turn_id ? (
          <Button
            aria-label="查看这轮追问 graph"
            onClick={() => onOpenGraph?.(turn.assistantMessage!)}
            size="icon"
            title="查看这轮追问 graph"
          >
            <GitBranch aria-hidden="true" size={15} />
          </Button>
        ) : null}
      </div>
    );
  }
  if (turn.status === "pending") {
    return <TypingIndicator compact />;
  }
  if (turn.fallbackAnswer) {
    return <MarkdownMessage content={turn.fallbackAnswer} fallback="" />;
  }
  return <p className="segment-followup-panel__pending">这条追问还没有回复。</p>;
}

function SegmentFollowupThreadPreview({
  thread,
  thoughts,
}: {
  thread: FollowupPanelThread;
  thoughts: ChatThought[];
}) {
  return (
    <div className="segment-followup-thread-turns">
      {thread.turns.map((turn, index) => (
        <section className="segment-followup-turn" key={turn.key}>
          <p className="segment-followup-turn__question">
            <span>Q{index + 1}</span>
            {turn.question}
          </p>
          <div className="segment-followup-turn__answer">
            <SegmentFollowupAnswer thoughts={thoughts} turn={turn} />
          </div>
        </section>
      ))}
    </div>
  );
}

function SegmentFollowupContinueComposer({
  disabled,
  onStop,
  onSubmit,
}: {
  disabled?: boolean;
  onStop?: () => void;
  onSubmit: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");

  function submit() {
    if (!question.trim() || disabled) {
      return;
    }
    onSubmit(question);
    setQuestion("");
  }

  return (
    <div className="segment-followup-continue">
      <textarea
        disabled={disabled}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder="继续追问这个片段..."
        value={question}
      />
      <button
        aria-label={disabled ? "中断生成" : "继续追问"}
        disabled={!disabled && !question.trim()}
        onClick={disabled ? onStop : submit}
        title={disabled ? "中断生成" : "继续追问"}
        type="button"
      >
        {disabled ? <Square aria-hidden="true" size={13} /> : <Send aria-hidden="true" size={14} />}
      </button>
    </div>
  );
}

function SegmentFollowupModal({
  onClose,
  onDeleteMessage,
  onOpenGraph,
  onStopGeneration,
  onSubmitFollowup,
  thread,
  thoughts,
}: {
  onClose: () => void;
  onDeleteMessage: (message: DraftAssistantMessage) => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onStopGeneration: () => void;
  onSubmitFollowup: (question: string) => void;
  thread: FollowupPanelThread;
  thoughts: ChatThought[];
}) {
  const [turnMenu, setTurnMenu] = useState<{
    anchor: { x: number; y: number };
    message: DraftAssistantMessage;
  } | null>(null);

  useEffect(() => {
    if (!turnMenu) {
      return;
    }
    function closeTurnMenu(event: PointerEvent) {
      if (event.target instanceof HTMLElement && event.target.closest(".segment-followup-menu")) {
        return;
      }
      setTurnMenu(null);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setTurnMenu(null);
      }
    }
    document.addEventListener("pointerdown", closeTurnMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeTurnMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [turnMenu]);

  return (
    <div className="segment-followup-modal-backdrop" role="presentation">
      <section className="segment-followup-modal" role="dialog" aria-label="放大片段追问" aria-modal="true">
        <header className="segment-followup-modal__header">
          <div>
            <span>片段追问</span>
            <h3>{thread.turns[thread.turns.length - 1]?.question ?? "片段小会话"}</h3>
          </div>
          <Button aria-label="关闭放大窗口" onClick={onClose} size="icon" title="关闭">
            <X aria-hidden="true" size={16} />
          </Button>
        </header>
        <div className="segment-followup-modal__source">
          <span>片段</span>
          <q>{thread.originalText}</q>
        </div>
        <div className="segment-followup-modal__body">
          <div className="segment-followup-modal__turns">
            {thread.turns.map((turn, index) => (
              <section
                className="segment-followup-modal__turn"
                key={turn.key}
                onContextMenu={(event) => {
                  if (!turn.assistantMessage || turn.assistantMessage.isStreaming === true) {
                    return;
                  }
                  event.preventDefault();
                  setTurnMenu({
                    anchor: clampFloatingAnchor(
                      { x: event.clientX + 8, y: event.clientY + 8 },
                      { width: 132, height: 46 },
                    ),
                    message: turn.assistantMessage,
                  });
                }}
              >
                {turn.assistantMessage && turn.assistantMessage.isStreaming !== true ? (
                  <button
                    aria-label="删除这轮追问"
                    className="segment-followup-modal__delete-turn"
                    onClick={() => onDeleteMessage(turn.assistantMessage!)}
                    title="删除这轮追问"
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={14} />
                  </button>
                ) : null}
                <p className="segment-followup-turn__question">
                  <span>Q{index + 1}</span>
                  {turn.question}
                </p>
                <div className="segment-followup-turn__answer">
                  <SegmentFollowupAnswer
                    onOpenGraph={onOpenGraph}
                    showGraph
                    thoughts={thoughts}
                    turn={turn}
                  />
                </div>
              </section>
            ))}
          </div>
        </div>
        <SegmentFollowupContinueComposer
          disabled={thread.status === "pending"}
          onStop={onStopGeneration}
          onSubmit={onSubmitFollowup}
        />
        {turnMenu ? (
          <SegmentFollowupMenu
            anchor={turnMenu.anchor}
            canDelete
            canFollowup={false}
            onDelete={() => {
              onDeleteMessage(turnMenu.message);
              setTurnMenu(null);
            }}
            onOpen={() => undefined}
          />
        ) : null}
      </section>
    </div>
  );
}

function followupStatusText(status: FollowupPanelThread["status"]): string {
  if (status === "pending") {
    return "生成中";
  }
  if (status === "failed") {
    return "失败";
  }
  return "已回复";
}

function parseSegmentFollowupPayload(content: string): SegmentFollowupRequest | null {
  try {
    const payload = JSON.parse(content) as Partial<SegmentFollowupRequest> & { type?: string };
    if (
      payload.type !== "segment_followup" ||
      typeof payload.source_message_id !== "number" ||
      typeof payload.original_text !== "string" ||
      typeof payload.user_question !== "string"
    ) {
      return null;
    }
    return {
      source_message_id: payload.source_message_id,
      segment_id: typeof payload.segment_id === "string" ? payload.segment_id : null,
      original_text: payload.original_text,
      user_question: payload.user_question,
      position: payload.position ?? null,
    };
  } catch {
    return null;
  }
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

function resolveTextPosition(content: string, selectedText: string): { start: number; end: number } | null {
  const start = normalizeText(content).indexOf(normalizeText(selectedText));
  if (start < 0) {
    return null;
  }
  return { start, end: start + selectedText.length };
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
    const marked = normalizeText(thread.original_text);
    if (marked.includes(selected) || selected.includes(marked)) {
      return true;
    }
    if (!position || !thread.position) {
      return false;
    }
    return position.start < thread.position.end && position.end > thread.position.start;
  });
}

function hasMessageFollowups(message: DraftAssistantMessage): boolean {
  return (message.followupThreads ?? []).some((thread) => thread.followups.length > 0);
}

function normalizeText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function createSegmentId(text: string): string {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `seg-${(hash >>> 0).toString(16)}`;
}

function findEarliestThreadMatch(
  text: string,
  threads: SegmentFollowupThread[],
): { index: number; thread: SegmentFollowupThread } | null {
  let best: { index: number; thread: SegmentFollowupThread } | null = null;
  for (const thread of threads) {
    const index = text.indexOf(thread.original_text);
    if (index < 0) {
      continue;
    }
    if (
      !best ||
      index < best.index ||
      (index === best.index && thread.original_text.length > best.thread.original_text.length)
    ) {
      best = { index, thread };
    }
  }
  return best;
}

interface UserInputInterruptCardProps {
  disabled: boolean;
  message: DraftAssistantMessage;
  request: UserInputRequest;
  onSubmit: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
}

function UserInputInterruptCard({
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
        <Button disabled={disabled || !canSubmit} onClick={handleFinalSubmit} size="sm" type="button" variant="primary">
          <Check aria-hidden="true" size={15} />
          Submit
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

function TypingIndicator({
  compact = false,
  variant = "initial",
}: {
  compact?: boolean;
  variant?: "initial" | "tail";
}) {
  return (
    <div
      className={[
        "chat-typing-indicator",
        compact ? "chat-typing-indicator--compact" : "",
        variant === "tail" ? "chat-typing-indicator--tail" : "",
      ].filter(Boolean).join(" ")}
      aria-live="polite"
      aria-label="正在生成回复"
    >
      <div className="chat-typing-indicator__header">
        <PulseGlyph active />
        <span className="chat-typing-indicator__label">
          <span className="chat-typing-indicator__title">Thinking</span>
          <span className="chat-typing-indicator__sep">·</span>
          <VerbRotator />
        </span>
      </div>
      <div className="chat-typing-indicator__dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

function StreamingMarkdown({
  activeSegmentId,
  content,
  followupThreads,
  onOpenSegment,
  streaming,
}: {
  activeSegmentId?: string | null;
  content: string;
  followupThreads?: SegmentFollowupThread[];
  onOpenSegment?: (segmentId: string) => void;
  streaming: boolean;
}) {
  return (
    <div className={`chat-answer-stream ${streaming ? "is-streaming" : ""}`}>
      <MarkedMarkdownMessage
        activeSegmentId={activeSegmentId}
        content={content}
        followupThreads={followupThreads}
        onOpenSegment={onOpenSegment}
      />
      {streaming && content.length > 0 ? <span className="chat-stream-caret" aria-hidden="true" /> : null}
    </div>
  );
}

function MarkedMarkdownMessage({
  activeSegmentId,
  content,
  followupThreads,
  onOpenSegment,
}: {
  activeSegmentId?: string | null;
  content: string;
  followupThreads?: SegmentFollowupThread[];
  onOpenSegment?: (segmentId: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = rootRef.current;
    const threads = followupThreads?.filter((thread) => thread.original_text.trim()) ?? [];
    if (!root) {
      return;
    }

    root.querySelectorAll<HTMLButtonElement>(".segment-followup-mark").forEach((mark) => {
      mark.replaceWith(document.createTextNode(mark.textContent ?? ""));
    });

    if (threads.length === 0) {
      return;
    }

    const listeners: Array<{ element: HTMLButtonElement; handler: (event: MouseEvent) => void }> = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || !node.textContent?.trim()) {
          return NodeFilter.FILTER_REJECT;
        }
        if (parent.closest("pre, code, button, .segment-followup-mark")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes: Text[] = [];
    let node = walker.nextNode();
    while (node) {
      nodes.push(node as Text);
      node = walker.nextNode();
    }

    for (const textNode of nodes) {
      let remaining = textNode.textContent ?? "";
      const pieces: Array<string | SegmentFollowupThread> = [];
      while (remaining) {
        const match = findEarliestThreadMatch(remaining, threads);
        if (!match) {
          pieces.push(remaining);
          break;
        }
        if (match.index > 0) {
          pieces.push(remaining.slice(0, match.index));
        }
        pieces.push(match.thread);
        remaining = remaining.slice(match.index + match.thread.original_text.length);
      }
      if (pieces.length <= 1) {
        continue;
      }
      const fragment = document.createDocumentFragment();
      for (const piece of pieces) {
        if (typeof piece === "string") {
          fragment.append(document.createTextNode(piece));
          continue;
        }
        const button = document.createElement("button");
        button.className = [
          "segment-followup-mark",
          activeSegmentId === piece.segment_id ? "is-active" : "",
        ].filter(Boolean).join(" ");
        button.dataset.segmentId = piece.segment_id;
        button.type = "button";
        button.textContent = piece.original_text;
        button.title = "查看这个片段的追问";
        const handler = (event: MouseEvent) => {
          event.stopPropagation();
          onOpenSegment?.(piece.segment_id);
        };
        button.addEventListener("click", handler);
        listeners.push({ element: button, handler });
        fragment.append(button);
      }
      textNode.replaceWith(fragment);
    }

    return () => {
      for (const { element, handler } of listeners) {
        element.removeEventListener("click", handler);
      }
    };
  }, [activeSegmentId, content, followupThreads, onOpenSegment]);

  return (
    <div ref={rootRef}>
      <MarkdownMessage content={content} fallback="" />
    </div>
  );
}

interface ChronologicalTimelineProps {
  activeSegmentId?: string | null;
  followupThreads?: SegmentFollowupThread[];
  segments: MessageSegment[];
  thoughtsByStep: Map<number, ChatThought[]>;
  isStreaming: boolean;
  onOpenSegment?: (segmentId: string) => void;
  showWarmingUp: boolean;
}

/**
 * 串行化展示一条 assistant 消息：按 step_index 升序，
 * 每个 segment 顺序渲染 thought → text → tool cards。
 * 这样工具卡片紧贴产生它的那段叙述，避免“工具放最上、文字放最下”的割裂体验。
 */
function ChronologicalTimeline({
  activeSegmentId,
  followupThreads,
  segments,
  thoughtsByStep,
  isStreaming,
  onOpenSegment,
  showWarmingUp,
}: ChronologicalTimelineProps) {
  const lastIndex = segments.length - 1;
  // 把所有未挂到 segment 的 thought（一般是 step_index=0 的全局/兜底 thought）放最前面。
  const orphanThoughts = orphansBeforeSegments(thoughtsByStep, segments);
  const showThinkingTail = isStreaming && !showWarmingUp && shouldShowThinkingTail(segments);
  return (
    <div className="chat-segment-timeline">
      {showWarmingUp ? <TypingIndicator compact /> : null}
      {orphanThoughts.length > 0 ? <SegmentThoughts thoughts={orphanThoughts} /> : null}
      {segments.map((segment, idx) => {
        const isLast = idx === lastIndex;
        const segmentStreaming = isStreaming && isLast && segment.tools.length === 0;
        const stepThoughts = thoughtsByStep.get(segment.step_index) ?? [];
        return (
          <div className="chat-segment" key={`step-${segment.step_index}`}>
            {stepThoughts.length > 0 ? <SegmentThoughts thoughts={stepThoughts} /> : null}
            {segment.text.length > 0 ? (
              <StreamingMarkdown
                activeSegmentId={activeSegmentId}
                content={segment.text}
                followupThreads={followupThreads}
                onOpenSegment={onOpenSegment}
                streaming={segmentStreaming}
              />
            ) : null}
            {segment.tools.length > 0 ? (
              <div className="chat-segment__tools">
                {segment.tools.map((tool) => (
                  <ToolCallCard
                    key={tool.tool_call_id || `${tool.tool_name}-${segment.step_index}`}
                    toolName={tool.tool_name}
                    args={tool.arguments}
                    summary={tool.result_summary || tool.message}
                    status={toolCardStatus(tool)}
                  />
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
      {showThinkingTail ? <TypingIndicator compact variant="tail" /> : null}
    </div>
  );
}

function hasVisibleAssistantWork(
  segments: MessageSegment[],
  thoughts: ChatThought[],
  content: string,
): boolean {
  if (content.trim().length > 0) {
    return true;
  }
  if (thoughts.some((thought) => thought.title.trim() || thought.summary.trim())) {
    return true;
  }
  return segments.some(
    (segment) => segment.text.trim().length > 0 || segment.tools.length > 0,
  );
}

function shouldShowThinkingTail(segments: MessageSegment[]): boolean {
  const lastSegment = segments.length > 0 ? segments[segments.length - 1] : undefined;
  if (!lastSegment) {
    return false;
  }
  // 最终回答 token 正在流出时，光标已经承担“正在生成”的反馈；这里避免重复动画。
  if (lastSegment.text.trim().length > 0 && lastSegment.tools.length === 0) {
    return false;
  }
  return true;
}

function orphansBeforeSegments(
  thoughtsByStep: Map<number, ChatThought[]>,
  segments: MessageSegment[],
): ChatThought[] {
  const segmentSteps = new Set(segments.map((segment) => segment.step_index));
  const orphans: ChatThought[] = [];
  for (const [step, list] of thoughtsByStep) {
    if (!segmentSteps.has(step)) {
      orphans.push(...list);
    }
  }
  return orphans;
}

function toolCardStatus(tool: ToolInvocation): "completed" | "failed" | "running" {
  // running 卡片的 ok/blocked 此时还没有真实意义（工具还没跑完），优先看 running 标志。
  if (tool.running) {
    return "running";
  }
  if (tool.blocked || !tool.ok) {
    return "failed";
  }
  return "completed";
}

function SegmentThoughts({ thoughts }: { thoughts: ChatThought[] }) {
  return (
    <ul className="chat-segment-thoughts" aria-live="polite">
      {thoughts.map((thought, index) => {
        const status = normalizeStatus(thought.status);
        return (
          <li
            className={`chat-segment-thoughts__item chat-segment-thoughts__item--${status}`}
            key={`${thought.id}-${index}`}
          >
            <span className="chat-segment-thoughts__icon" aria-hidden="true">
              {statusGlyph(status)}
            </span>
            <div className="chat-segment-thoughts__body">
              <span className="chat-segment-thoughts__title">{thought.title}</span>
              {thought.summary ? (
                <span className="chat-segment-thoughts__summary"> {thought.summary}</span>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

type ThoughtStatus = "running" | "completed" | "failed" | "interrupted";

function normalizeStatus(value: string | undefined): ThoughtStatus {
  if (value === "running" || value === "completed" || value === "failed" || value === "interrupted") {
    return value;
  }
  return "completed";
}

function statusGlyph(status: ThoughtStatus) {
  switch (status) {
    case "running":
      return <PulseGlyph active />;
    case "failed":
      return <span className="chat-step-icon chat-step-icon--failed">✕</span>;
    case "interrupted":
      return <span className="chat-step-icon chat-step-icon--interrupted">⏸</span>;
    case "completed":
    default:
      return <span className="chat-step-icon chat-step-icon--completed">✓</span>;
  }
}

/**
 * 历史消息没有 segments（DB 落库的旧消息只有 thoughts 数组）时的折叠展示。
 * 新版 segments 直接内联展开，无需折叠。
 */
function ThoughtRecap({ thoughts }: { thoughts: ChatThought[] }) {
  if (thoughts.length === 0) {
    return null;
  }
  return (
    <details className="chat-thought-recap">
      <summary>
        <span className="chat-thought-mark" aria-hidden="true">
          ∴
        </span>
        <span className="chat-thought-recap__label">查看思考过程</span>
        <small>{thoughts.length} 个步骤</small>
      </summary>
      <ol className="chat-timeline">
        {thoughts.map((thought, index) => {
          const status = normalizeStatus(thought.status);
          return (
            <li
              className={`chat-timeline__step chat-timeline__step--${status}`}
              key={`${thought.id}-${index}`}
            >
              <span className="chat-timeline__node" aria-hidden="true">
                {statusGlyph(status)}
              </span>
              <div className="chat-timeline__body">
                <div className="chat-timeline__title">{thought.title}</div>
                <p className="chat-timeline__summary">{thought.summary}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </details>
  );
}
