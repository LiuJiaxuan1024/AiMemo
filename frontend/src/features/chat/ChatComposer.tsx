import { ImagePlus, Paperclip, SendHorizontal, Square, X } from "lucide-react";
import { useRef } from "react";
import type { ClipboardEvent, FormEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";

import { Button } from "../../shared/ui";
import type { PendingChatAttachment } from "./types";

interface ChatComposerProps {
  attachments: PendingChatAttachment[];
  input: string;
  isSending: boolean;
  isUploading?: boolean;
  onAddFiles: (files: File[]) => void;
  onInputChange: (value: string) => void;
  onRemoveAttachment: (localId: string) => void;
  onStop?: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function ChatComposer({
  attachments,
  input,
  isSending,
  isUploading = false,
  onAddFiles,
  onInputChange,
  onRemoveAttachment,
  onStop,
  onSubmit,
}: ChatComposerProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const canSubmit = input.trim().length > 0 || attachments.length > 0;

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files ?? []);
    if (files.length > 0) {
      onAddFiles(files);
    }
  }

  return (
    <form className="chat-input-bar" onSubmit={onSubmit}>
      <div className="chat-input-main">
        {attachments.length > 0 ? (
          <div className="chat-attachment-tray">
            {attachments.map((attachment) => (
              <div className="chat-attachment-chip" key={attachment.localId}>
                {attachment.previewUrl ? (
                  <img alt={attachment.name} src={attachment.previewUrl} />
                ) : (
                  <Paperclip aria-hidden="true" size={16} />
                )}
                <span>{attachment.name}</span>
                <button
                  aria-label={`移除 ${attachment.name}`}
                  disabled={isSending}
                  onClick={() => onRemoveAttachment(attachment.localId)}
                  type="button"
                >
                  <X aria-hidden="true" size={14} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <TextareaAutosize
          aria-label="发送消息"
          maxRows={6}
          minRows={1}
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (isSending || isUploading) {
                return;
              }
              event.currentTarget.form?.requestSubmit();
            }
          }}
          onPaste={handlePaste}
          placeholder="问问你的笔记、计划、偏好，或直接粘贴图片..."
          value={input}
        />
      </div>
      <input
        multiple
        onChange={(event) => {
          onAddFiles(Array.from(event.target.files ?? []));
          event.currentTarget.value = "";
        }}
        ref={fileInputRef}
        type="file"
        hidden
      />
      <Button
        aria-label="选择附件"
        className="chat-attach-button"
        disabled={isSending || isUploading}
        onClick={() => fileInputRef.current?.click()}
        size="icon"
        title="选择附件"
        type="button"
        variant="secondary"
      >
        <ImagePlus aria-hidden="true" size={17} />
      </Button>
      <Button
        disabled={isSending ? false : isUploading || !canSubmit}
        onClick={isSending ? onStop : undefined}
        size="lg"
        type={isSending ? "button" : "submit"}
        variant="primary"
      >
        {isSending ? <Square aria-hidden="true" size={15} /> : <SendHorizontal aria-hidden="true" size={17} />}
        {isSending ? "中断" : isUploading ? "上传中" : "发送"}
      </Button>
    </form>
  );
}
