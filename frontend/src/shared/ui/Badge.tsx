import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";

interface BadgeProps {
  children: ReactNode;
  className?: string;
  tone?: BadgeTone;
}

export function Badge({ children, className = "", tone = "neutral" }: BadgeProps) {
  const classes = ["ui-badge", `ui-badge-${tone}`, className].filter(Boolean).join(" ");

  return <span className={classes}>{children}</span>;
}
