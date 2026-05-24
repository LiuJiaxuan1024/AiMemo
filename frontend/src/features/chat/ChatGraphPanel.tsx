import { ArrowLeftRight, ChevronDown, ChevronRight, Pin, X } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";

import { MermaidGraphView } from "../graph/MermaidGraphView";
import { Button, EmptyState } from "../../shared/ui";
import { getTurnStateHistory } from "./chatApi";
import type { ChatCheckpointState, ChatTurnGraph, ChatTurnStateHistory } from "./types";

type DebugTab = "graph" | "checkpoints" | "context" | "performance";

type DiffKind = "added" | "removed" | "changed" | "same";

interface ChatGraphPanelProps {
  graph: ChatTurnGraph | null;
  isLoading: boolean;
  onClose: () => void;
}

export function ChatGraphPanel({ graph, isLoading, onClose }: ChatGraphPanelProps) {
  const [activeTab, setActiveTab] = useState<DebugTab>("graph");
  const [selectedSubgraphNode, setSelectedSubgraphNode] = useState<string | null>(null);
  const [selectedStateNode, setSelectedStateNode] = useState<string | null>(null);
  const [stateHistory, setStateHistory] = useState<ChatTurnStateHistory | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [headId, setHeadId] = useState<string | null>(null);
  const [pinnedBaseId, setPinnedBaseId] = useState<string | null>(null);
  const [onlyShowDiff, setOnlyShowDiff] = useState(true);

  useEffect(() => {
    setSelectedStateNode(null);
    setSelectedSubgraphNode(null);
    setStateHistory(null);
    setHistoryError("");
    setHeadId(null);
    setPinnedBaseId(null);
    if (!graph) {
      return;
    }
    let canceled = false;
    setIsHistoryLoading(true);
    getTurnStateHistory(graph.conversation_id, graph.turn_id)
      .then((history) => {
        if (canceled) {
          return;
        }
        setStateHistory(history);
        const ordered = [...history.states].reverse();
        const latest = ordered[ordered.length - 1];
        if (latest?.checkpoint_id) {
          setHeadId(latest.checkpoint_id);
        }
      })
      .catch((currentError) => {
        if (canceled) {
          return;
        }
        setHistoryError(
          currentError instanceof Error ? currentError.message : "读取 checkpoint history 失败",
        );
      })
      .finally(() => {
        if (!canceled) {
          setIsHistoryLoading(false);
        }
      });
    return () => {
      canceled = true;
    };
  }, [graph?.conversation_id, graph?.turn_id]);

  const orderedStates = useMemo<ChatCheckpointState[]>(() => {
    if (!stateHistory) {
      return [];
    }
    return [...stateHistory.states].reverse();
  }, [stateHistory]);

  const headIndex = useMemo(() => {
    if (!headId) {
      return -1;
    }
    return orderedStates.findIndex((state) => state.checkpoint_id === headId);
  }, [orderedStates, headId]);

  const headCheckpoint = headIndex >= 0 ? orderedStates[headIndex] : null;

  const baseCheckpoint = useMemo<ChatCheckpointState | null>(() => {
    if (pinnedBaseId) {
      return orderedStates.find((state) => state.checkpoint_id === pinnedBaseId) ?? null;
    }
    if (headIndex > 0) {
      return orderedStates[headIndex - 1];
    }
    return null;
  }, [orderedStates, pinnedBaseId, headIndex]);

  const baseIndex = baseCheckpoint
    ? orderedStates.findIndex((state) => state.checkpoint_id === baseCheckpoint.checkpoint_id)
    : -1;

  const selectedSubgraph = selectedSubgraphNode ? graph?.subgraphs?.[selectedSubgraphNode] : null;
  const selectedNodePayload = selectedStateNode
    ? graph?.debug_payload?.nodes?.[selectedStateNode] ?? null
    : null;

  return (
    <div
      aria-label="Graph 调试工作台"
      aria-modal="true"
      className="chat-debug-workspace"
      role="dialog"
    >
      <header className="chat-debug-workspace-header">
        <div className="chat-debug-workspace-title">
          <h2>Graph 调试工作台</h2>
          {graph ? (
            <p>
              <span>turn #{graph.turn_id}</span>
              <span
                className={`chat-debug-status chat-debug-status-${graph.status}`}
              >
                {graph.status}
              </span>
              {graph.thread_id ? <span>thread {graph.thread_id}</span> : null}
            </p>
          ) : (
            <p>选择一条 AI 回复查看</p>
          )}
        </div>
        <nav className="chat-debug-workspace-tabs" role="tablist">
          <TabButton activeTab={activeTab} onSelect={setActiveTab} value="graph">
            图结构
          </TabButton>
          <TabButton activeTab={activeTab} onSelect={setActiveTab} value="checkpoints">
            Checkpoint 对比
            {stateHistory ? <em>{stateHistory.states.length}</em> : null}
          </TabButton>
          <TabButton activeTab={activeTab} onSelect={setActiveTab} value="context">
            上下文金字塔
          </TabButton>
          <TabButton activeTab={activeTab} onSelect={setActiveTab} value="performance">
            性能 / 证据
          </TabButton>
        </nav>
        <Button aria-label="关闭 Graph 调试" onClick={onClose} size="sm">
          <X aria-hidden="true" size={16} />
          关闭
        </Button>
      </header>

      {isLoading ? (
        <EmptyState className="chat-debug-empty">正在读取 graph...</EmptyState>
      ) : null}
      {!isLoading && !graph ? (
        <EmptyState className="chat-debug-empty">暂无 graph 数据</EmptyState>
      ) : null}

      {graph ? (
        <div className="chat-debug-workspace-body">
          {activeTab === "graph" ? (
            <GraphTab
              graph={graph}
              onSelectStateNode={setSelectedStateNode}
              onSelectSubgraphNode={setSelectedSubgraphNode}
              selectedNodePayload={selectedNodePayload}
              selectedStateNode={selectedStateNode}
              selectedSubgraph={selectedSubgraph}
              selectedSubgraphNode={selectedSubgraphNode}
            />
          ) : null}
          {activeTab === "checkpoints" ? (
            <CheckpointsTab
              baseCheckpoint={baseCheckpoint}
              baseIndex={baseIndex}
              error={historyError}
              headCheckpoint={headCheckpoint}
              headId={headId}
              headIndex={headIndex}
              isLoading={isHistoryLoading}
              onlyShowDiff={onlyShowDiff}
              onSelectHead={(checkpointId) => setHeadId(checkpointId)}
              onSwap={() => {
                if (!baseCheckpoint || !headCheckpoint) {
                  return;
                }
                setHeadId(baseCheckpoint.checkpoint_id);
                setPinnedBaseId(headCheckpoint.checkpoint_id);
              }}
              onTogglePinBase={(checkpointId) =>
                setPinnedBaseId((current) =>
                  current === checkpointId ? null : checkpointId,
                )
              }
              onToggleOnlyShowDiff={() => setOnlyShowDiff((value) => !value)}
              orderedStates={orderedStates}
              pinnedBaseId={pinnedBaseId}
            />
          ) : null}
          {activeTab === "context" ? <ContextTab graph={graph} /> : null}
          {activeTab === "performance" ? <PerformanceTab graph={graph} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function TabButton({
  activeTab,
  children,
  onSelect,
  value,
}: {
  activeTab: DebugTab;
  children: ReactNode;
  onSelect: (tab: DebugTab) => void;
  value: DebugTab;
}) {
  const isActive = activeTab === value;
  return (
    <button
      aria-selected={isActive}
      className={isActive ? "is-active" : ""}
      onClick={() => onSelect(value)}
      role="tab"
      type="button"
    >
      {children}
    </button>
  );
}

type NodeDebugPayload = NonNullable<
  NonNullable<ChatTurnGraph["debug_payload"]>["nodes"]
>[string];

interface GraphTabProps {
  graph: ChatTurnGraph;
  onSelectStateNode: (node: string | null) => void;
  onSelectSubgraphNode: (node: string | null) => void;
  selectedNodePayload: NodeDebugPayload | null;
  selectedStateNode: string | null;
  selectedSubgraph: string | null | undefined;
  selectedSubgraphNode: string | null;
}

function GraphTab({
  graph,
  onSelectStateNode,
  onSelectSubgraphNode,
  selectedNodePayload,
  selectedStateNode,
  selectedSubgraph,
  selectedSubgraphNode,
}: GraphTabProps) {
  return (
    <div className="chat-debug-graph-tab">
      <div className="chat-debug-graph-canvas">
        <MermaidGraphView
          chart={graph.mermaid}
          className="chat-graph-svg"
          errorClassName="chat-debug-error"
          onNodeClick={(nodeId) => {
            if (graph.subgraphs?.[nodeId]) {
              onSelectSubgraphNode(nodeId);
              onSelectStateNode(null);
              return;
            }
            onSelectStateNode(nodeId);
            onSelectSubgraphNode(null);
          }}
          renderKey={graph.turn_id}
          themeVariables={{
            primaryColor: "#f8fafc",
            primaryBorderColor: "#cbd5e1",
          }}
        />
      </div>
      <aside className="chat-debug-graph-aside">
        {!selectedStateNode && !selectedSubgraphNode ? (
          <p className="chat-debug-aside-hint">点击 graph 中的节点查看 state 或子图。</p>
        ) : null}
        {selectedSubgraphNode && selectedSubgraph ? (
          <section className="chat-debug-aside-section">
            <header>
              <h4>子图：{selectedSubgraphNode}</h4>
              <Button
                aria-label="关闭子图"
                onClick={() => onSelectSubgraphNode(null)}
                size="sm"
              >
                <X aria-hidden="true" size={14} />
              </Button>
            </header>
            <MermaidGraphView
              chart={selectedSubgraph}
              className="chat-graph-svg"
              errorClassName="chat-debug-error"
              renderKey={`${graph.turn_id}-${selectedSubgraphNode}`}
              themeVariables={{
                primaryColor: "#f8fafc",
                primaryBorderColor: "#cbd5e1",
              }}
            />
            <details>
              <summary>节点调用详情</summary>
              <JsonTreeView
                defaultExpandDepth={1}
                value={graph.debug_payload?.nodes?.[selectedSubgraphNode] ?? {}}
              />
            </details>
          </section>
        ) : null}
        {selectedStateNode ? (
          <NodeStatePanel
            node={selectedStateNode}
            onClose={() => onSelectStateNode(null)}
            payload={selectedNodePayload}
          />
        ) : null}
      </aside>
    </div>
  );
}

interface NodeStatePanelProps {
  node: string;
  onClose: () => void;
  payload: NodeDebugPayload | null;
}

function NodeStatePanel({ node, onClose, payload }: NodeStatePanelProps) {
  const invocations = payload?.invocations ?? [];
  const fallbackState = payload?.state;
  const hasAnyInvocation = invocations.length > 0;
  // 缺省选中最新一次调用；切换节点时重置为最后一项
  const [selectedIndex, setSelectedIndex] = useState<number>(() =>
    hasAnyInvocation ? invocations.length - 1 : 0,
  );
  useEffect(() => {
    setSelectedIndex(invocations.length > 0 ? invocations.length - 1 : 0);
  }, [node, invocations.length]);

  const currentInvocation =
    hasAnyInvocation && selectedIndex >= 0 && selectedIndex < invocations.length
      ? invocations[selectedIndex]
      : null;
  const currentState = currentInvocation
    ? currentInvocation.state
    : fallbackState;
  const hasState = currentState !== null && currentState !== undefined;

  return (
    <section className="chat-debug-aside-section">
      <header>
        <h4>
          节点 state：{node}
          {invocations.length > 1 ? (
            <small className="chat-debug-invocation-count">
              被调用 {invocations.length} 次
            </small>
          ) : null}
        </h4>
        <Button aria-label="关闭节点 state" onClick={onClose} size="sm">
          <X aria-hidden="true" size={14} />
        </Button>
      </header>
      {invocations.length > 1 ? (
        <div className="chat-debug-invocation-tabs" role="tablist">
          {invocations.map((entry, index) => {
            const label = `第 ${index + 1} 次`;
            const isActive = index === selectedIndex;
            return (
              <button
                aria-selected={isActive}
                className={`chat-debug-invocation-tab${
                  isActive ? " chat-debug-invocation-tab--active" : ""
                }`}
                key={entry.index ?? index}
                onClick={() => setSelectedIndex(index)}
                role="tab"
                type="button"
              >
                <span>{label}</span>
                {typeof entry.duration_ms === "number" ? (
                  <em>{entry.duration_ms} ms</em>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
      {currentInvocation ? (
        <p className="chat-debug-invocation-meta">
          状态 {currentInvocation.status ?? "-"} ·
          {" "}
          {typeof currentInvocation.started_ms === "number"
            ? `开始 ${currentInvocation.started_ms} ms`
            : "开始 -"}
          {" "}·{" "}
          {typeof currentInvocation.completed_ms === "number"
            ? `结束 ${currentInvocation.completed_ms} ms`
            : "结束 -"}
        </p>
      ) : null}
      {hasState ? (
        <JsonTreeView defaultExpandDepth={1} value={currentState} />
      ) : (
        <p>这个节点暂时没有保存 state 快照。</p>
      )}
    </section>
  );
}

interface JsonTreeViewProps {
  value: unknown;
  defaultExpandDepth?: number;
}

function JsonTreeView({ value, defaultExpandDepth = 1 }: JsonTreeViewProps) {
  return (
    <div className="chat-debug-json-tree" role="tree">
      <JsonNode
        depth={0}
        defaultExpandDepth={defaultExpandDepth}
        label="root"
        showLabel={false}
        value={value}
      />
    </div>
  );
}

interface JsonNodeProps {
  depth: number;
  defaultExpandDepth: number;
  label: string;
  showLabel: boolean;
  value: unknown;
}

function JsonNode({ depth, defaultExpandDepth, label, showLabel, value }: JsonNodeProps) {
  const expandable = isPlainObject(value) || Array.isArray(value);
  const [expanded, setExpanded] = useState<boolean>(depth < defaultExpandDepth);
  const summary = describeJsonValue(value);

  if (!expandable) {
    return (
      <div
        className="chat-debug-json-row chat-debug-json-leaf"
        style={{ paddingLeft: depth * 12 }}
      >
        {showLabel ? <span className="chat-debug-json-key">{label}:</span> : null}
        <JsonLeaf value={value} />
      </div>
    );
  }

  const entries: Array<[string, unknown]> = Array.isArray(value)
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value as Record<string, unknown>);

  return (
    <div className="chat-debug-json-block">
      <button
        aria-expanded={expanded}
        className="chat-debug-json-row chat-debug-json-toggle"
        onClick={() => setExpanded((prev) => !prev)}
        style={{ paddingLeft: depth * 12 }}
        type="button"
      >
        {expanded ? (
          <ChevronDown aria-hidden="true" size={12} />
        ) : (
          <ChevronRight aria-hidden="true" size={12} />
        )}
        {showLabel ? <span className="chat-debug-json-key">{label}:</span> : null}
        <span className="chat-debug-json-summary">{summary}</span>
      </button>
      {expanded ? (
        <div className="chat-debug-json-children" role="group">
          {entries.length === 0 ? (
            <div
              className="chat-debug-json-row chat-debug-json-empty"
              style={{ paddingLeft: (depth + 1) * 12 }}
            >
              （空）
            </div>
          ) : (
            entries.map(([entryKey, entryValue]) => (
              <JsonNode
                defaultExpandDepth={defaultExpandDepth}
                depth={depth + 1}
                key={entryKey}
                label={entryKey}
                showLabel
                value={entryValue}
              />
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

function describeJsonValue(value: unknown): string {
  if (Array.isArray(value)) {
    return `Array · ${value.length}`;
  }
  if (isPlainObject(value)) {
    return `Object · ${Object.keys(value).length} keys`;
  }
  return typeof value;
}

function JsonLeaf({ value }: { value: unknown }) {
  if (value === null) {
    return <span className="chat-debug-json-null">null</span>;
  }
  if (value === undefined) {
    return <span className="chat-debug-json-null">undefined</span>;
  }
  if (typeof value === "string") {
    const truncated = value.length > 200 ? `${value.slice(0, 200)}…` : value;
    return (
      <span className="chat-debug-json-string" title={value}>
        &quot;{truncated}&quot;
      </span>
    );
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="chat-debug-json-scalar">{String(value)}</span>;
  }
  return <span className="chat-debug-json-scalar">{String(value)}</span>;
}

interface CheckpointsTabProps {
  baseCheckpoint: ChatCheckpointState | null;
  baseIndex: number;
  error: string;
  headCheckpoint: ChatCheckpointState | null;
  headId: string | null;
  headIndex: number;
  isLoading: boolean;
  onlyShowDiff: boolean;
  onSelectHead: (checkpointId: string | null) => void;
  onSwap: () => void;
  onTogglePinBase: (checkpointId: string | null) => void;
  onToggleOnlyShowDiff: () => void;
  orderedStates: ChatCheckpointState[];
  pinnedBaseId: string | null;
}

function CheckpointsTab({
  baseCheckpoint,
  baseIndex,
  error,
  headCheckpoint,
  headId,
  headIndex,
  isLoading,
  onlyShowDiff,
  onSelectHead,
  onSwap,
  onTogglePinBase,
  onToggleOnlyShowDiff,
  orderedStates,
  pinnedBaseId,
}: CheckpointsTabProps) {
  if (error) {
    return (
      <div className="chat-debug-checkpoints-tab">
        <p className="chat-debug-error">{error}</p>
      </div>
    );
  }
  if (isLoading && orderedStates.length === 0) {
    return (
      <EmptyState className="chat-debug-empty">正在读取 checkpoint history...</EmptyState>
    );
  }
  if (orderedStates.length === 0) {
    return (
      <EmptyState className="chat-debug-empty">本轮没有 checkpoint 快照。</EmptyState>
    );
  }

  return (
    <div className="chat-debug-checkpoints-tab">
      <CheckpointTimeline
        baseId={baseCheckpoint?.checkpoint_id ?? null}
        headId={headId}
        onSelectHead={onSelectHead}
        onTogglePinBase={onTogglePinBase}
        pinnedBaseId={pinnedBaseId}
        states={orderedStates}
      />

      <div className="chat-debug-compare-bar">
        <CompareLabel
          checkpoint={baseCheckpoint}
          index={baseIndex}
          label="Base"
          tone="base"
        />
        <button
          aria-label="交换 base / head"
          className="chat-debug-swap"
          disabled={!baseCheckpoint || !headCheckpoint}
          onClick={onSwap}
          type="button"
        >
          <ArrowLeftRight aria-hidden="true" size={14} />
          交换
        </button>
        <CompareLabel
          checkpoint={headCheckpoint}
          index={headIndex}
          label="Head"
          tone="head"
        />
        <label className="chat-debug-diff-toggle">
          <input
            checked={onlyShowDiff}
            onChange={onToggleOnlyShowDiff}
            type="checkbox"
          />
          仅显示差异
        </label>
      </div>

      <CheckpointDiffPane
        baseCheckpoint={baseCheckpoint}
        baseIndex={baseIndex}
        headCheckpoint={headCheckpoint}
        headIndex={headIndex}
        onlyShowDiff={onlyShowDiff}
      />
    </div>
  );
}

interface CheckpointTimelineProps {
  baseId: string | null;
  headId: string | null;
  onSelectHead: (checkpointId: string | null) => void;
  onTogglePinBase: (checkpointId: string | null) => void;
  pinnedBaseId: string | null;
  states: ChatCheckpointState[];
}

function CheckpointTimeline({
  baseId,
  headId,
  onSelectHead,
  onTogglePinBase,
  pinnedBaseId,
  states,
}: CheckpointTimelineProps) {
  return (
    <div className="chat-debug-timeline" role="listbox" aria-label="Checkpoint 时间线">
      {states.map((state, index) => {
        const isHead = state.checkpoint_id === headId;
        const isBase = state.checkpoint_id === baseId;
        const isPinned = state.checkpoint_id === pinnedBaseId;
        const className = [
          "chat-debug-timeline-item",
          isHead ? "is-head" : "",
          isBase ? "is-base" : "",
          isPinned ? "is-pinned" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <div className={className} key={state.checkpoint_id ?? index}>
            <button
              aria-pressed={isHead}
              className="chat-debug-timeline-pill"
              onClick={() => onSelectHead(state.checkpoint_id)}
              type="button"
            >
              <span className="chat-debug-timeline-step">#{index + 1}</span>
              <strong>{state.next.length > 0 ? state.next.join(", ") : "END"}</strong>
              <small>{shortCheckpointId(state.checkpoint_id)}</small>
              {state.created_at ? (
                <small className="chat-debug-timeline-time">
                  {formatTime(state.created_at)}
                </small>
              ) : null}
            </button>
            <button
              aria-label={isPinned ? "取消固定为 Base" : "固定为 Base"}
              className="chat-debug-timeline-base-pin"
              onClick={() => onTogglePinBase(state.checkpoint_id)}
              type="button"
            >
              <Pin aria-hidden="true" size={11} />
              {isPinned ? "Base ✓" : "钉为 base"}
            </button>
          </div>
        );
      })}
    </div>
  );
}

function CompareLabel({
  checkpoint,
  index,
  label,
  tone,
}: {
  checkpoint: ChatCheckpointState | null;
  index: number;
  label: string;
  tone: "base" | "head";
}) {
  return (
    <div className={`chat-debug-compare-card chat-debug-compare-${tone}`}>
      <span className="chat-debug-compare-label">{label}</span>
      {checkpoint ? (
        <>
          <strong>#{index + 1}</strong>
          <em>{checkpoint.next.length > 0 ? checkpoint.next.join(", ") : "END"}</em>
          <small>{shortCheckpointId(checkpoint.checkpoint_id)}</small>
        </>
      ) : (
        <strong>—</strong>
      )}
    </div>
  );
}

interface CheckpointDiffPaneProps {
  baseCheckpoint: ChatCheckpointState | null;
  baseIndex: number;
  headCheckpoint: ChatCheckpointState | null;
  headIndex: number;
  onlyShowDiff: boolean;
}

function CheckpointDiffPane({
  baseCheckpoint,
  baseIndex,
  headCheckpoint,
  headIndex,
  onlyShowDiff,
}: CheckpointDiffPaneProps) {
  if (!headCheckpoint) {
    return (
      <EmptyState className="chat-debug-empty">请选择一个 checkpoint。</EmptyState>
    );
  }
  const beforeValues = baseCheckpoint?.values ?? {};
  const afterValues = headCheckpoint.values;
  return (
    <div className="chat-debug-diff-pane">
      <header className="chat-debug-diff-headerbar">
        <div>
          <span className="chat-debug-diff-tag chat-debug-diff-tag-before">before</span>
          {baseCheckpoint ? <strong>#{baseIndex + 1}</strong> : <strong>—</strong>}
        </div>
        <div>
          <span className="chat-debug-diff-tag chat-debug-diff-tag-after">after</span>
          <strong>#{headIndex + 1}</strong>
        </div>
      </header>

      <CheckpointMetaDiff base={baseCheckpoint} head={headCheckpoint} />

      <div className="chat-debug-diff-section-title">
        <h4>state.values</h4>
      </div>
      <KeysDiff after={afterValues} before={beforeValues} onlyShowDiff={onlyShowDiff} />
    </div>
  );
}

function CheckpointMetaDiff({
  base,
  head,
}: {
  base: ChatCheckpointState | null;
  head: ChatCheckpointState;
}) {
  const baseNext = (base?.next ?? []).join(", ") || "—";
  const headNext = head.next.join(", ") || "—";
  const rows = [
    {
      label: "next",
      before: baseNext,
      after: headNext,
      same: baseNext === headNext,
    },
    {
      label: "checkpoint",
      before: base?.checkpoint_id ?? "—",
      after: head.checkpoint_id ?? "—",
      same: false,
    },
    {
      label: "parent",
      before: base?.parent_checkpoint_id ?? "—",
      after: head.parent_checkpoint_id ?? "—",
      same: false,
    },
    {
      label: "tasks",
      before: String(base?.tasks?.length ?? 0),
      after: String(head.tasks.length),
      same: (base?.tasks?.length ?? 0) === head.tasks.length,
    },
    {
      label: "interrupts",
      before: String(base?.interrupts?.length ?? 0),
      after: String(head.interrupts.length),
      same: (base?.interrupts?.length ?? 0) === head.interrupts.length,
    },
    {
      label: "values keys",
      before: String(Object.keys(base?.values ?? {}).length),
      after: String(Object.keys(head.values).length),
      same:
        Object.keys(base?.values ?? {}).length === Object.keys(head.values).length,
    },
  ];

  return (
    <div className="chat-debug-meta-diff">
      {rows.map((row) => (
        <div
          className={`chat-debug-meta-row ${row.same ? "is-same" : "is-changed"}`}
          key={row.label}
        >
          <span className="chat-debug-meta-row-label">{row.label}</span>
          <span className="chat-debug-meta-row-before" title={row.before}>
            {row.before}
          </span>
          <span className="chat-debug-meta-row-arrow">→</span>
          <span className="chat-debug-meta-row-after" title={row.after}>
            {row.after}
          </span>
        </div>
      ))}
    </div>
  );
}

function KeysDiff({
  after,
  before,
  onlyShowDiff,
}: {
  after: unknown;
  before: unknown;
  onlyShowDiff: boolean;
}) {
  const keys = useMemo(() => {
    const beforeKeys = isPlainObject(before) ? Object.keys(before) : [];
    const afterKeys = isPlainObject(after) ? Object.keys(after) : [];
    return Array.from(new Set([...beforeKeys, ...afterKeys])).sort();
  }, [before, after]);

  if (keys.length === 0) {
    return <p className="chat-debug-empty">没有 values 字段</p>;
  }

  return (
    <div className="chat-debug-diff-grid">
      <div className="chat-debug-diff-grid-header">
        <span>key</span>
        <span>before</span>
        <span>after</span>
      </div>
      {keys.map((key) => (
        <DiffRow
          after={isPlainObject(after) ? after[key] : undefined}
          before={isPlainObject(before) ? before[key] : undefined}
          depth={0}
          key={key}
          onlyShowDiff={onlyShowDiff}
          pathKey={key}
        />
      ))}
    </div>
  );
}

interface DiffRowProps {
  after: unknown;
  before: unknown;
  depth: number;
  onlyShowDiff: boolean;
  pathKey: string;
}

function DiffRow({ after, before, depth, onlyShowDiff, pathKey }: DiffRowProps) {
  const equal = deepEqual(before, after);
  const beforeMissing = before === undefined;
  const afterMissing = after === undefined;
  const kind: DiffKind = beforeMissing
    ? "added"
    : afterMissing
      ? "removed"
      : equal
        ? "same"
        : "changed";
  const expandable = (isContainer(before) || isContainer(after)) && !equal;
  const [expanded, setExpanded] = useState<boolean>(depth === 0 && kind !== "same");

  if (equal && onlyShowDiff) {
    return null;
  }

  return (
    <>
      <div
        className={`chat-debug-diff-row chat-debug-diff-kind-${kind}`}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <div className="chat-debug-diff-key">
          {expandable ? (
            <button
              aria-label={expanded ? "折叠" : "展开"}
              className="chat-debug-diff-expand"
              onClick={() => setExpanded((value) => !value)}
              type="button"
            >
              {expanded ? (
                <ChevronDown aria-hidden="true" size={12} />
              ) : (
                <ChevronRight aria-hidden="true" size={12} />
              )}
            </button>
          ) : (
            <span className="chat-debug-diff-spacer" />
          )}
          <span className="chat-debug-diff-key-name">{pathKey}</span>
          <span className={`chat-debug-diff-badge chat-debug-diff-badge-${kind}`}>
            {badgeLabel(kind)}
          </span>
        </div>
        <div className="chat-debug-diff-before">
          <ValuePreview missing={beforeMissing} value={before} />
        </div>
        <div className="chat-debug-diff-after">
          <ValuePreview missing={afterMissing} value={after} />
        </div>
      </div>
      {expandable && expanded ? (
        <NestedDiff
          after={after}
          before={before}
          depth={depth + 1}
          onlyShowDiff={onlyShowDiff}
        />
      ) : null}
    </>
  );
}

function NestedDiff({
  after,
  before,
  depth,
  onlyShowDiff,
}: {
  after: unknown;
  before: unknown;
  depth: number;
  onlyShowDiff: boolean;
}) {
  if (Array.isArray(before) || Array.isArray(after)) {
    const beforeArr = Array.isArray(before) ? before : [];
    const afterArr = Array.isArray(after) ? after : [];
    const length = Math.max(beforeArr.length, afterArr.length);
    return (
      <>
        {Array.from({ length }, (_, index) => (
          <DiffRow
            after={index < afterArr.length ? afterArr[index] : undefined}
            before={index < beforeArr.length ? beforeArr[index] : undefined}
            depth={depth}
            key={index}
            onlyShowDiff={onlyShowDiff}
            pathKey={`[${index}]`}
          />
        ))}
      </>
    );
  }
  const beforeKeys = isPlainObject(before) ? Object.keys(before) : [];
  const afterKeys = isPlainObject(after) ? Object.keys(after) : [];
  const keys = Array.from(new Set([...beforeKeys, ...afterKeys])).sort();
  return (
    <>
      {keys.map((key) => (
        <DiffRow
          after={isPlainObject(after) ? after[key] : undefined}
          before={isPlainObject(before) ? before[key] : undefined}
          depth={depth}
          key={key}
          onlyShowDiff={onlyShowDiff}
          pathKey={key}
        />
      ))}
    </>
  );
}

function ValuePreview({ missing, value }: { missing: boolean; value: unknown }) {
  if (missing) {
    return <span className="chat-debug-diff-missing">—</span>;
  }
  if (value === null) {
    return <span className="chat-debug-diff-literal">null</span>;
  }
  if (Array.isArray(value)) {
    return (
      <span className="chat-debug-diff-summary" title={previewArrayHead(value)}>
        Array · {value.length}
      </span>
    );
  }
  if (typeof value === "object") {
    const objectKeys = Object.keys(value as Record<string, unknown>);
    return (
      <span className="chat-debug-diff-summary" title={objectKeys.join(", ")}>
        Object · {objectKeys.length} keys
      </span>
    );
  }
  if (typeof value === "string") {
    if (value.length > 80) {
      return (
        <span className="chat-debug-diff-literal" title={value}>
          &quot;{value.slice(0, 80)}…&quot;
        </span>
      );
    }
    return <span className="chat-debug-diff-literal">&quot;{value}&quot;</span>;
  }
  return <span className="chat-debug-diff-literal">{String(value)}</span>;
}

function ContextTab({ graph }: { graph: ChatTurnGraph }) {
  if (graph.context_layers.length === 0) {
    return <p className="chat-debug-empty">暂无上下文记录</p>;
  }
  // 把金字塔层级和“合并视图”（L0+L1 当前对话窗口）分开渲染，避免视觉上出现重复 L1。
  const layerLayers = graph.context_layers.filter((layer) => layer.kind !== "fused");
  const fusedLayers = graph.context_layers.filter((layer) => layer.kind === "fused");

  return (
    <div className="chat-debug-context-tab">
      <section className="chat-debug-context-section">
        <h4 className="chat-debug-context-heading">金字塔层级</h4>
        {layerLayers.map((layer) => (
          <details
            className="chat-debug-context-card"
            key={`layer-${layer.level}-${layer.name}`}
          >
            <summary>
              <span
                className={`chat-debug-context-level chat-debug-context-level-${layer.level}`}
              >
                L{layer.level}
              </span>
              <strong>{layer.name}</strong>
              <small>
                {layer.used_tokens} tokens
                {layer.budget_tokens ? ` / ${layer.budget_tokens}` : ""}
              </small>
            </summary>
            <pre>{layer.content || layer.note || "空"}</pre>
          </details>
        ))}
      </section>
      {fusedLayers.length > 0 ? (
        <section className="chat-debug-context-section chat-debug-context-section--fused">
          <h4 className="chat-debug-context-heading">
            合并视图
            <span className="chat-debug-context-fused-note">
              工具规划和回答节点真正读到的输入；不属于层级体系。
            </span>
          </h4>
          {fusedLayers.map((layer) => (
            <details
              className="chat-debug-context-card chat-debug-context-card--fused"
              key={`fused-${layer.level}-${layer.name}`}
              open
            >
              <summary>
                <span className="chat-debug-context-level chat-debug-context-level-fused">
                  L0+L1
                </span>
                <strong>{layer.name}</strong>
                <small>
                  {layer.used_tokens} tokens
                  {layer.budget_tokens ? ` / ${layer.budget_tokens}` : ""}
                </small>
              </summary>
              <pre>{layer.content || layer.note || "空"}</pre>
            </details>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function PerformanceTab({ graph }: { graph: ChatTurnGraph }) {
  const events = graph.debug_payload?.events ?? {};
  const summary = graph.debug_payload?.summary ?? {};
  const nodes = graph.debug_payload?.nodes ?? {};
  const nodeEntries = Object.entries(nodes);

  return (
    <div className="chat-debug-performance-tab">
      <section className="chat-debug-performance-summary">
        <div>
          <span>首 token</span>
          <strong>{formatMs(summary.first_answer_token_ms)}</strong>
        </div>
        <div>
          <span>最后 token</span>
          <strong>{formatMs(summary.last_answer_token_ms)}</strong>
        </div>
        <div>
          <span>完成</span>
          <strong>{formatMs(events.turn_completed ?? events.graph_done)}</strong>
        </div>
        <div>
          <span>token 事件</span>
          <strong>{summary.answer_token_events ?? 0}</strong>
        </div>
      </section>

      {nodeEntries.length > 0 ? (
        <div className="chat-debug-performance-table-wrap">
          <table className="chat-debug-performance-table">
            <thead>
              <tr>
                <th>节点</th>
                <th>状态</th>
                <th>完成</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              {nodeEntries.map(([nodeName, timing]) => (
                <tr key={nodeName}>
                  <td>{nodeName}</td>
                  <td>{timing.status ?? "-"}</td>
                  <td>{formatMs(timing.completed_ms)}</td>
                  <td>{formatMs(timing.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {nodes.build_l3_retrieved_memory?.retrieval_debug ? (
        <details className="chat-debug-performance-details">
          <summary>L3 内部耗时</summary>
          <pre>
            {JSON.stringify(nodes.build_l3_retrieved_memory.retrieval_debug, null, 2)}
          </pre>
        </details>
      ) : null}

      <h4>检索证据</h4>
      {graph.retrieved_chunks.length === 0 ? (
        <p className="chat-debug-empty">本轮没有可展示的检索结果</p>
      ) : null}
      {graph.retrieved_chunks.map((chunk) => (
        <article className="chat-evidence-card" key={chunk.chunk_id}>
          <strong>{chunk.note_title}</strong>
          <small>
            chunk #{chunk.chunk_index} · score {chunk.score.toFixed(3)}
          </small>
          <p>{chunk.content}</p>
        </article>
      ))}
    </div>
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isContainer(value: unknown): boolean {
  return Array.isArray(value) || isPlainObject(value);
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (typeof a !== typeof b) {
    return false;
  }
  if (a === null || b === null) {
    return false;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) {
      return false;
    }
    return a.every((value, index) => deepEqual(value, b[index]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const aKeys = Object.keys(a);
    const bKeys = Object.keys(b);
    if (aKeys.length !== bKeys.length) {
      return false;
    }
    return aKeys.every((key) => deepEqual(a[key], b[key]));
  }
  return false;
}

function badgeLabel(kind: DiffKind): string {
  switch (kind) {
    case "added":
      return "+ 新增";
    case "removed":
      return "- 删除";
    case "changed":
      return "~ 修改";
    default:
      return "=";
  }
}

function previewArrayHead(value: unknown[]): string {
  if (value.length === 0) {
    return "[]";
  }
  const first = value[0];
  if (typeof first === "object" && first !== null) {
    return `${value.length} 项 · 首项 keys: ${Object.keys(first as object)
      .slice(0, 4)
      .join(", ")}`;
  }
  return `${value.length} 项 · 首项: ${String(first).slice(0, 60)}`;
}

function shortCheckpointId(value: string | null): string {
  if (!value) {
    return "-";
  }
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function formatTime(value: string): string {
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return `${date.getHours().toString().padStart(2, "0")}:${date
      .getMinutes()
      .toString()
      .padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
  } catch {
    return value;
  }
}

function formatMs(value: number | null | undefined): string {
  if (typeof value !== "number") {
    return "-";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${value}ms`;
}
