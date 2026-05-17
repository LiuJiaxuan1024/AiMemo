import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { EmptyState } from "../../shared/ui";
import { activateMemory, archiveMemory, listMemories, updateMemory } from "./memoriesApi";
import { MemoryCard } from "./MemoryCard";
import { MemoryToolbar } from "./MemoryToolbar";
import type { Memory, MemoryStatus, MemoryUpdateInput } from "./types";
import { STATUS_LABELS } from "./memoryUtils";

interface MemoryPanelProps {
  isOpen: boolean;
  isActive: boolean;
}

export function MemoryPanel({ isOpen, isActive }: MemoryPanelProps) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<MemoryStatus>("active");
  const [category, setCategory] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<MemoryUpdateInput>({});
  const [error, setError] = useState("");
  const memoriesQueryKey = ["memories", { status, category: category || "" }];
  const memoriesQuery = useQuery({
    enabled: isOpen && isActive,
    queryKey: memoriesQueryKey,
    queryFn: () =>
      listMemories({
        status,
        category: category || undefined,
      }),
  });
  const memories = memoriesQuery.data ?? [];

  const activeCount = useMemo(
    () => memories.filter((memory) => memory.status === "active").length,
    [memories],
  );

  const updateMemoryMutation = useMutation({
    mutationFn: ({ memoryId, input }: { memoryId: number; input: MemoryUpdateInput }) =>
      updateMemory(memoryId, input),
    onSuccess: async () => {
      setEditingId(null);
      setDraft({});
      await queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });
  const archiveMemoryMutation = useMutation({
    mutationFn: archiveMemory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });
  const activateMemoryMutation = useMutation({
    mutationFn: activateMemory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });
  const isSaving =
    updateMemoryMutation.isPending ||
    archiveMemoryMutation.isPending ||
    activateMemoryMutation.isPending;

  function refreshMemories() {
    setError("");
    memoriesQuery.refetch();
  }

  useEffect(() => {
    if (editingId && !memories.some((memory) => memory.id === editingId)) {
      setEditingId(null);
      setDraft({});
    }
  }, [editingId, memories]);

  function startEdit(memory: Memory) {
    setEditingId(memory.id);
    setDraft({
      category: memory.category,
      content: memory.content,
      summary: memory.summary,
      importance: memory.importance,
      confidence: memory.confidence,
      status: memory.status,
    });
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft({});
  }

  function handleSave(event: FormEvent<HTMLFormElement>, memoryId: number) {
    event.preventDefault();
    if (!String(draft.content ?? "").trim()) {
      setError("记忆内容不能为空");
      return;
    }

    setError("");
    updateMemoryMutation.mutate(
      {
        memoryId,
        input: {
          category: draft.category,
          content: String(draft.content ?? "").trim(),
          summary: String(draft.summary ?? "").trim(),
          importance: Number(draft.importance),
          confidence: Number(draft.confidence),
          status: draft.status,
        },
      },
      {
        onError: (currentError) => {
          setError(currentError instanceof Error ? currentError.message : "保存记忆失败");
        },
      },
    );
  }

  function handleArchive(memoryId: number) {
    setError("");
    archiveMemoryMutation.mutate(memoryId, {
      onError: (currentError) => {
        setError(currentError instanceof Error ? currentError.message : "停用记忆失败");
      },
    });
  }

  function handleActivate(memoryId: number) {
    setError("");
    activateMemoryMutation.mutate(memoryId, {
      onError: (currentError) => {
        setError(currentError instanceof Error ? currentError.message : "启用记忆失败");
      },
    });
  }

  return (
    <section className="memory-panel">
      <MemoryToolbar
        category={category}
        onCategoryChange={setCategory}
        onRefresh={refreshMemories}
        onStatusChange={setStatus}
        status={status}
      />

      <div className="memory-summary-line">
        {memoriesQuery.isFetching ? "正在读取记忆..." : `${memories.length} 条${STATUS_LABELS[status]}记忆`}
        {status === "active" && activeCount > 0 ? `，其中 ${activeCount} 条会进入 L4` : ""}
        {status === "archived" && memories.length > 0 ? "，不会进入 L4" : ""}
      </div>

      {error ? <div className="job-drawer-error">{error}</div> : null}
      {memoriesQuery.error ? (
        <div className="job-drawer-error">
          {memoriesQuery.error instanceof Error ? memoriesQuery.error.message : "读取记忆失败"}
        </div>
      ) : null}

      <div className="memory-list">
        {!memoriesQuery.isFetching && memories.length === 0 ? (
          <EmptyState>暂无长期记忆</EmptyState>
        ) : null}

        {memories.map((memory) => (
          <MemoryCard
            draft={draft}
            isEditing={editingId === memory.id}
            isSaving={isSaving}
            key={memory.id}
            memory={memory}
            onActivate={handleActivate}
            onArchive={handleArchive}
            onCancelEdit={cancelEdit}
            onDraftChange={setDraft}
            onSave={handleSave}
            onStartEdit={startEdit}
          />
        ))}
      </div>
    </section>
  );
}
