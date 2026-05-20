import { X } from "lucide-react";

import { MermaidGraphView } from "../graph/MermaidGraphView";
import { Button, EmptyState, PanelHeader } from "../../shared/ui";
import type { ChatTurnGraph } from "./types";

interface ChatGraphPanelProps {
  graph: ChatTurnGraph | null;
  isLoading: boolean;
  onClose: () => void;
}

export function ChatGraphPanel({ graph, isLoading, onClose }: ChatGraphPanelProps) {
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
            themeVariables={{
              primaryColor: "#f8fafc",
              primaryBorderColor: "#cbd5e1",
            }}
          />

          <section className="chat-debug-section">
            <h4>性能</h4>
            <PerformanceDebug graph={graph} />
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

function formatMs(value: number | null | undefined) {
  if (typeof value !== "number") {
    return "-";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}s`;
  }
  return `${value}ms`;
}
