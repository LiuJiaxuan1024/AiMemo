import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

interface MarkdownViewProps {
  className?: string;
  content: string;
  fallback?: string;
}

export function MarkdownView({ className = "", content, fallback = "" }: MarkdownViewProps) {
  const value = content || fallback;

  if (!value) {
    return null;
  }

  const classes = ["markdown-message", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <ReactMarkdown rehypePlugins={[rehypeSanitize]} remarkPlugins={[remarkGfm]}>
        {value}
      </ReactMarkdown>
    </div>
  );
}
