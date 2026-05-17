export interface NoteListItem {
  id: number;
  title: string;
  summary: string;
  tags: string[];
  processing_status: "pending" | "processing" | "completed" | "failed" | "canceled" | string;
  processing_error: string;
  processed_at: string | null;
  embedding_status: "pending" | "processing" | "completed" | "failed" | "canceled" | string;
  embedding_error: string;
  embedded_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Note extends NoteListItem {
  content: string;
}

export interface CreateNoteInput {
  title?: string;
  content: string;
  summary?: string;
  tags?: string[];
}
