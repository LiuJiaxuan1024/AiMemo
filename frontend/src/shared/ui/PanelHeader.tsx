import type { ReactNode } from "react";

interface PanelHeaderProps {
  actions?: ReactNode;
  className?: string;
  subtitle?: ReactNode;
  title: ReactNode;
}

export function PanelHeader({ actions, className = "", subtitle, title }: PanelHeaderProps) {
  const classes = ["ui-panel-header", className].filter(Boolean).join(" ");

  return (
    <header className={classes}>
      <div>
        <h2>{title}</h2>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actions ? <div className="ui-panel-header-actions">{actions}</div> : null}
    </header>
  );
}
