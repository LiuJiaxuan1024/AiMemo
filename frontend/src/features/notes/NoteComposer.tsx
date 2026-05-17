import type { FormEvent } from "react";

import { Button } from "../../shared/ui";

interface NoteComposerProps {
  content: string;
  error: string;
  isSaving: boolean;
  onContentChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTitleChange: (value: string) => void;
  title: string;
}

/**
 * 新建笔记输入区。
 * 父组件负责保存请求和错误状态，这里只维护受控表单的展示与提交入口。
 */
export function NoteComposer({
  content,
  error,
  isSaving,
  onContentChange,
  onSubmit,
  onTitleChange,
  title,
}: NoteComposerProps) {
  return (
    <form className="composer" onSubmit={onSubmit}>
      <input
        aria-label="笔记标题"
        onChange={(event) => onTitleChange(event.target.value)}
        placeholder="标题，可选"
        value={title}
      />
      <textarea
        aria-label="笔记内容"
        onChange={(event) => onContentChange(event.target.value)}
        placeholder="记录点什么..."
        value={content}
      />
      <div className="composer-actions">
        {error ? <p className="error">{error}</p> : <span />}
        <Button disabled={isSaving} type="submit" variant="primary">
          {isSaving ? "保存中" : "保存笔记"}
        </Button>
      </div>
    </form>
  );
}
