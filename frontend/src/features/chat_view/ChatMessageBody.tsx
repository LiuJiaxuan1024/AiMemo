import { Check, Send, X } from "lucide-react";
import { memo, useEffect, useRef, useState, type ReactNode } from "react";

import { MarkdownMessage } from "../chat/MarkdownMessage";
import { PulseGlyph } from "../chat/PulseGlyph";
import { groupThoughtsByStep } from "../chat/streamingStore";
import { ToolCallCard } from "../chat/ToolCallCard";
import { VerbRotator } from "../chat/VerbRotator";
import type {
  ChatThought,
  CommandResult,
  DraftAssistantMessage,
  MessageSegment,
  SegmentFollowupThread,
  ToolInvocation,
} from "../chat/types";

const REQUEST_USER_INPUT_TOOL_NAME = "request_user_input";

export const ChatMessageBody = memo(function ChatMessageBody({
  activeSegmentId,
  commandActionsDisabled = false,
  message,
  onExecuteCommandSuggestion,
  onOpenSegment,
  thoughts,
  isWarmingUp = false,
}: {
  activeSegmentId?: string | null;
  commandActionsDisabled?: boolean;
  message: DraftAssistantMessage;
  onExecuteCommandSuggestion?: (command: string) => Promise<void> | void;
  onOpenSegment?: (segmentId: string) => void;
  thoughts?: ChatThought[];
  isWarmingUp?: boolean;
}) {
  const isStreaming = message.isStreaming === true;
  const commandResult = parseCommandResult(message.content);
  const segments = message.segments ?? [];
  const hasSegments = segments.length > 0;
  const stepThoughts = groupThoughtsByStep(isStreaming ? thoughts ?? [] : message.thoughts);
  if (commandResult && !isStreaming) {
    return (
      <CommandResultCard
        disabled={commandActionsDisabled}
        onExecuteCommandSuggestion={onExecuteCommandSuggestion}
        result={commandResult}
      />
    );
  }
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
});

