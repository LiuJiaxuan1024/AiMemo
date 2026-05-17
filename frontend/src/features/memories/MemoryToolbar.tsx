import { RefreshCw } from "lucide-react";

import { Button, SegmentedTabs } from "../../shared/ui";
import type { MemoryStatus } from "./types";
import { CATEGORY_OPTIONS } from "./memoryUtils";

interface MemoryToolbarProps {
  category: string;
  onCategoryChange: (value: string) => void;
  onRefresh: () => void;
  onStatusChange: (value: MemoryStatus) => void;
  status: MemoryStatus;
}

/**
 * 记忆管理顶部筛选工具栏。
 * 状态 tab 和类型筛选只产出筛选条件，具体请求由 MemoryPanel 统一处理。
 */
export function MemoryToolbar({
  category,
  onCategoryChange,
  onRefresh,
  onStatusChange,
  status,
}: MemoryToolbarProps) {
  return (
    <div className="memory-toolbar">
      <SegmentedTabs
        ariaLabel="记忆状态"
        items={[
          { label: "生效", value: "active" },
          { label: "停用", value: "archived" },
        ]}
        onChange={onStatusChange}
        value={status}
      />
      <select
        aria-label="记忆类型"
        onChange={(event) => onCategoryChange(event.target.value)}
        value={category}
      >
        {CATEGORY_OPTIONS.map((option) => (
          <option key={option.value || "all"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <Button onClick={onRefresh} size="md">
        <RefreshCw aria-hidden="true" size={15} />
        刷新
      </Button>
    </div>
  );
}
