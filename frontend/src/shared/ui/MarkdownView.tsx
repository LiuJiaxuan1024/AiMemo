import "katex/dist/katex.min.css";

import { Check, Copy } from "lucide-react";
import { Children, isValidElement, type ComponentPropsWithoutRef, type ReactNode, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
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

type CodeElementProps = {
  className?: string;
  children?: ReactNode;
};

function reactNodeToText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(reactNodeToText).join("");
  }
  if (isValidElement<CodeElementProps>(node)) {
    return reactNodeToText(node.props.children);
  }
  return "";
}

function getCodeChild(children: ReactNode) {
  return Children.toArray(children).find((child) => isValidElement<CodeElementProps>(child));
}

function getCodeLanguage(children: ReactNode): string | null {
  const codeChild = getCodeChild(children);
  if (!isValidElement<CodeElementProps>(codeChild)) {
    return null;
  }

  const match = /(?:^|\s)language-([^\s]+)/.exec(codeChild.props.className ?? "");
  return match?.[1] ?? null;
}

async function copyToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function MarkdownPre({ children, ...props }: ComponentPropsWithoutRef<"pre">) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);
  const codeText = reactNodeToText(children).replace(/\n$/, "");
  const language = getCodeLanguage(children);

  useEffect(() => {
    return () => {
      if (resetTimerRef.current !== null) {
        window.clearTimeout(resetTimerRef.current);
      }
    };
  }, []);

  async function handleCopy() {
    if (!codeText) {
      return;
    }

    await copyToClipboard(codeText);
    setCopied(true);
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="markdown-code-block">
      <div className="markdown-code-block__toolbar">
        <span className="markdown-code-block__language">{language ?? "code"}</span>
        <button
          type="button"
          className={`markdown-code-copy ${copied ? "is-copied" : ""}`}
          onClick={handleCopy}
          title={copied ? "已复制" : "复制"}
          aria-label={copied ? "代码已复制" : "复制代码"}
          disabled={!codeText}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </button>
      </div>
      <pre {...props}>{children}</pre>
    </div>
  );
}

const markdownComponents: Components = {
  pre: MarkdownPre,
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
        components={markdownComponents}
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeSanitize, mathFriendlySchema], rehypeKatex]}
      >
        {value}
      </ReactMarkdown>
    </div>
  );
}
