import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

interface MarkdownMessageProps {
  content: string;
  fallback?: string;
}

export function MarkdownMessage({ content, fallback = "" }: MarkdownMessageProps) {
  const value = content || fallback;

  if (!value) {
    return null;
  }

  return (
    <div className="markdown-message">
      <ReactMarkdown rehypePlugins={[rehypeSanitize]} remarkPlugins={[remarkGfm]}>
        {value}
      </ReactMarkdown>
    </div>
  );
}
