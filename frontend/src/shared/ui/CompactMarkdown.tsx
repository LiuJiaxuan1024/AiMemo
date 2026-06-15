import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

interface CompactMarkdownProps {
  className?: string;
  content: string;
  fallback?: string;
}

const compactMarkdownComponents: Components = {
  a: ({ children }) => <span className="compact-markdown__link">{children}</span>,
  blockquote: ({ children }) => <span className="compact-markdown__block">{children}</span>,
  br: () => <span className="compact-markdown__break"> </span>,
  code: ({ children }) => <code>{children}</code>,
  em: ({ children }) => <em>{children}</em>,
  h1: ({ children }) => <strong>{children}</strong>,
  h2: ({ children }) => <strong>{children}</strong>,
  h3: ({ children }) => <strong>{children}</strong>,
  h4: ({ children }) => <strong>{children}</strong>,
  h5: ({ children }) => <strong>{children}</strong>,
  h6: ({ children }) => <strong>{children}</strong>,
  hr: () => <span className="compact-markdown__break"> </span>,
  img: ({ alt }) => <span>{alt}</span>,
  li: ({ children }) => <span className="compact-markdown__item">{children}</span>,
  ol: ({ children }) => <span className="compact-markdown__list">{children}</span>,
  p: ({ children }) => <span className="compact-markdown__block">{children}</span>,
  pre: ({ children }) => <span className="compact-markdown__block">{children}</span>,
  strong: ({ children }) => <strong>{children}</strong>,
  table: ({ children }) => <span className="compact-markdown__block">{children}</span>,
  tbody: ({ children }) => <span>{children}</span>,
  td: ({ children }) => <span>{children}</span>,
  th: ({ children }) => <strong>{children}</strong>,
  thead: ({ children }) => <span>{children}</span>,
  tr: ({ children }) => <span className="compact-markdown__item">{children}</span>,
  ul: ({ children }) => <span className="compact-markdown__list">{children}</span>,
};

export function CompactMarkdown({ className = "", content, fallback = "" }: CompactMarkdownProps) {
  const value = content || fallback;
  if (!value) {
    return null;
  }
  const classes = ["compact-markdown", className].filter(Boolean).join(" ");
  return (
    <span className={classes}>
      <ReactMarkdown
        components={compactMarkdownComponents}
        rehypePlugins={[rehypeSanitize]}
        remarkPlugins={[remarkGfm]}
      >
        {value}
      </ReactMarkdown>
    </span>
  );
}
