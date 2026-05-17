import { Badge, EmptyState } from "../../shared/ui";
import type { BadgeTone } from "../../shared/ui/Badge";
import type { Job } from "./types";

interface JobListProps {
  jobs: Job[];
  selectedJobId: number | null;
  onSelect: (job: Job) => void;
}

function formatTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function JobList({ jobs, selectedJobId, onSelect }: JobListProps) {
  if (jobs.length === 0) {
    return <EmptyState>暂无后台任务</EmptyState>;
  }

  return (
    <div className="job-list">
      {jobs.map((job) => (
        <button
          className={job.id === selectedJobId ? "job-row active" : "job-row"}
          key={job.id}
          onClick={() => onSelect(job)}
          type="button"
        >
          <div>
            <strong>#{job.id}</strong>
            <span>{job.type}</span>
          </div>
          <small>{formatTime(job.updated_at)}</small>
          <Badge className="job-row-status" tone={getJobStatusTone(job.status)}>{job.status}</Badge>
        </button>
      ))}
    </div>
  );
}

function getJobStatusTone(status: string): BadgeTone {
  if (status === "completed") {
    return "success";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "running") {
    return "warning";
  }
  if (status === "pending") {
    return "info";
  }
  return "neutral";
}
