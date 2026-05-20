import { Suspense, lazy, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getJobGraph, listJobs } from "../../features/jobs/jobsApi";
import { JobDetail } from "../../features/jobs/JobDetail";
import { JobList } from "../../features/jobs/JobList";
import type { Job } from "../../features/jobs/types";
import { EmptyState } from "../../shared/ui";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
const JobGraphView = lazy(() =>
  import("../../features/jobs/JobGraphView").then((module) => ({ default: module.JobGraphView })),
);

export function WorkshopJobsPage() {
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

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
    }
  }, [jobs, selectedJob]);

  return (
    <>
      <div className="workshop-error-slot">
        {jobsQuery.error ? (
          <div className="job-drawer-error">
            {jobsQuery.error instanceof Error ? jobsQuery.error.message : "读取任务失败"}
          </div>
        ) : null}
      </div>

      <div className="workshop-job-grid">
        <JobList jobs={jobs} selectedJobId={selectedJob?.id ?? null} onSelect={setSelectedJob} />
        <section className="workshop-job-inspector">
          <JobDetail job={selectedJob} />
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
