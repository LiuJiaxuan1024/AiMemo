import { EmptyState } from "../../shared/ui";
import type { Job } from "./types";

interface JobDetailProps {
  job: Job | null;
  isDeleting?: boolean;
  isRetrying?: boolean;
  onDelete?: (job: Job) => void;
  onRetry?: (job: Job) => void;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function JobDetail({ job, isDeleting = false, isRetrying = false, onDelete, onRetry }: JobDetailProps) {
  if (!job) {
    return <EmptyState>选择一个任务看看精灵在忙什么</EmptyState>;
  }

  const canRetry = job.status === "failed" || job.status === "canceled";
  const canDelete = job.status !== "pending" && job.status !== "running";

  return (
    <section className="job-detail">
      {canRetry || canDelete ? (
        <div className="job-detail-actions">
          {canRetry ? (
            <button disabled={isRetrying} onClick={() => onRetry?.(job)} type="button">
              重试
            </button>
          ) : null}
          {canDelete ? (
            <button className="danger" disabled={isDeleting} onClick={() => onDelete?.(job)} type="button">
              删除
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="job-detail-grid">
        <span>ID</span>
        <strong>#{job.id}</strong>
        <span>Graph</span>
        <strong>{job.graph_name ?? "-"}</strong>
        <span>尝试</span>
        <strong>
          {job.attempts}/{job.max_attempts}
        </strong>
        <span>更新时间</span>
        <strong>{formatDate(job.updated_at)}</strong>
      </div>
      {job.error ? <pre className="job-error">{job.error}</pre> : null}
      <details>
        <summary>Payload</summary>
        <pre>{JSON.stringify(job.payload, null, 2)}</pre>
      </details>
    </section>
  );
}
