import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Hammer, Pin, PinOff, Sparkles } from "lucide-react";

import { ElfAssistant } from "../elf/ElfAssistant";
import { countActiveJobs, countFailedJobs } from "../elf/elfState";
import { MemoryPanel } from "../memories/MemoryPanel";
import { Button, EmptyState, PanelHeader, SegmentedTabs } from "../../shared/ui";
import { getRuntimeConfig } from "../../shared/runtimeConfig";
import { deleteJob, getJobGraph, listJobs, retryJob } from "./jobsApi";
import { JobDetail } from "./JobDetail";
import { JobList } from "./JobList";
import type { Job } from "./types";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
type DrawerTab = "jobs" | "memories";
const JobGraphView = lazy(() =>
  import("./JobGraphView").then((module) => ({ default: module.JobGraphView })),
);

export function JobDrawer() {
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<DrawerTab>("jobs");
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [actionError, setActionError] = useState("");

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    // 精灵抽屉打开或存在运行任务时更频繁刷新；安静状态下降低轮询压力。
    refetchInterval: (query) => {
      const currentJobs = query.state.data ?? [];
      const hasActiveJob = currentJobs.some((job) => ACTIVE_STATUSES.has(job.status));
      return isOpen || hasActiveJob ? 3000 : 8000;
    },
  });

  const jobs = jobsQuery.data ?? [];
  const runtimeConfigQuery = useQuery({
    queryKey: ["runtime_config"],
    queryFn: getRuntimeConfig,
    refetchInterval: (query) => (query.state.error ? 3000 : false),
    staleTime: 0,
  });
  const isElfEnabled = runtimeConfigQuery.data?.elf.enabled === true;
  const activeCount = useMemo(() => countActiveJobs(jobs), [jobs]);
  const failedCount = useMemo(() => countFailedJobs(jobs), [jobs]);
  const selectedJobId = selectedJob?.id ?? null;
  const selectedJobGraphName = selectedJob?.graph_name ?? "";
  const graphQuery = useQuery({
    enabled: isOpen && Boolean(selectedJobId && selectedJobGraphName),
    queryKey: ["jobs", selectedJobId, "graph"],
    queryFn: () => getJobGraph(Number(selectedJobId)),
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

    // 轮询拿到新列表后，用最新 job 覆盖本地选中项，避免详情停留在旧状态。
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
      {isElfEnabled ? (
        <ElfAssistant
          activeCount={activeCount}
          failedCount={failedCount}
          isWorkshopOpen={isOpen}
          jobs={jobs}
          onToggleWorkshop={() => setIsOpen((value) => !value)}
        />
      ) : null}

      <aside className={isOpen ? "job-drawer open" : "job-drawer"}>
      {!isElfEnabled ? (
        <button
          aria-label={isOpen ? "收起精灵工坊" : "打开精灵工坊"}
          className="job-drawer-handle"
          onClick={() => setIsOpen((value) => !value)}
          type="button"
        >
          <Sparkles aria-hidden="true" size={18} />
          {activeCount > 0 ? <span>{activeCount}</span> : null}
        </button>
      ) : null}
      <div className="job-drawer-panel">
        <PanelHeader
          actions={
            <Button onClick={() => setIsOpen((value) => !value)} size="sm">
              {isOpen ? <PinOff aria-hidden="true" size={16} /> : <Pin aria-hidden="true" size={16} />}
              {isOpen ? "收起" : "展开"}
            </Button>
          }
          subtitle={activeCount > 0 ? `${activeCount} 个任务进行中` : "后台现在很安静"}
          title="精灵工坊"
        />

        {jobsQuery.error ? (
          <div className="job-drawer-error">
            {jobsQuery.error instanceof Error ? jobsQuery.error.message : "读取任务失败"}
          </div>
        ) : null}
        {graphQuery.error ? (
          <div className="job-drawer-error">
            {graphQuery.error instanceof Error ? graphQuery.error.message : "读取流程图失败"}
          </div>
        ) : null}
        {actionError ? <div className="job-drawer-error">{actionError}</div> : null}

        <SegmentedTabs
          ariaLabel="精灵工坊视图"
          items={[
            { icon: <Hammer aria-hidden="true" size={16} />, label: "任务", value: "jobs" },
            { icon: <Brain aria-hidden="true" size={16} />, label: "记忆", value: "memories" },
          ]}
          onChange={setActiveTab}
          value={activeTab}
        />

        {activeTab === "jobs" ? (
          <div className="job-drawer-content">
            <JobList jobs={jobs} selectedJobId={selectedJob?.id ?? null} onSelect={setSelectedJob} />
            <div className="job-inspector">
              <JobDetail
                isDeleting={deleteMutation.isPending}
                isRetrying={retryMutation.isPending}
                job={selectedJob}
                onDelete={handleDelete}
                onRetry={handleRetry}
              />
              <Suspense fallback={<EmptyState>正在加载 graph 渲染器...</EmptyState>}>
                <JobGraphView graph={graphQuery.data ?? null} isLoading={graphQuery.isFetching} />
              </Suspense>
            </div>
          </div>
        ) : (
          <MemoryPanel isActive={activeTab === "memories"} isOpen={isOpen} />
        )}
      </div>
    </aside>
    </>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}
