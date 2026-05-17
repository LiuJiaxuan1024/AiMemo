import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Brain, Hammer, Pin, PinOff } from "lucide-react";

import { ElfAssistant } from "../elf/ElfAssistant";
import { countActiveJobs, countFailedJobs } from "../elf/elfState";
import { MemoryPanel } from "../memories/MemoryPanel";
import { Button, PanelHeader, SegmentedTabs } from "../../shared/ui";
import { getJobGraph, listJobs } from "./jobsApi";
import { JobDetail } from "./JobDetail";
import { JobGraphView } from "./JobGraphView";
import { JobList } from "./JobList";
import type { Job, JobGraph } from "./types";

const ACTIVE_STATUSES = new Set(["pending", "running"]);
type DrawerTab = "jobs" | "memories";

export function JobDrawer() {
  const [isOpen, setIsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<DrawerTab>("jobs");
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

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
    }
  }, [jobs, selectedJob]);

  return (
    <>
      <ElfAssistant
        activeCount={activeCount}
        failedCount={failedCount}
        isWorkshopOpen={isOpen}
        jobs={jobs}
        onToggleWorkshop={() => setIsOpen((value) => !value)}
      />

      <aside className={isOpen ? "job-drawer open" : "job-drawer"}>
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
              <JobDetail job={selectedJob} />
              <JobGraphView graph={graphQuery.data ?? null} isLoading={graphQuery.isFetching} />
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
