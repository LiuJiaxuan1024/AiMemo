export interface NoteListItem {
  id: number;
  title: string;
  content_hash: string;
  summary: string;
  tags: string[];
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
}

export interface UpdateNoteInput {
  title?: string;
  content?: string;
  content_markdown?: string;
  content_blocks?: string;
  content_format?: "markdown" | "blocknote";
}
