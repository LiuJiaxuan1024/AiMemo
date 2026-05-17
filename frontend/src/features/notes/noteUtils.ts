import type { NoteListItem } from "../../types/note";

/**
 * 将后端 ISO 时间格式化成笔记列表和详情页使用的短日期。
 * 这个工具只处理展示格式，不参与任何业务排序或时间计算。
 */
export function formatNoteDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/**
 * 判断一条笔记是否仍有后台任务在运行。
 * App 用它决定是否轮询刷新，列表和详情页继续只负责展示状态。
 */
export function isNoteProcessing(note: NoteListItem | null | undefined): boolean {
  if (!note) {
    return false;
  }

  return (
    note.processing_status === "pending" ||
    note.processing_status === "processing" ||
    note.embedding_status === "pending" ||
    note.embedding_status === "processing"
  );
}
