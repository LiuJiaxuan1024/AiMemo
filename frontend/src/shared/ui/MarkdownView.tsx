import "katex/dist/katex.min.css";

import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

interface MarkdownViewProps {
  className?: string;
  content: string;
  fallback?: string;
}

/**
 * 扩展 rehype-sanitize 默认 schema：放行 remark-math 产出的 `math` / `math-inline` /
 * `math-display` className，让 rehype-katex 之后还能识别到这些节点并渲染成 KaTeX。
 * 顺序：sanitize → katex。sanitize 只放行 class，KaTeX 输出本身由 KaTeX 程序化生成、
 * 不会回显用户输入字面量，因此无需再过一次 sanitize。
 */
const mathFriendlySchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      ["className", "math", "math-inline"],
    ],
    div: [
      ...(defaultSchema.attributes?.div ?? []),
      ["className", "math", "math-display"],
    ],
  },
};

export function MarkdownView({ className = "", content, fallback = "" }: MarkdownViewProps) {
  const value = content || fallback;

  if (!value) {
    return null;
  }

  const classes = ["markdown-message", className].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeSanitize, mathFriendlySchema], rehypeKatex]}
      >
        {value}
      </ReactMarkdown>
    </div>
  );
}
