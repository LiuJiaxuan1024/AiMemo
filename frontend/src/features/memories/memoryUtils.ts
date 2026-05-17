import type { MemoryStatus } from "./types";

export const CATEGORY_OPTIONS = [
  { value: "", label: "全部类型" },
  { value: "preference", label: "偏好" },
  { value: "identity", label: "身份" },
  { value: "goal", label: "目标" },
  { value: "instruction", label: "指令" },
  { value: "event", label: "事件" },
  { value: "fact", label: "事实" },
];

export const CATEGORY_LABELS = new Map(CATEGORY_OPTIONS.map((item) => [item.value, item.label]));

export const STATUS_LABELS: Record<MemoryStatus, string> = {
  active: "生效",
  archived: "停用",
};

/**
 * 记忆面板里的短时间展示格式。
 * 这个函数只服务 UI 展示，不用于判断记忆新旧或排序。
 */
export function formatMemoryTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/**
 * importance/confidence 在模型侧是 0-1 小数，界面固定展示两位便于扫描。
 */
export function formatMemoryScore(value: number): string {
  return value.toFixed(2);
}
