import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ChatWindow } from "./features/chat/ChatWindow";
import { JobDrawer } from "./features/jobs/JobDrawer";
import { NoteSidebar } from "./features/notes/NoteSidebar";
import { NotesWorkspace } from "./features/notes/NotesWorkspace";
import { isNoteProcessing } from "./features/notes/noteUtils";
import {
  createNote,
  deleteNote,
  getNote,
  hardDeleteNote,
  listNotes,
  restoreNote,
  updateNote,
} from "./services/api";
import { SegmentedTabs } from "./shared/ui";
import type { Note } from "./types/note";

export default function App() {
  const queryClient = useQueryClient();
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"notes" | "chat">("notes");
  const [noteMode, setNoteMode] = useState<"active" | "deleted">("active");

  const notesQuery = useQuery({
    queryKey: ["notes", noteMode],
    queryFn: () => listNotes(noteMode),
    // 后台整理和 embedding 进行中时保持轻量轮询，完成后自动安静下来。
    refetchInterval: (query) => {
      const currentNotes = query.state.data ?? [];
      return currentNotes.some(isNoteProcessing) ? 3000 : false;
    },
  });
  const notes = notesQuery.data ?? [];

  const selectedNoteQuery = useQuery({
    enabled: Boolean(selectedNoteId),
    queryKey: ["notes", selectedNoteId],
    queryFn: () => getNote(Number(selectedNoteId)),
    refetchInterval: (query) => (isNoteProcessing(query.state.data) ? 3000 : false),
  });
  const selectedNote = selectedNoteQuery.data ?? null;

  const createNoteMutation = useMutation({
    mutationFn: createNote,
    onSuccess: async (note) => {
      setTitle("");
      setContent("");
      setSelectedNoteId(note.id);
      queryClient.setQueryData(["notes", note.id], note);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "保存笔记失败");
    },
  });
  const updateNoteMutation = useMutation({
    mutationFn: ({ noteId, input }: { noteId: number; input: { title: string; content: string } }) =>
      updateNote(noteId, input),
    onSuccess: async (note) => {
      queryClient.setQueryData(["notes", note.id], note);
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (currentError) => {
      setError(currentError instanceof Error ? currentError.message : "更新笔记失败");
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
    if (notes.length === 0) {
      setSelectedNoteId(null);
      return;
    }

    if (!selectedNoteId || !notes.some((note) => note.id === selectedNoteId)) {
      setSelectedNoteId(notes[0].id);
    }
  }, [notes, selectedNoteId]);

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
    });
  }

  function handleSelect(noteId: number) {
    setError("");
    setSelectedNoteId(noteId);
  }

  function handleNoteModeChange(nextMode: "active" | "deleted") {
    setError("");
    setNoteMode(nextMode);
    setSelectedNoteId(null);
  }

  function handleUpdateNote(note: Note, input: { title: string; content: string }) {
    if (!input.content.trim()) {
      setError("笔记内容不能为空");
      return;
    }
    setError("");
    updateNoteMutation.mutate({
      noteId: note.id,
      input: {
        title: input.title.trim(),
        content: input.content.trim(),
      },
    });
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
    <main className="app-shell">
      <NoteSidebar
        isLoading={notesQuery.isFetching && notes.length === 0}
        mode={noteMode}
        notes={notes}
        onModeChange={handleNoteModeChange}
        onSelectNote={handleSelect}
        selectedNote={selectedNote}
      />

      <section className="workspace">
        <SegmentedTabs
          ariaLabel="工作区切换"
          items={[
            { label: "笔记", value: "notes" },
            { label: "对话", value: "chat" },
          ]}
          onChange={setMode}
          value={mode}
        />

        {mode === "notes" ? (
          <NotesWorkspace
            content={content}
            error={visibleError}
            isMutatingNote={isMutatingNote}
            isSaving={createNoteMutation.isPending}
            noteMode={noteMode}
            onContentChange={setContent}
            onDeleteNote={handleDeleteNote}
            onHardDeleteNote={handleHardDeleteNote}
            onRestoreNote={handleRestoreNote}
            onSubmit={handleSubmit}
            onTitleChange={setTitle}
            onUpdateNote={handleUpdateNote}
            selectedNote={selectedNote}
            title={title}
          />
        ) : (
          <ChatWindow />
        )}
      </section>
      <JobDrawer />
    </main>
  );
}
