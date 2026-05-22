import { X } from "lucide-react";
import { useState } from "react";

import { MermaidGraphView } from "../graph/MermaidGraphView";
import { Button, EmptyState, PanelHeader } from "../../shared/ui";
import { getTurnStateHistory } from "./chatApi";
import type { ChatCheckpointState, ChatTurnGraph, ChatTurnStateHistory } from "./types";

interface ChatGraphPanelProps {
  graph: ChatTurnGraph | null;
  isLoading: boolean;
  onClose: () => void;
}

export function ChatGraphPanel({ graph, isLoading, onClose }: ChatGraphPanelProps) {
  const [selectedSubgraphNode, setSelectedSubgraphNode] = useState<string | null>(null);
  const [selectedStateNode, setSelectedStateNode] = useState<string | null>(null);
  const [stateHistory, setStateHistory] = useState<ChatTurnStateHistory | null>(null);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState<string | null>(null);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const selectedSubgraph = selectedSubgraphNode ? graph?.subgraphs?.[selectedSubgraphNode] : null;
  const selectedState = selectedStateNode ? graph?.debug_payload?.nodes?.[selectedStateNode]?.state : null;
  const selectedCheckpoint = stateHistory?.states.find((state) => state.checkpoint_id === selectedCheckpointId) ?? null;

  async function handleLoadStateHistory() {
    if (!graph) {
      return;
    }
    setIsHistoryLoading(true);
    setHistoryError("");
    try {
      const history = await getTurnStateHistory(graph.conversation_id, graph.turn_id);
      setStateHistory(history);
      setSelectedCheckpointId(history.states[0]?.checkpoint_id ?? null);
    } catch (currentError) {
      setHistoryError(currentError instanceof Error ? currentError.message : "读取 checkpoint history 失败");
    } finally {
      setIsHistoryLoading(false);
    }
  }

  return (
    <aside className="chat-debug-panel">
      <PanelHeader
        actions={
          <Button aria-label="关闭 Graph 调试" onClick={onClose} size="sm">
            <X aria-hidden="true" size={16} />
            关闭
          </Button>
        }
        subtitle={graph ? `turn #${graph.turn_id} · ${graph.status}` : "选择一条 AI 回复查看"}
        title="Graph 调试"
      />

      {isLoading ? <EmptyState className="chat-debug-empty">正在读取 graph...</EmptyState> : null}
      {!isLoading && !graph ? <EmptyState className="chat-debug-empty">暂无 graph 数据</EmptyState> : null}

      {graph ? (
        <div className="chat-debug-content">
          <MermaidGraphView
            chart={graph.mermaid}
            className="chat-graph-svg"
            errorClassName="chat-debug-error"
            renderKey={graph.turn_id}
            onNodeClick={(nodeId) => {
              if (graph.subgraphs?.[nodeId]) {
                setSelectedSubgraphNode(nodeId);
                setSelectedStateNode(null);
                return;
              }
              setSelectedStateNode(nodeId);
              setSelectedSubgraphNode(null);
            }}
            themeVariables={{
              primaryColor: "#f8fafc",
              primaryBorderColor: "#cbd5e1",
            }}
          />

          {selectedStateNode ? (
            <section className="chat-debug-section chat-node-state-section">
              <div className="chat-subgraph-header">
                <h4>节点 State：{selectedStateNode}</h4>
                <Button aria-label="关闭节点 State" onClick={() => setSelectedStateNode(null)} size="sm">
                  <X aria-hidden="true" size={15} />
                  关闭
                </Button>
              </div>
              {selectedState === null || selectedState === undefined ? (
                <p>这个节点暂时没有保存 state 快照。运行中的节点通常要等节点完成后才会写入快照。</p>
              ) : (
                <pre>{JSON.stringify(selectedState, null, 2)}</pre>
              )}
            </section>
          ) : null}

          {selectedSubgraphNode && selectedSubgraph ? (
            <section className="chat-debug-section chat-subgraph-section">
              <div className="chat-subgraph-header">
                <h4>子图：{selectedSubgraphNode}</h4>
                <Button aria-label="关闭子图" onClick={() => setSelectedSubgraphNode(null)} size="sm">
                  <X aria-hidden="true" size={15} />
                  关闭
                </Button>
              </div>
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
                <pre>{JSON.stringify(graph.debug_payload?.nodes?.[selectedSubgraphNode] ?? {}, null, 2)}</pre>
              </details>
            </section>
          ) : null}

          <section className="chat-debug-section">
            <h4>性能</h4>
            <PerformanceDebug graph={graph} />
          </section>

          <section className="chat-debug-section chat-checkpoint-history">
            <div className="chat-subgraph-header">
              <h4>Checkpoint History</h4>
              <Button
                aria-label="读取 Checkpoint History"
                disabled={isHistoryLoading}
                onClick={handleLoadStateHistory}
                size="sm"
              >
                {isHistoryLoading ? "读取中" : stateHistory ? "刷新" : "读取"}
              </Button>
            </div>
            {historyError ? <p className="chat-debug-error">{historyError}</p> : null}
            {stateHistory ? (
              <CheckpointHistoryViewer
                history={stateHistory}
                selectedCheckpoint={selectedCheckpoint}
                selectedCheckpointId={selectedCheckpointId}
                onSelect={setSelectedCheckpointId}
              />
            ) : (
              <p>按需读取 LangGraph 原生 state history，用来查看每个 checkpoint 的 values、next 和 task。</p>
            )}
          </section>

          <section className="chat-debug-section">
            <h4>上下文金字塔</h4>
            {graph.context_layers.length === 0 ? <p>暂无上下文记录</p> : null}
            {graph.context_layers.map((layer) => (
              <details key={`${layer.level}-${layer.name}`}>
                <summary>
                  L{layer.level} · {layer.name} · {layer.used_tokens} tokens
                </summary>
                <pre>{layer.content || layer.note || "空"}</pre>
              </details>
            ))}
          </section>

          <section className="chat-debug-section">
            <h4>检索证据</h4>
            {graph.retrieved_chunks.length === 0 ? <p>本轮没有可展示的检索结果</p> : null}
            {graph.retrieved_chunks.map((chunk) => (
              <article className="chat-evidence-card" key={chunk.chunk_id}>
                <strong>{chunk.note_title}</strong>
                <small>
                  chunk #{chunk.chunk_index} · score {chunk.score.toFixed(3)}
                </small>
                <p>{chunk.content}</p>
              </article>
            ))}
          </section>
        </div>
      ) : null}
    </aside>
  );
}

