import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ChatWindow } from "./features/chat/ChatWindow";
import { JobDrawer } from "./features/jobs/JobDrawer";
import { NoteSidebar } from "./features/notes/NoteSidebar";
import { NotesWorkspace } from "./features/notes/NotesWorkspace";
import { isNoteProcessing } from "./features/notes/noteUtils";
import { createNote, getNote, listNotes } from "./services/api";
import { SegmentedTabs } from "./shared/ui";

export default function App() {
  const queryClient = useQueryClient();
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"notes" | "chat">("notes");

  const notesQuery = useQuery({
    queryKey: ["notes"],
    queryFn: listNotes,
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
        notes={notes}
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
            isSaving={createNoteMutation.isPending}
            onContentChange={setContent}
            onSubmit={handleSubmit}
            onTitleChange={setTitle}
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
