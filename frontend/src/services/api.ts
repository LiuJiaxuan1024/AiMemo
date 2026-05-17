import type { CreateNoteInput, Note, NoteListItem } from "../types/note";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function listNotes(): Promise<NoteListItem[]> {
  return request<NoteListItem[]>("/api/notes");
}

export function getNote(id: number): Promise<Note> {
  return request<Note>(`/api/notes/${id}`);
}

export function createNote(input: CreateNoteInput): Promise<Note> {
  return request<Note>("/api/notes", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
