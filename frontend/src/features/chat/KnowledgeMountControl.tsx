import { BookOpenCheck, ChevronDown, LibraryBig } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { listKnowledgeSpaces } from "../knowledge/knowledgeApi";
import type { KnowledgeSpace } from "../knowledge/types";
import { Button } from "../../shared/ui";
import type { ConversationKnowledgeMount } from "./types";

interface KnowledgeMountControlProps {
  conversationId: number | null | undefined;
  disabled?: boolean;
  mounts: ConversationKnowledgeMount[];
  onSave: (spaceIds: number[]) => Promise<void>;
}

export function KnowledgeMountControl({
  conversationId,
  disabled = false,
  mounts,
  onSave,
}: KnowledgeMountControlProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);

  const mountedIds = useMemo(() => mounts.map((mount) => mount.space_id), [mounts]);
  const mountedLabel = useMemo(() => {
    if (mounts.length === 0) {
      return "未挂载知库";
    }
    if (mounts.length === 1) {
      return mounts[0].space_name;
    }
    return `已挂载 ${mounts.length} 个知库`;
  }, [mounts]);

  useEffect(() => {
    setSelectedIds(mountedIds);
  }, [mountedIds]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    let canceled = false;
    setIsLoading(true);
    setError("");
    listKnowledgeSpaces(false)
      .then((items) => {
        if (!canceled) {
          setSpaces(items.filter((item) => item.status === "active"));
        }
      })
      .catch((currentError: unknown) => {
        if (!canceled) {
          setError(currentError instanceof Error ? currentError.message : "读取知库失败");
        }
      })
      .finally(() => {
        if (!canceled) {
          setIsLoading(false);
        }
      });
    return () => {
      canceled = true;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && rootRef.current?.contains(target)) {
        return;
      }
      setIsOpen(false);
    }
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [isOpen]);

  function toggleSpace(spaceId: number) {
    setSelectedIds((current) =>
      current.includes(spaceId)
        ? current.filter((id) => id !== spaceId)
        : [...current, spaceId],
    );
  }

  async function handleSave() {
    if (!conversationId) {
      return;
    }
    setIsSaving(true);
    setError("");
    try {
      await onSave(selectedIds);
      setIsOpen(false);
    } catch (currentError) {
      setError(currentError instanceof Error ? currentError.message : "保存挂载失败");
    } finally {
      setIsSaving(false);
    }
  }

  const hasChanged = selectedIds.slice().sort().join(",") !== mountedIds.slice().sort().join(",");

  return (
    <div className="knowledge-mount-control" ref={rootRef}>
      <button
        className={`knowledge-mount-trigger ${mounts.length > 0 ? "knowledge-mount-trigger--active" : ""}`}
        disabled={!conversationId || disabled}
        onClick={() => setIsOpen((current) => !current)}
        type="button"
      >
        <BookOpenCheck aria-hidden="true" size={16} />
        <span>{mountedLabel}</span>
        <ChevronDown aria-hidden="true" size={15} />
      </button>

      {isOpen ? (
        <div className="knowledge-mount-popover">
          <div className="knowledge-mount-popover__header">
            <strong>挂载到当前对话</strong>
            <span>Agent 只会检索这里选中的知识空间。</span>
          </div>

          <div className="knowledge-mount-popover__body">
            {isLoading ? <p className="knowledge-mount-empty">正在读取知识空间...</p> : null}
            {!isLoading && spaces.length === 0 ? (
              <p className="knowledge-mount-empty">还没有可挂载的知识空间。</p>
            ) : null}
            {spaces.map((space) => (
              <label className="knowledge-mount-option" key={space.id}>
                <input
                  checked={selectedIds.includes(space.id)}
                  onChange={() => toggleSpace(space.id)}
                  type="checkbox"
                />
                <span className="knowledge-mount-option__icon">
                  <LibraryBig aria-hidden="true" size={15} />
                </span>
                <span className="knowledge-mount-option__text">
                  <strong>{space.name}</strong>
                  <small>
                    {space.ready_document_count}/{space.document_count} 个文档可用
                  </small>
                </span>
              </label>
            ))}
          </div>

          {error ? <p className="knowledge-mount-error">{error}</p> : null}

          <div className="knowledge-mount-popover__actions">
            <button className="knowledge-mount-clear" onClick={() => setSelectedIds([])} type="button">
              清空
            </button>
            <Button disabled={isSaving || !hasChanged} onClick={handleSave} size="sm" type="button" variant="primary">
              {isSaving ? "保存中" : "保存挂载"}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
