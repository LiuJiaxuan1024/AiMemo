import type { Job } from "../jobs/types";
import { describeJobWork, ELF_IDLE_MESSAGE } from "./elfMessages";
import type { ElfState } from "./types";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

interface DeriveElfStateOptions {
  announcedCompletedJobIds?: Set<number>;
}

/**
 * 根据后台 jobs 推导精灵当前应该呈现的状态。
 * 这个函数保持纯函数，方便后续给 chat stream、memory mutation 增加更多状态来源。
 */
export function deriveElfStateFromJobs(
  jobs: Job[],
  options: DeriveElfStateOptions = {},
): ElfState {
  const failedJob = jobs.find((job) => job.status === "failed");
  if (failedJob) {
    return {
      mood: "error",
      message: "有任务失败了，点我看看哪里卡住了。",
      source: "jobs",
      priority: 100,
      jobId: failedJob.id,
    };
  }

  const runningJob = jobs.find((job) => job.status === "running");
  if (runningJob) {
    return {
      mood: "working",
      message: `我正在${describeJobWork(runningJob)}。`,
      source: "jobs",
      priority: 70,
      jobId: runningJob.id,
    };
  }

  const pendingJob = jobs.find((job) => job.status === "pending");
  if (pendingJob) {
    return {
      mood: "thinking",
      message: "我排好队了，马上开始处理。",
      source: "jobs",
      priority: 60,
      jobId: pendingJob.id,
    };
  }

  // completed 是一次性提醒：同一个完成任务播报过以后，就不再持续占据精灵气泡。
  const completedJob = jobs.find(
    (job) => job.status === "completed" && !options.announcedCompletedJobIds?.has(job.id),
  );
  if (completedJob) {
    return {
      mood: "success",
      message: "刚刚有任务完成了。",
      source: "jobs",
      priority: 40,
      jobId: completedJob.id,
    };
  }

  return {
    mood: "idle",
    message: ELF_IDLE_MESSAGE,
    source: "system",
    priority: 10,
  };
}

export function countActiveJobs(jobs: Job[]): number {
  return jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
}

export function countFailedJobs(jobs: Job[]): number {
  return jobs.filter((job) => job.status === "failed").length;
}
