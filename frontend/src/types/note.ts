export interface NoteListItem {
  id: number;
  title: string;
  content_hash: string;
  summary: string;
  tags: string[];
  category_id: number | null;
  category_name: string;
  is_favorite: boolean;
  pinned_at: string | null;
  archived_at: string | null;
  status: "active" | "deleted" | string;
  processing_status: "pending" | "processing" | "completed" | "failed" | "canceled" | string;
  processing_error: string;
  processed_at: string | null;
  embedding_status: "pending" | "processing" | "completed" | "failed" | "canceled" | string;
  embedding_error: string;
  embedded_at: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Note extends NoteListItem {
  content: string;
  content_markdown: string;
  content_blocks: string;
  content_format: "markdown" | "blocknote" | string;
  content_version: number;
}

export interface CreateNoteInput {
  title?: string;
  content: string;
  content_markdown?: string;
  content_blocks?: string;
  content_format?: "markdown" | "blocknote";
  summary?: string;
  tags?: string[];
  category_id?: number | null;
  is_favorite?: boolean;
  pinned?: boolean;
}

export interface UpdateNoteInput {
  title?: string;
  content?: string;
  content_markdown?: string;
  content_blocks?: string;
  content_format?: "markdown" | "blocknote";
  category_id?: number | null;
  tags?: string[];
  is_favorite?: boolean;
  pinned?: boolean;
}

export interface NoteCategory {
  id: number;
  name: string;
  description: string;
  color: string;
  sort_order: number;
  status: "active" | "deleted" | string;
  note_count: number;
  created_at: string;
  updated_at: string;
}

export interface NoteTag {
  name: string;
  note_count: number;
}
