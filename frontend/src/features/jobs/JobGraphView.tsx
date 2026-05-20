import { MermaidGraphView } from "../graph/MermaidGraphView";
import { EmptyState } from "../../shared/ui";
import type { JobGraph } from "./types";

interface JobGraphViewProps {
  graph: JobGraph | null;
  isLoading: boolean;
}

export function JobGraphView({ graph, isLoading }: JobGraphViewProps) {
  if (isLoading) {
    return <EmptyState>正在读取 graph...</EmptyState>;
  }

  if (!graph) {
    return <EmptyState>选择一个 job 查看流程图</EmptyState>;
  }

  return (
    <section className="job-graph">
      <div className="job-graph-meta">
        <span>{graph.graph_name}</span>
        <small>thread: {graph.thread_id}</small>
      </div>
      {graph.next_nodes.length > 0 ? (
        <div className="job-next-node">下一步：{graph.next_nodes.join(", ")}</div>
      ) : (
        <div className="job-next-node quiet">暂无待执行节点</div>
      )}
      <MermaidGraphView
        chart={graph.mermaid}
        className="job-graph-svg"
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
