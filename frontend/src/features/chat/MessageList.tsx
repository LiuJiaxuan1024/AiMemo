import { Check, GitBranch } from "lucide-react";
import { useMemo, useState, type RefObject } from "react";

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
  ToolInvocation,
  UserInputAnswer,
  UserInputRequest,
} from "./types";

interface MessageListProps {
  endRef: RefObject<HTMLDivElement | null>;
  messages: DraftAssistantMessage[];
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
  thoughts?: ChatThought[];
}

export function MessageList({
  endRef,
  messages,
  onOpenGraph,
  onSubmitUserInput,
  thoughts = [],
}: MessageListProps) {
  return (
    <div className="chat-message-list">
      {messages.length === 0 ? (
        <EmptyState className="chat-empty">向 AiMemo 提一个关于笔记或记忆的问题</EmptyState>
      ) : null}
      {messages.map((message) => {
        const isAssistant = message.role === "assistant";
        const isStreaming = isAssistant && message.isStreaming === true;
        // 优先使用 segments：streaming 期间由 streamingStore 实时拼装；done 后从落库消息恢复。
        const segments = message.segments ?? [];
        const stepThoughts = groupThoughtsByStep(
          isStreaming ? thoughts : message.thoughts,
        );
        const hasSegments = segments.length > 0;
        const hasVisibleWork = hasVisibleAssistantWork(segments, thoughts, message.content);
        const isAssistantWarmingUp =
          isStreaming &&
          !hasVisibleWork;

        return (
          <article className={`chat-message ${message.role}`} key={message.id}>
            <div className="chat-message-bubble">
              <div className="chat-message-content">
                {isAssistantWarmingUp ? <TypingIndicator /> : null}
                {isAssistant ? (
                  hasSegments ? (
                    <ChronologicalTimeline
                      segments={segments}
                      thoughtsByStep={stepThoughts}
                      isStreaming={isStreaming}
                      showWarmingUp={isAssistantWarmingUp}
                    />
                  ) : (
                    <StreamingMarkdown content={message.content} streaming={isStreaming} />
                  )
                ) : (
                  <p>{message.content}</p>
                )}
                {/* done 后的旧版兜底：没有 segments 但有 thoughts，仍折叠展示思考链。 */}
                {isAssistant && !isStreaming && !hasSegments && message.thoughts?.length ? (
                  <ThoughtRecap thoughts={message.thoughts} />
                ) : null}
                {isAssistant && message.pending_interrupt ? (
                  <UserInputInterruptCard
                    disabled={message.status !== "interrupted"}
                    message={message}
                    request={message.pending_interrupt}
                    onSubmit={onSubmitUserInput}
                  />
                ) : null}
              </div>
              {isAssistant && message.turn_id ? (
                <Button
                  aria-label="查看本轮 graph"
                  onClick={() => onOpenGraph(message)}
                  size="icon"
                  title="查看本轮 graph"
                >
                  <GitBranch aria-hidden="true" size={16} />
                </Button>
              ) : null}
            </div>
          </article>
        );
      })}
      <div ref={endRef} />
    </div>
  );
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
  const mode = request.selection_mode === "multiple" ? "multiple" : "single";
  const firstOptionId = request.options[0]?.id ?? "";
  const [selectedIds, setSelectedIds] = useState<string[]>(firstOptionId ? [firstOptionId] : []);
  const [otherText, setOtherText] = useState("");

  const selectedOptions = useMemo(
    () => request.options.filter((option) => selectedIds.includes(option.id)),
    [request.options, selectedIds],
  );
  const includesOther = selectedIds.includes("other");
  const canSubmit =
    selectedOptions.length > 0 || (request.allow_other && (includesOther || otherText.trim().length > 0));

  function toggleOption(optionId: string) {
    if (disabled) {
      return;
    }
    if (mode === "single") {
      setSelectedIds([optionId]);
      return;
    }
    setSelectedIds((current) =>
      current.includes(optionId)
        ? current.filter((item) => item !== optionId)
        : [...current, optionId],
    );
  }

  function handleOtherFocus() {
    if (!request.allow_other || selectedIds.includes("other")) {
      return;
    }
    setSelectedIds((current) => (mode === "single" ? ["other"] : [...current, "other"]));
  }

  function handleSubmit() {
    if (disabled || !canSubmit) {
      return;
    }
    const ids = [...selectedIds];
    const trimmedOther = otherText.trim();
    if (trimmedOther && !ids.includes("other")) {
      ids.push("other");
    }
    const normalValues = request.options
      .filter((option) => ids.includes(option.id))
      .map((option) => option.value || option.label)
      .filter(Boolean);
    const answerParts = [...normalValues];
    if (trimmedOther) {
      answerParts.push(trimmedOther);
    }
    const answer = answerParts.join("\n").trim();
    onSubmit(message, {
      request_id: request.request_id,
      selected_option_id: ids[0] ?? "",
      selected_option_ids: ids,
      answer,
      other_text: trimmedOther,
    });
  }

  function handleOtherClick() {
    if (!request.allow_other || disabled) {
      return;
    }
    if (!selectedIds.includes("other")) {
      setSelectedIds((current) => (mode === "single" ? ["other"] : [...current, "other"]));
    }
  }

  return (
    <div className="chat-interrupt-card" role="group" aria-label={request.question}>
      <p className="chat-interrupt-card__question">{request.question}</p>
      <div className="chat-interrupt-options">
        {request.options.map((option, index) => {
          const checked = selectedIds.includes(option.id);
          return (
            <button
              className={`chat-interrupt-option ${checked ? "selected" : ""}`}
              key={option.id}
              onClick={() => toggleOption(option.id)}
              type="button"
            >
              <input
                aria-hidden="true"
                checked={checked}
                disabled={disabled}
                name={`interrupt-${request.request_id}`}
                onChange={() => undefined}
                tabIndex={-1}
                type={mode === "multiple" ? "checkbox" : "radio"}
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
        {request.allow_other ? (
          <button
            className={`chat-interrupt-option chat-interrupt-option--other ${includesOther ? "selected" : ""}`}
            onClick={handleOtherClick}
            type="button"
          >
            <input
              aria-hidden="true"
              checked={includesOther}
              disabled={disabled}
              name={`interrupt-${request.request_id}`}
              onChange={() => undefined}
              tabIndex={-1}
              type={mode === "multiple" ? "checkbox" : "radio"}
            />
            <span className="chat-interrupt-option__mark" aria-hidden="true" />
            <span className="chat-interrupt-option__body chat-interrupt-option__body--other">
              <span className="chat-interrupt-option__other-label">其他</span>
              <input
                className="chat-interrupt-option__inline-input"
                disabled={disabled}
                onChange={(event) => setOtherText(event.target.value)}
                onFocus={handleOtherFocus}
                placeholder={request.other_option?.placeholder || "在这里补充你的答案"}
                value={otherText}
              />
            </span>
          </button>
        ) : null}
      </div>
      <Button disabled={disabled || !canSubmit} onClick={handleSubmit} size="sm" type="button" variant="primary">
        <Check aria-hidden="true" size={15} />
        Submit
      </Button>
    </div>
  );
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

function StreamingMarkdown({ content, streaming }: { content: string; streaming: boolean }) {
  return (
    <div className={`chat-answer-stream ${streaming ? "is-streaming" : ""}`}>
      <MarkdownMessage content={content} fallback="" />
      {streaming && content.length > 0 ? <span className="chat-stream-caret" aria-hidden="true" /> : null}
    </div>
  );
}

interface ChronologicalTimelineProps {
  segments: MessageSegment[];
  thoughtsByStep: Map<number, ChatThought[]>;
  isStreaming: boolean;
  showWarmingUp: boolean;
}

/**
 * 串行化展示一条 assistant 消息：按 step_index 升序，
 * 每个 segment 顺序渲染 thought → text → tool cards。
 * 这样工具卡片紧贴产生它的那段叙述，避免“工具放最上、文字放最下”的割裂体验。
 */
function ChronologicalTimeline({
  segments,
  thoughtsByStep,
  isStreaming,
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
              <StreamingMarkdown content={segment.text} streaming={segmentStreaming} />
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
