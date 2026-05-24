/**
 * 把后端返回的 ISO 时间格式化成简洁的中文相对时间。
 * - 1 分钟内：刚刚
 * - 1 小时内：N 分钟前
 * - 24 小时内：N 小时前
 * - 7 天内：N 天前
 * - 更久：日期形式（M月D日）
 */
export function formatRelativeTime(input: string | Date | null | undefined): string {
  if (!input) {
    return "";
  }
  const date = typeof input === "string" ? new Date(input) : input;
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));
  if (diffSec < 60) {
    return "刚刚";
  }
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) {
    return `${diffMin} 分钟前`;
  }
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) {
    return `${diffHour} 小时前`;
  }
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) {
    return `${diffDay} 天前`;
  }
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const now = new Date();
  if (date.getFullYear() === now.getFullYear()) {
    return `${month}月${day}日`;
  }
  return `${date.getFullYear()}/${month}/${day}`;
}
