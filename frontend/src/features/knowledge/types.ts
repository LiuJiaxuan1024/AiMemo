export interface KnowledgeSpace {
  id: number;
  name: string;
  description: string;
  icon: string | null;
  status: "active" | "archived" | string;
  document_count: number;
  ready_document_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: number;
  space_id: number;
  title: string;
  source_type: string;
  source_uri: string | null;
  storage_path: string | null;
  original_filename: string | null;
  mime_type: string | null;
  content_hash: string;
  parser: string | null;
  chunk_strategy: string;
  status: string;
  chunk_count: number;
  token_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

export interface KnowledgeDocumentUploadResponse {
  document: KnowledgeDocument;
  job: {
    id: number;
    type: string;
    graph_name: string;
    status: string;
  } | null;
}

export interface KnowledgeChunk {
  id: number;
  space_id: number;
  document_id: number;
  chunk_index: number;
  text: string;
  summary: string | null;
  heading_path: string | null;
  page_number: number | null;
  source_offset: number | null;
  token_count: number;
  content_hash: string;
  embedding_status: string;
  embedding_error: string | null;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeSearchResultItem {
  chunk_id: number;
  space_id: number;
  space_name: string;
  document_id: number;
  document_title: string;
  text: string;
  score: number;
  score_source: string;
  heading_path: string[];
  page_number: number | null;
  source_uri: string | null;
  original_filename: string | null;
  retrieval_phase: string;
  distance: number | null;
}

export interface KnowledgeSearchResponse {
  query: string;
  top_k: number;
  mode: string;
  status: string;
  results: KnowledgeSearchResultItem[];
}
