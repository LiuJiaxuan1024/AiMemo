import type { CreateNoteInput, Note, NoteCategory, NoteListItem, NoteTag, UpdateNoteInput } from "../types/note";

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

export interface ListNotesParams {
  status?: string;
  categoryId?: number | "uncategorized" | null;
  tag?: string | null;
  favorite?: boolean | null;
  pinned?: boolean | null;
  processingStatus?: string | null;
}

export function listNotes(statusOrParams: string | ListNotesParams = "active"): Promise<NoteListItem[]> {
  const params = typeof statusOrParams === "string" ? { status: statusOrParams } : statusOrParams;
  const searchParams = new URLSearchParams();
  searchParams.set("status", params.status ?? "active");
  if (params.categoryId !== undefined && params.categoryId !== null) {
    searchParams.set("category_id", String(params.categoryId));
  }
  if (params.tag) {
    searchParams.set("tag", params.tag);
  }
  if (params.favorite !== undefined && params.favorite !== null) {
    searchParams.set("favorite", String(params.favorite));
  }
  if (params.pinned !== undefined && params.pinned !== null) {
    searchParams.set("pinned", String(params.pinned));
  }
  if (params.processingStatus) {
    searchParams.set("processing_status", params.processingStatus);
  }
  return request<NoteListItem[]>(`/api/notes?${searchParams.toString()}`);
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

export function listNoteCategories(): Promise<NoteCategory[]> {
  return request<NoteCategory[]>("/api/note-categories");
}

export function createNoteCategory(input: { name: string; description?: string; color?: string }): Promise<NoteCategory> {
  return request<NoteCategory>("/api/note-categories", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateNoteCategory(
  id: number,
  input: { name?: string; description?: string; color?: string; sort_order?: number },
): Promise<NoteCategory> {
  return request<NoteCategory>(`/api/note-categories/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export async function deleteNoteCategory(id: number): Promise<void> {
  await request<void>(`/api/note-categories/${id}`, {
    method: "DELETE",
  });
}

export function listNoteTags(): Promise<NoteTag[]> {
  return request<NoteTag[]>("/api/note-tags");
}
