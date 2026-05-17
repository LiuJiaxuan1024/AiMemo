import type { ReactNode } from "react";

interface EmptyStateProps {
  children: ReactNode;
  className?: string;
}

export function EmptyState({ children, className = "" }: EmptyStateProps) {
  const classes = ["ui-empty-state", className].filter(Boolean).join(" ");

  return <div className={classes}>{children}</div>;
}
