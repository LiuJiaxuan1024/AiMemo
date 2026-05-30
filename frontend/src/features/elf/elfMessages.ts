import type { Job } from "../jobs/types";

/**
 * 把 job 类型转换成更像人话的动作描述。
 * 后端 job type 后续可能继续扩展，这里做兜底，避免未知任务显示成生硬的技术字段。
 */
export function describeJobWork(job: Job | undefined): string {
  if (!job) {
    return "后台任务";
  }

  if (job.type.includes("embedding")) {
    return "把笔记放进记忆库";
  }
  if (job.type.includes("metadata")) {
    return "整理笔记标题、摘要和标签";
  }
  if (job.type.includes("conversation") || job.type.includes("memory")) {
    return "整理对话里的长期记忆";
  }

  return "处理后台任务";
}

export const ELF_IDLE_MESSAGE = "";