function CheckpointHistoryViewer({
  history,
  onSelect,
  selectedCheckpoint,
  selectedCheckpointId,
}: {
  history: ChatTurnStateHistory;
  onSelect: (checkpointId: string | null) => void;
  selectedCheckpoint: ChatCheckpointState | null;
  selectedCheckpointId: string | null;
}) {
  return (
    <div className="chat-checkpoint-viewer">
      <div className="chat-checkpoint-list">
        {history.states.map((state, index) => (
          <button
            className={state.checkpoint_id === selectedCheckpointId ? "is-selected" : ""}
            key={state.checkpoint_id ?? index}
            onClick={() => onSelect(state.checkpoint_id)}
            type="button"
          >
            <span>#{history.states.length - index}</span>
            <strong>{state.next.length > 0 ? state.next.join(", ") : "END / checkpoint"}</strong>
            <small>{shortCheckpointId(state.checkpoint_id)}</small>
          </button>
        ))}
      </div>
      {selectedCheckpoint ? (
        <div className="chat-checkpoint-detail">
          <dl>
            <div>
              <dt>checkpoint</dt>
              <dd>{selectedCheckpoint.checkpoint_id ?? "-"}</dd>
            </div>
            <div>
              <dt>parent</dt>
              <dd>{selectedCheckpoint.parent_checkpoint_id ?? "-"}</dd>
            </div>
            <div>
              <dt>created</dt>
              <dd>{selectedCheckpoint.created_at ?? "-"}</dd>
            </div>
            <div>
              <dt>next</dt>
              <dd>{selectedCheckpoint.next.length > 0 ? selectedCheckpoint.next.join(", ") : "-"}</dd>
            </div>
          </dl>
          <details open>
            <summary>values</summary>
            <pre>{JSON.stringify(selectedCheckpoint.values, null, 2)}</pre>
          </details>
          <details>
            <summary>tasks</summary>
            <pre>{JSON.stringify(selectedCheckpoint.tasks, null, 2)}</pre>
          </details>
          <details>
            <summary>metadata</summary>
            <pre>{JSON.stringify(selectedCheckpoint.metadata ?? {}, null, 2)}</pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}

function PerformanceDebug({ graph }: { graph: ChatTurnGraph }) {
  const events = graph.debug_payload?.events ?? {};
  const summary = graph.debug_payload?.summary ?? {};
  const nodes = graph.debug_payload?.nodes ?? {};
  const nodeEntries = Object.entries(nodes);

  if (Object.keys(events).length === 0 && nodeEntries.length === 0) {
    return <p>暂无性能埋点</p>;
  }

  return (
    <div className="chat-performance-debug">
      <dl>
        <div>
          <dt>首 token</dt>
          <dd>{formatMs(summary.first_answer_token_ms)}</dd>
        </div>
        <div>
          <dt>最后 token</dt>
          <dd>{formatMs(summary.last_answer_token_ms)}</dd>
        </div>
        <div>
          <dt>完成</dt>
          <dd>{formatMs(events.turn_completed ?? events.graph_done)}</dd>
        </div>
        <div>
          <dt>token 事件</dt>
          <dd>{summary.answer_token_events ?? 0}</dd>
        </div>
      </dl>

      {nodeEntries.length > 0 ? (
        <table>
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
      ) : null}

      {nodes.build_l3_retrieved_memory?.retrieval_debug ? (
        <details>
          <summary>L3 内部耗时</summary>
          <pre>{JSON.stringify(nodes.build_l3_retrieved_memory.retrieval_debug, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

function shortCheckpointId(value: string | null) {
  if (!value) {
    return "-";
  }
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function formatMs(value: number | null | undefined) {
  if (typeof value !== "number") {
    return "-";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${value}ms`;
}
