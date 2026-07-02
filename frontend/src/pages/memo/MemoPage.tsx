import { FormEvent, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import { NoteSidebar, type NoteFilter } from "../../features/notes/NoteSidebar";
import { NotesWorkspace } from "../../features/notes/NotesWorkspace";
import { isNoteProcessing } from "../../features/notes/noteUtils";
import {
  createNoteCategory,
  createNote,
  deleteNote,
  getNote,
  hardDeleteNote,
  listNoteCategories,
  listNoteTags,
  listNotes,
  restoreNote,
  updateNote,
} from "../../services/api";
import { Button } from "../../shared/ui";
import type { Note, UpdateNoteInput } from "../../types/note";

export function MemoPage() {
  const queryClient = useQueryClient();
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [contentBlocks, setContentBlocks] = useState("");
  const [error, setError] = useState("");
  const [noteMode, setNoteMode] = useState<"active" | "deleted">("active");
  const [workspaceMode, setWorkspaceMode] = useState<"compose" | "read">("compose");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState<"updated" | "created" | "title">("updated");
  const [activeFilter, setActiveFilter] = useState<NoteFilter>({ type: "all" });
  const [isCategoryDialogOpen, setIsCategoryDialogOpen] = useState(false);
  const [categoryNameDraft, setCategoryNameDraft] = useState("");

  const categoriesQuery = useQuery({
    queryKey: ["note-categories"],
    queryFn: listNoteCategories,
  });
  const tagsQuery = useQuery({
    queryKey: ["note-tags"],
    queryFn: listNoteTags,
  });
  const noteListParams = useMemo(() => {
    if (noteMode === "deleted") {
      return { status: "deleted" };
    }
    if (activeFilter.type === "uncategorized") {
      return { status: "active", categoryId: "uncategorized" as const };
    }
    if (activeFilter.type === "favorite") {
      return { status: "active", favorite: true };
    }
    if (activeFilter.type === "pinned") {
      return { status: "active", pinned: true };
    }
    if (activeFilter.type === "category") {
      return { status: "active", categoryId: activeFilter.id };
    }
    if (activeFilter.type === "tag") {
      return { status: "active", tag: activeFilter.name };
    }
    return { status: "active" };
  }, [activeFilter, noteMode]);
  const noteListViewKey = useMemo(() => {
    if (noteMode === "deleted") {
      return "deleted";
    }
    if (activeFilter.type === "category") {
      return `category:${activeFilter.id}`;
    }
    if (activeFilter.type === "tag") {
      return `tag:${activeFilter.name}`;
    }
    return activeFilter.type;
  }, [activeFilter, noteMode]);

  const notesQuery = useQuery({
    queryKey: ["notes", noteListParams],
    queryFn: () => listNotes(noteListParams),
    placeholderData: keepPreviousData,
    // 后台整理和 embedding 进行中时保持轻量轮询，完成后自动安静下来。
    refetchInterval: (query) => {
      const currentNotes = query.state.data ?? [];
      return currentNotes.some(isNoteProcessing) ? 3000 : false;
    },
  });
  const notes = notesQuery.data ?? [];
  const isSwitchingNoteList = notesQuery.isPlaceholderData && notesQuery.isFetching;
  const visibleNotes = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    const filtered = normalizedQuery
      ? notes.filter((note) => {
          const haystack = [note.title, note.summary, ...note.tags].join(" ").toLowerCase();
          return haystack.includes(normalizedQuery);
        })
      : notes;
    return [...filtered].sort((left, right) => {
      if (left.pinned_at && !right.pinned_at) {
        return -1;
      }
      if (!left.pinned_at && right.pinned_at) {
        return 1;
      }
      if (sortMode === "title") {
        return left.title.localeCompare(right.title, "zh-CN");
      }
      const key = sortMode === "created" ? "created_at" : "updated_at";
      return new Date(right[key]).getTime() - new Date(left[key]).getTime();
    });
  }, [notes, searchQuery, sortMode]);
  const noteStats = useMemo(() => {
    const processingCount = notes.filter(isNoteProcessing).length;
    const tagCount = new Set(notes.flatMap((note) => note.tags)).size;
    return {
      processingCount,
      tagCount,
      totalCount: notes.length,
    };
  }, [notes]);

  const selectedNoteQuery = useQuery({
    enabled: Boolean(selectedNoteId),
    queryKey: ["notes", selectedNoteId],
    queryFn: () => getNote(Number(selectedNoteId)),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (isNoteProcessing(query.state.data) ? 3000 : false),
  });
  const selectedNote = selectedNoteId ? selectedNoteQuery.data ?? null : null;

  const createNoteMutation = useMutation({
    mutationFn: createNote,
    onSuccess: async (note) => {
      setTitle("");
      setContent("");
      setContentBlocks("");
      setSelectedNoteId(note.id);
      setWorkspaceMode("read");
      queryClient.setQueryData(["notes", note.id], note);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
      await queryClient.invalidateQueries({ queryKey: ["note-categories"] });
      await queryClient.invalidateQueries({ queryKey: ["note-tags"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "保存笔记失败");
    },
  });

  const updateNoteMutation = useMutation({
    mutationFn: ({ noteId, input }: { noteId: number; input: UpdateNoteInput }) =>
      updateNote(noteId, input),
    onSuccess: async (note) => {
      queryClient.setQueryData(["notes", note.id], note);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
      await queryClient.invalidateQueries({ queryKey: ["note-categories"] });
      await queryClient.invalidateQueries({ queryKey: ["note-tags"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "更新笔记失败");
    },
  });

  const createCategoryMutation = useMutation({
    mutationFn: createNoteCategory,
    onSuccess: async () => {
      setCategoryNameDraft("");
      setIsCategoryDialogOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["note-categories"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "新建分类失败");
    },
  });

  const deleteNoteMutation = useMutation({
    mutationFn: deleteNote,
    onSuccess: async () => {
      setSelectedNoteId(null);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "删除笔记失败");
    },
  });

  const restoreNoteMutation = useMutation({
    mutationFn: restoreNote,
    onSuccess: async () => {
      setSelectedNoteId(null);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "恢复笔记失败");
    },
  });

  const hardDeleteNoteMutation = useMutation({
    mutationFn: hardDeleteNote,
    onSuccess: async () => {
      setSelectedNoteId(null);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "永久删除笔记失败");
    },
  });

  const isMutatingNote =
    updateNoteMutation.isPending ||
    deleteNoteMutation.isPending ||
    restoreNoteMutation.isPending ||
    hardDeleteNoteMutation.isPending;

  useEffect(() => {
    if (visibleNotes.length === 0 || workspaceMode === "compose") {
      setSelectedNoteId(null);
      return;
    }

    if (!selectedNoteId || !visibleNotes.some((note) => note.id === selectedNoteId)) {
      setSelectedNoteId(visibleNotes[0].id);
    }
  }, [visibleNotes, selectedNoteId, workspaceMode]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!content.trim()) {
      setError("笔记内容不能为空");
      return;
    }

    setError("");
    createNoteMutation.mutate({
      title: title.trim(),
      content: content.trim(),
      content_markdown: content.trim(),
      content_blocks: contentBlocks,
      content_format: "blocknote",
    });
  }

  function handleContentChange(value: { blocksJson: string; markdown: string }) {
    setContent(value.markdown);
    setContentBlocks(value.blocksJson);
  }

  function handleSelect(noteId: number) {
    setError("");
    setSelectedNoteId(noteId);
    setWorkspaceMode("read");
  }

  function handleNoteModeChange(nextMode: "active" | "deleted") {
    setError("");
    setNoteMode(nextMode);
    if (nextMode === "deleted") {
      setActiveFilter({ type: "all" });
    }
    setSelectedNoteId(null);
    setWorkspaceMode(nextMode === "active" ? "compose" : "read");
  }

  function handleFilterChange(nextFilter: NoteFilter) {
    setError("");
    setActiveFilter(nextFilter);
    setWorkspaceMode("read");
  }

  function handleComposeMode() {
    setError("");
    setSelectedNoteId(null);
    setWorkspaceMode("compose");
  }

  function handleUpdateNote(note: Note, input: UpdateNoteInput) {
    const nextContent = (input.content_markdown ?? input.content ?? "").trim();
    if (!nextContent) {
      setError("笔记内容不能为空");
      return;
    }
    const nextTitle = (input.title ?? "").trim();
    setError("");
    updateNoteMutation.mutate({
      noteId: note.id,
      input: {
        title: nextTitle,
        content: nextContent,
        content_markdown: nextContent,
        content_blocks: input.content_blocks ?? note.content_blocks ?? "",
        content_format: input.content_format ?? "blocknote",
      },
    });
  }

  function handleUpdateNoteOrganization(note: Note, input: UpdateNoteInput) {
    setError("");
    updateNoteMutation.mutate({
      noteId: note.id,
      input,
    });
  }

  function handleCreateCategory() {
    setError("");
    setCategoryNameDraft("");
    setIsCategoryDialogOpen(true);
  }

  function handleSubmitCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedName = categoryNameDraft.trim();
    if (!normalizedName) {
      return;
    }
    setError("");
    createCategoryMutation.mutate({ name: normalizedName });
  }

  function handleCloseCategoryDialog() {
    if (createCategoryMutation.isPending) {
      return;
    }
    setIsCategoryDialogOpen(false);
    setCategoryNameDraft("");
  }

  function handleDeleteNote(note: Note) {
    setError("");
    deleteNoteMutation.mutate(note.id);
  }

  function handleRestoreNote(note: Note) {
    setError("");
    restoreNoteMutation.mutate(note.id);
  }

  function handleHardDeleteNote(note: Note) {
    const confirmed = window.confirm("确认永久删除这条笔记吗？删除后无法恢复。");
    if (!confirmed) {
      return;
    }
    setError("");
    hardDeleteNoteMutation.mutate(note.id);
  }

  const requestError = notesQuery.error ?? selectedNoteQuery.error;
  const noteError =
    requestError instanceof Error
      ? requestError.message
      : requestError
        ? "读取笔记失败"
        : "";
  const visibleError = error || noteError;

  return (
    <section className="memo-page app-shell">
      <NoteSidebar
        activeFilter={activeFilter}
        categories={categoriesQuery.data ?? []}
        isLoading={notesQuery.isFetching && notes.length === 0}
        isTransitioning={isSwitchingNoteList}
        listViewKey={noteListViewKey}
        mode={noteMode}
        notes={visibleNotes}
        onCreateCategory={handleCreateCategory}
        onFilterChange={handleFilterChange}
        onSearchChange={setSearchQuery}
        onModeChange={handleNoteModeChange}
        onSelectNote={handleSelect}
        onSortChange={setSortMode}
        searchQuery={searchQuery}
        selectedNote={selectedNote}
        sortMode={sortMode}
        stats={noteStats}
        tags={tagsQuery.data ?? []}
      />

      <section className="workspace memo-workspace">
        <NotesWorkspace
          categories={categoriesQuery.data ?? []}
          content={content}
          contentBlocks={contentBlocks}
          error={visibleError}
          isMutatingNote={isMutatingNote}
          isSaving={createNoteMutation.isPending}
          isTransitioning={isSwitchingNoteList}
          noteMode={noteMode}
          onContentChange={handleContentChange}
          onDeleteNote={handleDeleteNote}
          onHardDeleteNote={handleHardDeleteNote}
          onRestoreNote={handleRestoreNote}
          onSubmit={handleSubmit}
          onTitleChange={setTitle}
          onUpdateNote={handleUpdateNote}
          onUpdateNoteOrganization={handleUpdateNoteOrganization}
          onWriteNote={handleComposeMode}
          selectedNote={selectedNote}
          title={title}
          workspaceMode={workspaceMode}
        />
      </section>
      {isCategoryDialogOpen ? (
        <div className="memo-dialog-backdrop" role="presentation" onMouseDown={handleCloseCategoryDialog}>
          <form
            aria-label="新建分类"
            aria-modal="true"
            className="memo-dialog"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={handleSubmitCategory}
            role="dialog"
          >
            <header>
              <div>
                <strong>新建分类</strong>
                <p>给一组笔记一个稳定入口。</p>
              </div>
              <button
                aria-label="关闭"
                className="memo-dialog-close"
                disabled={createCategoryMutation.isPending}
                onClick={handleCloseCategoryDialog}
                type="button"
              >
                <X aria-hidden="true" size={16} />
              </button>
            </header>
            <label className="memo-dialog-field">
              <span>分类名称</span>
              <input
                autoFocus
                onChange={(event) => setCategoryNameDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    handleCloseCategoryDialog();
                  }
                }}
                placeholder="例如：项目、读书、生活"
                value={categoryNameDraft}
              />
            </label>
            <footer>
              <Button disabled={createCategoryMutation.isPending} onClick={handleCloseCategoryDialog} size="sm" type="button">
                取消
              </Button>
              <Button disabled={createCategoryMutation.isPending || !categoryNameDraft.trim()} size="sm" type="submit" variant="primary">
                {createCategoryMutation.isPending ? "创建中" : "创建"}
              </Button>
            </footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}
