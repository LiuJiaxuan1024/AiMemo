import type { CreateNoteInput, Note, NoteListItem, UpdateNoteInput } from "../types/note";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

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

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function listNotes(status = "active"): Promise<NoteListItem[]> {
  return request<NoteListItem[]>(`/api/notes?status=${encodeURIComponent(status)}`);
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

export function updateNote(id: number, input: UpdateNoteInput): Promise<Note> {
  return request<Note>(`/api/notes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteNote(id: number): Promise<Note> {
  return request<Note>(`/api/notes/${id}`, {
    method: "DELETE",
  });
}

export function restoreNote(id: number): Promise<Note> {
  return request<Note>(`/api/notes/${id}/restore`, {
    method: "POST",
  });
}

export async function hardDeleteNote(id: number): Promise<void> {
  await request<void>(`/api/notes/${id}/hard`, {
    method: "DELETE",
  });
}
