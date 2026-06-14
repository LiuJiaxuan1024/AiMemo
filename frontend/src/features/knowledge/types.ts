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
  text_chunk_count: number;
  image_asset_count: number;
  image_asset_processed_count: number;
  image_text_chunk_count: number;
  image_asset_failed_count: number;
  image_asset_warning_count: number;
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

export interface KnowledgeDocumentRetryResponse {
  document: KnowledgeDocument;
  job: {
    id: number;
    type: string;
    graph_name: string;
    status: string;
  } | null;
}

export interface KnowledgeImageAsset {
  id: number;
  space_id: number;
  document_id: number;
  asset_id: string;
  asset_uid: string;
  parser: string;
  location_label: string;
  page_number: number | null;
  source_offset: number | null;
  heading_path: string[];
  alt_text: string | null;
  caption: string | null;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  bbox: string | null;
  content_hash: string;
  byte_size: number;
  status: string;
  retryable: boolean;
  attempt_count: number;
  extractor: string | null;
  image_type: string | null;
  confidence: number | null;
  should_index: boolean | null;
  error_code: string | null;
  error_message: string | null;
  chunk_ids: number[];
  last_attempted_at: string | null;
  processed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeImageAssetRetryResponse {
  document: KnowledgeDocument;
  job: {
    id: number;
    type: string;
    graph_name: string;
    status: string;
  } | null;
  queued_asset_count: number;
}

export interface KnowledgeOcrStatus {
  mode: string;
  ready: boolean;
  status: string;
  tesseract_available: boolean;
  tesseract_path: string | null;
  tesseract_version: string | null;
  tessdata_path: string | null;
  available_languages: string[];
  required_languages: string[];
  missing_languages: string[];
  install_running: boolean;
  install_processes: string[];
  install_task_ids: string[];
  python_packages: Record<string, boolean>;
  message: string;
}

export interface KnowledgeOcrInstallResult {
  supported: boolean;
  installed: boolean;
  command_results: Array<{
    task_id: string | null;
    command: string;
    exit_code: number | null;
    stdout: string;
    stderr: string;
    message: string;
  }>;
  install_task_id: string | null;
  before_status: KnowledgeOcrStatus;
  after_status: KnowledgeOcrStatus;
  message: string;
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
