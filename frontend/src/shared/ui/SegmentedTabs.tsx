import type { ReactNode } from "react";

export interface SegmentedTabItem<T extends string> {
  icon?: ReactNode;
  label: string;
  value: T;
}

interface SegmentedTabsProps<T extends string> {
  ariaLabel: string;
  items: SegmentedTabItem<T>[];
  onChange: (value: T) => void;
  value: T;
}

export function SegmentedTabs<T extends string>({
  ariaLabel,
  items,
  onChange,
  value,
}: SegmentedTabsProps<T>) {
  return (
    <div className="ui-segmented-tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          aria-selected={item.value === value}
          className={item.value === value ? "active" : ""}
          key={item.value}
          onClick={() => onChange(item.value)}
          role="tab"
          type="button"
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>
  );
}