function CommandResultCard({
  disabled = false,
  onExecuteCommandSuggestion,
  result,
}: {
  disabled?: boolean;
  onExecuteCommandSuggestion?: (command: string) => Promise<void> | void;
  result: CommandResult;
}) {
  const statusLabel =
    result.status === "success"
      ? "已完成"
      : result.status === "noop"
        ? "无变更"
        : result.status === "failed"
          ? "失败"
          : "待处理";
  const [executingSuggestion, setExecutingSuggestion] = useState<string | null>(null);
  const [selectedSpaceIds, setSelectedSpaceIds] = useState<number[]>([]);
  const details = Array.isArray(result.details) ? result.details : [];
  const suggestions = result.status === "needs_input" && Array.isArray(result.suggestions) ? result.suggestions : [];
  const multiSelectPrefix = resolveKnowledgeMountMultiSelectPrefix(result);
  const multiSelectDetails = multiSelectPrefix
    ? details
        .map((detail) => ({ ...detail, space_id: detailSpaceId(detail) }))
        .filter((detail): detail is typeof detail & { space_id: number } => detail.space_id != null)
    : [];
  const commandSuggestions = multiSelectDetails.length > 0
    ? []
    : suggestions.filter((suggestion) => suggestion.trim().startsWith("/"));
  const textSuggestions = suggestions.filter((suggestion) => !suggestion.trim().startsWith("/"));

  useEffect(() => {
    setSelectedSpaceIds([]);
  }, [result.command, result.command_id]);

  const multiSelectSubmitCommand =
    multiSelectPrefix && selectedSpaceIds.length > 0
      ? `${multiSelectPrefix} ${selectedSpaceIds.join(",")}`
      : "";

  function toggleSelectedSpace(spaceId: number) {
    setSelectedSpaceIds((current) =>
      current.includes(spaceId)
        ? current.filter((item) => item !== spaceId)
        : [...current, spaceId],
    );
  }

  return (
    <section className={`chat-command-result is-${result.status}`}>
      <header>
        <span className="chat-command-result__status">
          {result.status === "failed" ? (
            <X aria-hidden="true" size={14} />
          ) : result.status === "needs_input" ? (
            <Send aria-hidden="true" size={14} />
          ) : (
            <Check aria-hidden="true" size={14} />
          )}
          {statusLabel}
        </span>
        <code>{result.command}</code>
      </header>
      <p>{result.message}</p>
      {multiSelectDetails.length > 0 ? (
        <div className="chat-interrupt-options chat-command-result__multi-select" aria-label="知识空间多选">
          {multiSelectDetails.map((detail) => {
            const checked = selectedSpaceIds.includes(detail.space_id);
            return (
              <button
                aria-pressed={checked}
                className={`chat-interrupt-option chat-command-result__multi-option ${checked ? "selected" : ""}`}
                disabled={disabled || executingSuggestion !== null}
                key={`${detail.label}-${detail.space_id}`}
                onClick={() => toggleSelectedSpace(detail.space_id)}
                type="button"
              >
                <input
                  aria-hidden="true"
                  checked={checked}
                  disabled={disabled || executingSuggestion !== null}
                  tabIndex={-1}
                  type="checkbox"
                  onChange={() => undefined}
                />
                <span className="chat-interrupt-option__mark" aria-hidden="true" />
                <span className="chat-interrupt-option__body">
                  <span>{detail.label}</span>
                  <small>{String(detail.value ?? `ID ${detail.space_id}`)}</small>
                </span>
              </button>
            );
          })}
          <div className="chat-interrupt-card__submit chat-command-result__multi-actions">
            <button
              disabled={
                disabled ||
                !onExecuteCommandSuggestion ||
                executingSuggestion !== null ||
                !multiSelectSubmitCommand
              }
              onClick={async () => {
                if (!onExecuteCommandSuggestion || !multiSelectSubmitCommand || executingSuggestion !== null) {
                  return;
                }
                setExecutingSuggestion(multiSelectSubmitCommand);
                try {
                  await onExecuteCommandSuggestion(multiSelectSubmitCommand);
                } finally {
                  setExecutingSuggestion(null);
                }
              }}
              type="button"
            >
              {executingSuggestion === multiSelectSubmitCommand
                ? "执行中..."
                : `${knowledgeMountActionVerb(result)}所选 ${selectedSpaceIds.length || ""}`.trim()}
            </button>
          </div>
        </div>
      ) : details.length > 0 ? (
        <dl>
          {details.map((detail, index) => (
            <div key={`${detail.label}-${index}`}>
              <dt>{detail.label}</dt>
              <dd>{String(detail.value ?? "")}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {commandSuggestions.length > 0 ? (
        <div className="chat-interrupt-options chat-command-result__choice-select" aria-label="可执行建议">
          {commandSuggestions.map((suggestion, index) => (
            <button
              className="chat-interrupt-option chat-command-result__choice-option"
              disabled={disabled || !onExecuteCommandSuggestion || executingSuggestion !== null}
              key={suggestion}
              onClick={async () => {
                if (!onExecuteCommandSuggestion || executingSuggestion !== null) {
                  return;
                }
                setExecutingSuggestion(suggestion);
                try {
                  await onExecuteCommandSuggestion(suggestion);
                } finally {
                  setExecutingSuggestion(null);
                }
              }}
              type="button"
            >
              <input aria-hidden="true" checked={false} tabIndex={-1} type="radio" onChange={() => undefined} />
              <span className="chat-interrupt-option__mark" aria-hidden="true" />
              <span className="chat-interrupt-option__body">
                <span>
                  {executingSuggestion === suggestion
                    ? "执行中..."
                    : formatCommandChoiceLabel(result, suggestion, details[index])}
                </span>
                <small>{suggestion}</small>
              </span>
            </button>
          ))}
        </div>
      ) : null}
      {textSuggestions.length > 0 ? (
        <ul>
          {textSuggestions.map((suggestion) => (
            <li key={suggestion}>{suggestion}</li>
          ))}
        </ul>
      ) : null}
      <footer>
        <span>{result.scope}</span>
        {result.target ? <span>{result.target}</span> : null}
        {result.rollback_command ? <code>{result.rollback_command}</code> : null}
      </footer>
    </section>
  );
}

function formatCommandSuggestionLabel(result: CommandResult, suggestion: string): string {
  const normalized = suggestion.trim().replace(/\s+/g, " ");
  const spaceName = extractKnowledgeSpaceNameFromCommand(normalized);
  if (result.command_id === "mount.knowledge" && spaceName) {
    return `挂载 ${spaceName}`;
  }
  if (result.command_id === "unmount.knowledge" && spaceName) {
    return `卸载 ${spaceName}`;
  }
  return normalized;
}

function formatCommandChoiceLabel(
  result: CommandResult,
  suggestion: string,
  detail?: { label: string; value: string; [key: string]: unknown },
): string {
  if (detail?.label) {
    return detail.label;
  }
  return formatCommandSuggestionLabel(result, suggestion);
}

function resolveKnowledgeMountMultiSelectPrefix(result: CommandResult): string {
  if (result.status !== "needs_input") {
    return "";
  }
  if (result.command_id === "mount.knowledge") {
    return "/mount knowledge";
  }
  if (result.command_id === "unmount.knowledge") {
    return "/unmount knowledge";
  }
  return "";
}

function knowledgeMountActionVerb(result: CommandResult): string {
  return result.command_id === "unmount.knowledge" ? "卸载" : "挂载";
}

function detailSpaceId(detail: { [key: string]: unknown }): number | null {
  const raw = detail.space_id;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && /^\d+$/.test(raw)) {
    return Number(raw);
  }
  return null;
}

function extractKnowledgeSpaceNameFromCommand(command: string): string {
  const match = command.match(/^\/(?:unmount|umount|mount)\s+knowledge\s+(.+)$/i);
  if (!match) {
    return "";
  }
  return match[1].trim().replace(/^["'“‘`](.*)["'”’`]$/u, "$1");
}

function parseCommandResult(content: string): CommandResult | null {
  const match = content.match(/```aimemo-command-result\s*([\s\S]*?)```/);
  if (!match) {
    return null;
  }
  try {
    const parsed = JSON.parse(match[1]) as CommandResult;
    return parsed?.type === "command_result" && parsed.source === "command_router" ? parsed : null;
  } catch {
    return null;
  }
}

export function TypingIndicator({
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

    root.querySelectorAll<HTMLElement>(".segment-followup-mark").forEach((mark) => {
      mark.replaceWith(document.createTextNode(mark.textContent ?? ""));
    });

    if (threads.length === 0) {
      return;
    }

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
    const nodes: Text[] = [];
    let node = walker.nextNode();
    while (node) {
      nodes.push(node as Text);
      node = walker.nextNode();
    }

    const nodeSpans: Array<{ end: number; node: Text; start: number; text: string }> = [];
    let textCursor = 0;
    for (const textNode of nodes) {
      const text = textNode.textContent ?? "";
      if (!text) {
        continue;
      }
      nodeSpans.push({ end: textCursor + text.length, node: textNode, start: textCursor, text });
      textCursor += text.length;
    }

    const ranges = resolveFollowupMarkRanges(
      nodeSpans.map((span) => span.text).join(""),
      threads,
    );
    if (ranges.length === 0) {
      return;
    }

    const listeners: Array<{
      clickHandler: (event: MouseEvent) => void;
      element: HTMLSpanElement;
      keyHandler: (event: KeyboardEvent) => void;
    }> = [];
    for (const span of nodeSpans) {
      const marks = ranges.filter((range) => range.start < span.end && range.end > span.start);
      if (marks.length === 0) {
        continue;
      }
      let localCursor = 0;
      const fragment = document.createDocumentFragment();
      for (const mark of marks) {
        const localStart = Math.max(0, mark.start - span.start);
        const localEnd = Math.min(span.text.length, mark.end - span.start);
        if (localEnd <= localStart) {
          continue;
        }
        if (localStart > localCursor) {
          fragment.append(document.createTextNode(span.text.slice(localCursor, localStart)));
        }
        const markElement = document.createElement("span");
        markElement.className = [
          "segment-followup-mark",
          activeSegmentId === mark.thread.segment_id ? "is-active" : "",
        ].filter(Boolean).join(" ");
        markElement.dataset.segmentId = mark.thread.segment_id;
        markElement.setAttribute("aria-label", `查看片段追问：${mark.thread.original_text}`);
        markElement.setAttribute("role", "button");
        markElement.tabIndex = 0;
        markElement.textContent = span.text.slice(localStart, localEnd);
        markElement.title = "查看这个片段的追问";
        const openSegment = () => {
          onOpenSegment?.(mark.thread.segment_id);
        };
        const clickHandler = (event: MouseEvent) => {
          event.stopPropagation();
          openSegment();
        };
        const keyHandler = (event: KeyboardEvent) => {
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          event.preventDefault();
          event.stopPropagation();
          openSegment();
        };
        markElement.addEventListener("click", clickHandler);
        markElement.addEventListener("keydown", keyHandler);
        listeners.push({ clickHandler, element: markElement, keyHandler });
        fragment.append(markElement);
        localCursor = localEnd;
      }
      if (localCursor < span.text.length) {
        fragment.append(document.createTextNode(span.text.slice(localCursor)));
      }
      span.node.replaceWith(fragment);
    }

    return () => {
      for (const { clickHandler, element, keyHandler } of listeners) {
        element.removeEventListener("click", clickHandler);
        element.removeEventListener("keydown", keyHandler);
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
  const orphanThoughts = orphansBeforeSegments(thoughtsByStep, segments);
  const showThinkingTail = isStreaming && !showWarmingUp && shouldShowThinkingTail(segments);
  const items: TimelineItem[] = [];
  if (showWarmingUp) {
    items.push({
      kind: "work",
      key: "warming-up",
      node: <TypingIndicator compact />,
    });
  }
  if (orphanThoughts.length > 0) {
    items.push({
      kind: "work",
      key: "orphan-thoughts",
      node: <SegmentThoughts thoughts={orphanThoughts} />,
    });
  }

  segments.forEach((segment, idx) => {
    const isLast = idx === lastIndex;
    const visibleTools = segment.tools.filter((tool) => tool.tool_name !== REQUEST_USER_INPUT_TOOL_NAME);
    const segmentStreaming = isStreaming && isLast && visibleTools.length === 0;
    const stepThoughts = thoughtsByStep.get(segment.step_index) ?? [];

    if (stepThoughts.length > 0) {
      items.push({
        kind: "work",
        key: `step-${segment.step_index}-${idx}-thoughts`,
        node: <SegmentThoughts thoughts={stepThoughts} />,
      });
    }

    if (segment.text.length > 0) {
      items.push({
        kind: "answer",
        key: `step-${segment.step_index}-${idx}-text`,
        node: (
          <div className="chat-segment" key={`step-${segment.step_index}-text`}>
            <StreamingMarkdown
              activeSegmentId={activeSegmentId}
              content={segment.text}
              followupThreads={followupThreads}
              onOpenSegment={onOpenSegment}
              streaming={segmentStreaming}
            />
          </div>
        ),
      });
    }

    if (visibleTools.length > 0) {
      items.push({
        kind: "work",
        key: `step-${segment.step_index}-${idx}-tools`,
        node: (
          <div className="chat-segment" key={`step-${segment.step_index}-tools`}>
            <div className="chat-segment__tools">
              {visibleTools.map((tool) => (
                <ToolCallCard
                  key={tool.tool_call_id || `${tool.tool_name}-${segment.step_index}`}
                  toolName={tool.tool_name}
                  args={tool.arguments}
                  summary={tool.result_summary || tool.message}
                  status={toolCardStatus(tool)}
                />
              ))}
            </div>
          </div>
        ),
      });
    }
  });

  if (showThinkingTail) {
    items.push({
      kind: "work",
      key: "thinking-tail",
      node: <TypingIndicator compact variant="tail" />,
    });
  }

  return <div className="chat-segment-timeline">{renderTimelineItems(items, isStreaming)}</div>;
}

type TimelineItem = {
  kind: "work" | "answer";
  key: string;
  node: ReactNode;
};

function renderTimelineItems(items: TimelineItem[], isStreaming: boolean) {
  const rendered: ReactNode[] = [];
  let workGroup: TimelineItem[] = [];

  const flushWorkGroup = () => {
    if (workGroup.length === 0) {
      return;
    }
    const group = workGroup;
    workGroup = [];
    rendered.push(
      <ToolProcessWindow isStreaming={isStreaming} key={`work-${group[0].key}`}>
        {group.map((item) => (
          <div className="chat-tool-process-window__item" key={item.key}>
            {item.node}
          </div>
        ))}
      </ToolProcessWindow>,
    );
  };

  for (const item of items) {
    if (item.kind === "work") {
      workGroup.push(item);
      continue;
    }
    flushWorkGroup();
    rendered.push(<div key={item.key}>{item.node}</div>);
  }
  flushWorkGroup();
  return rendered;
}

function ToolProcessWindow({
  children,
  isStreaming,
}: {
  children: ReactNode;
  isStreaming: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isStreaming || !bodyRef.current) {
      return;
    }
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [children, isStreaming]);

  return (
    <section className="chat-tool-process-window" aria-label="工具调用过程">
      <div className="chat-tool-process-window__body" ref={bodyRef}>
        {children}
      </div>
    </section>
  );
}

export function hasVisibleAssistantWork(
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
    (segment) =>
      segment.text.trim().length > 0 ||
      segment.tools.some((tool) => tool.tool_name !== REQUEST_USER_INPUT_TOOL_NAME),
  );
}

function shouldShowThinkingTail(segments: MessageSegment[]): boolean {
  const lastSegment = segments.length > 0 ? segments[segments.length - 1] : undefined;
  if (!lastSegment) {
    return false;
  }
  const visibleTools = lastSegment.tools.filter((tool) => tool.tool_name !== REQUEST_USER_INPUT_TOOL_NAME);
  if (lastSegment.text.trim().length > 0 && visibleTools.length === 0) {
    return false;
  }
  if (visibleTools.length === 0) {
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

interface FollowupMarkRange {
  end: number;
  start: number;
  thread: SegmentFollowupThread;
}

function resolveFollowupMarkRanges(text: string, threads: SegmentFollowupThread[]): FollowupMarkRange[] {
  const ranges: FollowupMarkRange[] = [];
  const sortedThreads = [...threads].sort((left, right) => {
    const leftStart = left.position?.start ?? Number.MAX_SAFE_INTEGER;
    const rightStart = right.position?.start ?? Number.MAX_SAFE_INTEGER;
    if (leftStart !== rightStart) {
      return leftStart - rightStart;
    }
    return right.original_text.length - left.original_text.length;
  });

  for (const thread of sortedThreads) {
    const positioned = rangeFromThreadPosition(text, thread);
    const fallback = positioned ?? rangeFromFirstTextMatch(text, thread);
    if (!fallback || ranges.some((range) => rangesOverlap(range, fallback))) {
      continue;
    }
    ranges.push(fallback);
  }

  return ranges.sort((left, right) => left.start - right.start);
}

function rangeFromThreadPosition(text: string, thread: SegmentFollowupThread): FollowupMarkRange | null {
  const position = thread.position;
  if (!position || position.start < 0 || position.end <= position.start || position.start >= text.length) {
    return null;
  }
  const end = Math.min(position.end, text.length);
  const slice = text.slice(position.start, end);
  if (normalizeMarkText(slice) !== normalizeMarkText(thread.original_text)) {
    return null;
  }
  return { start: position.start, end, thread };
}

function rangeFromFirstTextMatch(text: string, thread: SegmentFollowupThread): FollowupMarkRange | null {
  const index = text.indexOf(thread.original_text);
  if (index < 0) {
    return null;
  }
  return { start: index, end: index + thread.original_text.length, thread };
}

function rangesOverlap(left: FollowupMarkRange, right: FollowupMarkRange) {
  return left.start < right.end && left.end > right.start;
}

function normalizeMarkText(text: string) {
  return text.replace(/\s+/g, " ").trim();
}
