import { GitBranch, Maximize2, MessageCircleQuestion, Send, Square, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Button, EmptyState } from "../../shared/ui";
import { UserInputInterruptCard } from "../chat/MessageList";
import { MarkdownMessage } from "../chat/MarkdownMessage";
import type {
  ChatThought,
  DraftAssistantMessage,
  SegmentFollowupRequest,
  UserInputAnswer,
} from "../chat/types";
import {
  ChatMessageBody,
  hasVisibleAssistantWork,
  TypingIndicator,
} from "./ChatMessageBody";

export function SegmentFollowupPanel({
  activeSegmentId,
  messages,
  onClose,
  onDeleteMessage,
  onOpenGraph,
  onOpenSegment,
  onSegmentFollowup,
  onSubmitUserInput,
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
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
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
                <SegmentFollowupThreadPreview
                  thread={thread}
                  thoughts={thoughts}
                  onSubmitUserInput={onSubmitUserInput}
                />
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
              onSubmitUserInput={onSubmitUserInput}
              thread={expandedThread}
              thoughts={thoughts}
            />,
            document.body,
          )
        : null}
    </aside>
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
      status: assistant ? followupTurnStatus(assistant) : "pending",
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

function followupTurnStatus(message: DraftAssistantMessage): FollowupPanelTurn["status"] {
  if (message.status === "failed") {
    return "failed";
  }
  if (message.status === "interrupted" || message.status === "streaming" || message.isStreaming || message.pending_interrupt) {
    return "pending";
  }
  return "answered";
}

function SegmentFollowupAnswer({
  showGraph = false,
  thoughts,
  turn,
  onOpenGraph,
  onSubmitUserInput,
}: {
  showGraph?: boolean;
  thoughts: ChatThought[];
  turn: FollowupPanelTurn;
  onOpenGraph?: (message: DraftAssistantMessage) => void;
  onSubmitUserInput?: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
}) {
  if (turn.assistantMessage) {
    const followupMessage = turn.assistantMessage;
    const isStreaming = followupMessage.isStreaming === true;
    const effectiveThoughts = isStreaming
      ? thoughts
      : followupMessage.thoughts ?? [];
    const hasVisibleWork = hasVisibleAssistantWork(
      followupMessage.segments ?? [],
      effectiveThoughts,
      followupMessage.content,
    );
    const isWarmingUp = isStreaming && !hasVisibleWork;
    return (
      <div className="segment-followup-turn__assistant">
        {isWarmingUp ? <TypingIndicator compact /> : null}
        <ChatMessageBody
          message={followupMessage}
          thoughts={effectiveThoughts}
          isWarmingUp={isWarmingUp}
        />
        {followupMessage.pending_interrupt && onSubmitUserInput ? (
          <UserInputInterruptCard
            disabled={isStreaming}
            message={followupMessage}
            request={followupMessage.pending_interrupt}
            onSubmit={onSubmitUserInput}
          />
        ) : null}
        {showGraph && followupMessage.turn_id ? (
          <Button
            aria-label="查看这轮追问 graph"
            onClick={() => onOpenGraph?.(followupMessage)}
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
  onSubmitUserInput,
}: {
  thread: FollowupPanelThread;
  thoughts: ChatThought[];
  onSubmitUserInput?: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
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
            <SegmentFollowupAnswer thoughts={thoughts} turn={turn} onSubmitUserInput={onSubmitUserInput} />
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
  onSubmitUserInput,
  thread,
  thoughts,
}: {
  onClose: () => void;
  onDeleteMessage: (message: DraftAssistantMessage) => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onStopGeneration: () => void;
  onSubmitFollowup: (question: string) => void;
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
  thread: FollowupPanelThread;
  thoughts: ChatThought[];
}) {
  const [turnMenu, setTurnMenu] = useState<{
    anchor: { x: number; y: number };
    message: DraftAssistantMessage;
  } | null>(null);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);

  const scrollAnchor = useMemo(() => {
    const lastTurn = thread.turns[thread.turns.length - 1];
    if (!lastTurn) {
      return `${thread.turns.length}:empty`;
    }
    const msg = lastTurn.assistantMessage;
    if (!msg) {
      return `${thread.turns.length}:pending:${thoughts.length}`;
    }
    const segLen = (msg.segments ?? []).reduce(
      (sum, segment) => sum + segment.text.length + segment.tools.length,
      0,
    );
    return `${thread.turns.length}:${msg.id}:${msg.content.length}:${segLen}:${thoughts.length}`;
  }, [thread.turns, thoughts.length]);

  useEffect(() => {
    if (!shouldAutoScroll) {
      return;
    }
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [scrollAnchor, shouldAutoScroll]);

  const prevTurnCountRef = useRef(thread.turns.length);
  useEffect(() => {
    if (thread.turns.length > prevTurnCountRef.current) {
      setShouldAutoScroll(true);
    }
    prevTurnCountRef.current = thread.turns.length;
  }, [thread.turns.length]);

  const handleBodyScroll = () => {
    const el = bodyRef.current;
    if (!el) {
      return;
    }
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShouldAutoScroll(distance < 96);
  };

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
    <div
      className="segment-followup-modal-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
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
        <div className="segment-followup-modal__body" ref={bodyRef} onScroll={handleBodyScroll}>
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
                    onSubmitUserInput={onSubmitUserInput}
                    showGraph
                    thoughts={thoughts}
                    turn={turn}
                  />
                </div>
              </section>
            ))}
          </div>
          <div ref={endRef} />
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
