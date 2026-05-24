interface ToolCallCardProps {
  toolName: string;
  args?: Record<string, unknown> | string;
  summary?: string;
  status?: "running" | "completed" | "failed";
}

const TOOL_ICON: Record<string, string> = {
  note_search: "🔍",
  notebook_search: "🔍",
  note_lookup: "🗂",
  default: "⚙",
};

function formatArgs(args: ToolCallCardProps["args"]): string | null {
  if (args == null) {
    return null;
  }
  if (typeof args === "string") {
    return args.length > 120 ? `${args.slice(0, 117)}…` : args;
  }
  try {
    const json = JSON.stringify(args);
    return json.length > 160 ? `${json.slice(0, 157)}…` : json;
  } catch {
    return null;
  }
}

export function ToolCallCard({ toolName, args, summary, status = "completed" }: ToolCallCardProps) {
  const icon = TOOL_ICON[toolName] ?? TOOL_ICON.default;
  const argText = formatArgs(args);
  return (
    <div className={`chat-tool-card chat-tool-card--${status}`}>
      <div className="chat-tool-card__head">
        <span className="chat-tool-card__icon" aria-hidden="true">
          {icon}
        </span>
        <span className="chat-tool-card__name">{toolName}</span>
      </div>
      {argText ? <pre className="chat-tool-card__args">{argText}</pre> : null}
      {summary ? <div className="chat-tool-card__summary">→ {summary}</div> : null}
    </div>
  );
}
