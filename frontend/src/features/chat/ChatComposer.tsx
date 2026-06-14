import { ImagePlus, Paperclip, SendHorizontal, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent, FormEvent, KeyboardEvent } from "react";
import TextareaAutosize from "react-textarea-autosize";

import { Button } from "../../shared/ui";
import type { CommandSchema, PendingChatAttachment } from "./types";

interface ChatComposerProps {
  attachments: PendingChatAttachment[];
  commands?: CommandSchema[];
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
  commands = [],
  input,
  isSending,
  isUploading = false,
  onAddFiles,
  onInputChange,
  onRemoveAttachment,
  onStop,
  onSubmit,
}: ChatComposerProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState<number | null>(null);
  const canSubmit = input.trim().length > 0 || attachments.length > 0;
  const commandQuery = input.trimStart();
  const shouldShowCommandBoard = commandQuery.startsWith("/") && !isSending;
  const visibleCommands = useMemo(
    () => filterCommands(commands, commandQuery).slice(0, 8),
    [commands, commandQuery],
  );
  const safeSelectedIndex =
    selectedCommandIndex == null ? null : Math.min(selectedCommandIndex, Math.max(visibleCommands.length - 1, 0));

  useEffect(() => {
    setSelectedCommandIndex(null);
  }, [commandQuery]);

  function fillCommand(command: CommandSchema) {
    if (command.visibility.state !== "enabled") {
      return;
    }
    onInputChange(command.command.replace(/\s*<[^>]+>/g, "").trimEnd() + " ");
    setSelectedCommandIndex(null);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (shouldShowCommandBoard && visibleCommands.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedCommandIndex((current) => (current == null ? 0 : (current + 1) % visibleCommands.length));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedCommandIndex((current) =>
          current == null ? visibleCommands.length - 1 : (current - 1 + visibleCommands.length) % visibleCommands.length,
        );
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        onInputChange("");
        setSelectedCommandIndex(null);
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        const selected = safeSelectedIndex == null ? null : visibleCommands[safeSelectedIndex];
        const trimmed = input.trim();
        if (selected && normalizeCommand(selected.command) !== normalizeCommand(trimmed)) {
          event.preventDefault();
          fillCommand(selected);
          return;
        }
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (isSending || isUploading) {
        return;
      }
      formRef.current?.requestSubmit();
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files ?? []);
    if (files.length === 0) {
      return;
    }
    event.preventDefault();
    onAddFiles(files);
  }

  return (
    <form className="chat-input-bar" onSubmit={onSubmit} ref={formRef}>
      {shouldShowCommandBoard && visibleCommands.length > 0 ? (
        <div className="chat-command-board" role="listbox">
          {visibleCommands.map((command, index) => (
            <button
              className={`chat-command-board__item ${safeSelectedIndex != null && index === safeSelectedIndex ? "is-selected" : ""}`}
              disabled={command.visibility.state === "disabled"}
              key={command.id}
              onMouseDown={(event) => {
                event.preventDefault();
                fillCommand(command);
              }}
              role="option"
              type="button"
              aria-selected={safeSelectedIndex != null && index === safeSelectedIndex}
            >
              <span className="chat-command-board__main">
                <strong>{command.command}</strong>
                <small>{command.description}</small>
                {command.visibility.reason ? <em>{command.visibility.reason}</em> : null}
              </span>
              <span className={`chat-command-board__meta risk-${command.risk}`}>{command.scope}</span>
            </button>
          ))}
        </div>
      ) : null}
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
          className="chat-plain-input"
          disabled={isSending}
          maxRows={8}
          minRows={1}
          onChange={(event) => onInputChange(event.currentTarget.value)}
          onKeyDown={handleKeyDown}
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

function filterCommands(commands: CommandSchema[], query: string): CommandSchema[] {
  const normalized = normalizeCommand(query);
  const tokens = normalized.split(" ").filter(Boolean);
  return commands.filter((command) => {
    if (command.visibility.state === "hidden") {
      return false;
    }
    if (tokens.length === 0 || normalized === "/") {
      return true;
    }
    const haystack = [
      command.command,
      command.title,
      command.description,
      command.category,
      ...command.aliases,
    ].join(" ").toLowerCase();
    return tokens.every((token) => haystack.includes(token));
  });
}

function normalizeCommand(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}
