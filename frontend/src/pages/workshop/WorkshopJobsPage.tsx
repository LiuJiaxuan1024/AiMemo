import { Suspense, lazy, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteJob, getJobGraph, listJobs, retryJob } from "../../features/jobs/jobsApi";
import { JobDetail } from "../../features/jobs/JobDetail";
import { JobList } from "../../features/jobs/JobList";
import type { Job } from "../../features/jobs/types";
import { EmptyState } from "../../shared/ui";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
const JobGraphView = lazy(() =>
  import("../../features/jobs/JobGraphView").then((module) => ({ default: module.JobGraphView })),
);

export function WorkshopJobsPage() {
  const queryClient = useQueryClient();
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [actionError, setActionError] = useState("");

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    // 工坊任务页用于排查后台执行状态，所以在存在运行任务时保持较高刷新频率。
    refetchInterval: (query) => {
      const currentJobs = query.state.data ?? [];
      return currentJobs.some((job) => ACTIVE_STATUSES.has(job.status)) ? 3000 : 8000;
    },
  });
  const jobs = jobsQuery.data ?? [];

  const graphQuery = useQuery({
    enabled: Boolean(selectedJob?.id && selectedJob.graph_name),
    queryKey: ["jobs", selectedJob?.id, "graph"],
    queryFn: () => getJobGraph(Number(selectedJob?.id)),
    refetchInterval: selectedJob && ACTIVE_STATUSES.has(selectedJob.status) ? 3000 : false,
  });

  const retryMutation = useMutation({
    mutationFn: retryJob,
    onSuccess: async (job) => {
      setSelectedJob(job);
      setActionError("");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["jobs", job.id, "graph"] });
    },
    onError: (caught) => setActionError(errorMessage(caught, "重试任务失败")),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteJob,
    onSuccess: async (_value, jobId) => {
      setSelectedJob((current) => (current?.id === jobId ? null : current));
      setActionError("");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (caught) => setActionError(errorMessage(caught, "删除任务失败")),
  });

  useEffect(() => {
    if (jobs.length === 0) {
      setSelectedJob(null);
      return;
    }

    if (!selectedJob) {
      setSelectedJob(jobs[0]);
      return;
    }

    // 轮询拿到新列表后同步选中任务，避免详情和图停在旧状态。
    const latestSelected = jobs.find((job) => job.id === selectedJob.id);
    if (latestSelected && latestSelected !== selectedJob) {
      setSelectedJob(latestSelected);
      return;
    }
    if (!latestSelected) {
      setSelectedJob(jobs[0] ?? null);
    }
  }, [jobs, selectedJob]);

  function handleRetry(job: Job) {
    retryMutation.mutate(job.id);
  }

  function handleDelete(job: Job) {
    const confirmed = window.confirm(`确认删除任务 #${job.id} 吗？`);
    if (!confirmed) {
      return;
    }
    deleteMutation.mutate(job.id);
  }

  return (
    <>
      <div className="workshop-error-slot">
        {jobsQuery.error ? (
          <div className="job-drawer-error">
            {jobsQuery.error instanceof Error ? jobsQuery.error.message : "读取任务失败"}
          </div>
        ) : null}
        {actionError ? <div className="job-drawer-error">{actionError}</div> : null}
      </div>

      <div className="workshop-job-grid">
        <JobList jobs={jobs} selectedJobId={selectedJob?.id ?? null} onSelect={setSelectedJob} />
        <section className="workshop-job-inspector">
          <JobDetail
            isDeleting={deleteMutation.isPending}
            isRetrying={retryMutation.isPending}
            job={selectedJob}
            onDelete={handleDelete}
            onRetry={handleRetry}
          />
          {graphQuery.error ? (
            <div className="job-drawer-error">
              {graphQuery.error instanceof Error ? graphQuery.error.message : "读取流程图失败"}
            </div>
          ) : null}
          <Suspense fallback={<EmptyState>正在加载 graph 渲染器...</EmptyState>}>
            <JobGraphView graph={graphQuery.data ?? null} isLoading={graphQuery.isFetching} />
          </Suspense>
        </section>
      </div>
    </>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}
