import { MermaidGraphView } from "../graph/MermaidGraphView";
import { EmptyState } from "../../shared/ui";
import type { JobGraph } from "./types";

interface JobGraphViewProps {
  graph: JobGraph | null;
  isLoading: boolean;
  isRefreshing?: boolean;
}

const LIVE_STATUSES = new Set(["pending", "running"]);

export function JobGraphView({ graph, isLoading, isRefreshing = false }: JobGraphViewProps) {
  if (isLoading && !graph) {
    return <EmptyState>正在读取 graph...</EmptyState>;
  }

  if (!graph) {
    return <EmptyState>选择一个 job 查看流程图</EmptyState>;
  }

  const isLive = LIVE_STATUSES.has(graph.status) || graph.next_nodes.length > 0;

  return (
    <section className={`job-graph${isRefreshing ? " is-refreshing" : ""}${isLive ? " is-live" : ""}`}>
      <div className="job-graph-meta">
        <span>{graph.graph_name}</span>
        <small>thread: {graph.thread_id}</small>
        {isRefreshing ? <small className="job-graph-refreshing">同步中</small> : null}
      </div>
      {graph.next_nodes.length > 0 ? (
        <div className="job-next-node">下一步：{graph.next_nodes.join(", ")}</div>
      ) : (
        <div className="job-next-node quiet">暂无待执行节点</div>
      )}
      <MermaidGraphView
        chart={graph.mermaid}
        className={`job-graph-svg${isLive ? " is-live" : ""}`}
        errorClassName="job-graph-error"
        renderKey={graph.job_id}
        themeVariables={{
          primaryColor: "#eef4ff",
          primaryBorderColor: "#bfdbfe",
        }}
      />
    </section>
  );
}
