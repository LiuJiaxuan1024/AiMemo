import { SendHorizontal } from "lucide-react";
import type { FormEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";

import { Button } from "../../shared/ui";

interface ChatComposerProps {
  input: string;
  isSending: boolean;
  onInputChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function ChatComposer({ input, isSending, onInputChange, onSubmit }: ChatComposerProps) {
  return (
    <form className="chat-input-bar" onSubmit={onSubmit}>
      <TextareaAutosize
        aria-label="发送消息"
        maxRows={6}
        minRows={1}
        onChange={(event) => onInputChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (isSending) {
              return;
            }
            event.currentTarget.form?.requestSubmit();
          }
        }}
        placeholder="问问你的笔记、计划、偏好，或直接闲聊..."
        value={input}
      />
      <Button disabled={isSending || !input.trim()} size="lg" type="submit" variant="primary">
        <SendHorizontal aria-hidden="true" size={17} />
        {isSending ? "生成中" : "发送"}
      </Button>
    </form>
  );
}
