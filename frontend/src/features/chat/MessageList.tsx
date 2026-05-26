import { Check, GitBranch } from "lucide-react";
import { useState, type RefObject } from "react";

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
  UserInputQuestion,
  UserInputRequest,
} from "./types";

interface MessageListProps {
  endRef: RefObject<HTMLDivElement | null>;
  listRef?: RefObject<HTMLDivElement | null>;
  messages: DraftAssistantMessage[];
  onScroll?: () => void;
  onOpenGraph: (message: DraftAssistantMessage) => void;
  onSubmitUserInput: (message: DraftAssistantMessage, answer: UserInputAnswer) => void;
  thoughts?: ChatThought[];
}

export function MessageList({
  endRef,
  listRef,
  messages,
  onScroll,
  onOpenGraph,
  onSubmitUserInput,
  thoughts = [],
}: MessageListProps) {
  return (
    <div className="chat-message-list" onScroll={onScroll} ref={listRef}>
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
