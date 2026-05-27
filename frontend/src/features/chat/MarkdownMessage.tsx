import { MarkdownView } from "../../shared/ui";

interface MarkdownMessageProps {
  content: string;
  fallback?: string;
}

export function MarkdownMessage({ content, fallback = "" }: MarkdownMessageProps) {
  return <MarkdownView content={content} fallback={fallback} />;
}
