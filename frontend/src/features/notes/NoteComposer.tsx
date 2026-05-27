import type { FormEvent } from "react";
import { Sparkles } from "lucide-react";

import { Button } from "../../shared/ui";
import { LazyMarkdownEditor } from "./LazyMarkdownEditor";

interface NoteComposerProps {
  blocksJson: string;
  content: string;
  error: string;
  isSaving: boolean;
  onContentChange: (value: { blocksJson: string; markdown: string }) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTitleChange: (value: string) => void;
  title: string;
}

/**
 * 新建笔记输入区。
 * 父组件负责保存请求和错误状态，这里只维护受控表单的展示与提交入口。
 */
export function NoteComposer({
  blocksJson,
  content,
  error,
  isSaving,
  onContentChange,
  onSubmit,
  onTitleChange,
  title,
}: NoteComposerProps) {
  const contentLength = content.trim().length;

  return (
    <form className="composer" onSubmit={onSubmit}>
      <header className="composer-header">
        <div>
          <span className="composer-kicker">
            <Sparkles aria-hidden="true" size={15} />
            新笔记
          </span>
          <h2>捕捉现在想到的东西</h2>
        </div>
        <small>{contentLength} 字</small>
      </header>
      <div className="composer-fields">
        <input
          aria-label="笔记标题"
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="标题，可选"
          value={title}
        />
        <LazyMarkdownEditor
          blocksJson={blocksJson}
          className="composer-editor"
          markdown={content}
          onChange={onContentChange}
          placeholder="记录灵感、会议结论、读书摘录，或任何值得稍后被找回的内容..."
        />
      </div>
      <div className="composer-actions">
        {error ? <p className="error">{error}</p> : <p className="composer-hint">保存后会自动整理摘要、标签并进入记忆检索。</p>}
        <Button disabled={isSaving} type="submit" variant="primary">
          {isSaving ? "保存中" : "保存笔记"}
        </Button>
      </div>
    </form>
  );
}
