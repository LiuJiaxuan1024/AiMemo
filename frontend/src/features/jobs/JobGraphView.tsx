import { useEffect, useId, useState } from "react";

import { EmptyState } from "../../shared/ui";
import type { JobGraph } from "./types";

interface JobGraphViewProps {
  graph: JobGraph | null;
  isLoading: boolean;
}

export function JobGraphView({ graph, isLoading }: JobGraphViewProps) {
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!graph) {
      setSvg("");
      setError("");
      return;
    }

    let canceled = false;
    import("mermaid")
      .then((module) => {
        const mermaid = module.default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: "base",
          themeVariables: {
            fontFamily: "Inter, ui-sans-serif, system-ui",
            primaryColor: "#eef4ff",
            primaryBorderColor: "#bfdbfe",
            lineColor: "#98a2b3",
            textColor: "#1d2433",
          },
        });
        return mermaid.render(`job-graph-${id}-${graph.job_id}`, graph.mermaid);
      })
      .then((result) => {
        if (!canceled) {
          setSvg(result.svg);
          setError("");
        }
      })
      .catch((currentError: unknown) => {
        if (!canceled) {
          setSvg("");
          setError(currentError instanceof Error ? currentError.message : "流程图渲染失败");
        }
      });

    return () => {
      canceled = true;
    };
  }, [graph, id]);

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
      {error ? <pre className="job-graph-error">{error}</pre> : null}
      {svg ? <div className="job-graph-svg" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
    </section>
  );
}
