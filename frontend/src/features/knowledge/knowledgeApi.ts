import type {
  KnowledgeChunk,
  KnowledgeDocument,
  KnowledgeDocumentRetryResponse,
  KnowledgeDocumentUploadResponse,
  KnowledgeImageAsset,
  KnowledgeImageAssetRetryResponse,
  KnowledgeOcrInstallResult,
  KnowledgeOcrStatus,
  KnowledgeSearchResponse,
  KnowledgeSpace,
} from "./types";

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

export function listKnowledgeSpaces(includeArchived = false): Promise<KnowledgeSpace[]> {
  return request<KnowledgeSpace[]>(`/api/knowledge/spaces?include_archived=${includeArchived ? "true" : "false"}`);
}

export function createKnowledgeSpace(input: {
  name: string;
  description?: string;
  icon?: string | null;
}): Promise<KnowledgeSpace> {
  return request<KnowledgeSpace>("/api/knowledge/spaces", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function archiveKnowledgeSpace(spaceId: number): Promise<KnowledgeSpace> {
  return request<KnowledgeSpace>(`/api/knowledge/spaces/${spaceId}`, {
    method: "DELETE",
  });
}

export function listKnowledgeDocuments(spaceId: number): Promise<KnowledgeDocument[]> {
  return request<KnowledgeDocument[]>(`/api/knowledge/spaces/${spaceId}/documents`);
}

export function getKnowledgeOcrStatus(): Promise<KnowledgeOcrStatus> {
  return request<KnowledgeOcrStatus>("/api/knowledge/ocr/status");
}

export function installKnowledgeOcr(): Promise<KnowledgeOcrInstallResult> {
  return request<KnowledgeOcrInstallResult>("/api/knowledge/ocr/install", {
    method: "POST",
    body: JSON.stringify({ confirm_install: true }),
  });
}

export async function uploadKnowledgeDocument(spaceId: number, file: File, title?: string): Promise<KnowledgeDocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (title?.trim()) {
    formData.append("title", title.trim());
  }

  const response = await fetch(`${API_BASE_URL}/api/knowledge/spaces/${spaceId}/documents/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<KnowledgeDocumentUploadResponse>;
}

export function listKnowledgeChunks(documentId: number): Promise<KnowledgeChunk[]> {
  return request<KnowledgeChunk[]>(`/api/knowledge/documents/${documentId}/chunks`);
}

export function listKnowledgeImageAssets(documentId: number): Promise<KnowledgeImageAsset[]> {
  return request<KnowledgeImageAsset[]>(`/api/knowledge/documents/${documentId}/image-assets`);
}

export function deleteKnowledgeDocument(documentId: number): Promise<KnowledgeDocument> {
  return request<KnowledgeDocument>(`/api/knowledge/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function retryKnowledgeDocumentProcessing(documentId: number): Promise<KnowledgeDocumentRetryResponse> {
  return request<KnowledgeDocumentRetryResponse>(`/api/knowledge/documents/${documentId}/retry-processing`, {
    method: "POST",
  });
}

export function retryKnowledgeDocumentFailedImages(
  documentId: number,
  input: { onlyRetryable?: boolean; maxAssets?: number } = {},
): Promise<KnowledgeImageAssetRetryResponse> {
  return request<KnowledgeImageAssetRetryResponse>(`/api/knowledge/documents/${documentId}/image-assets/retry-failed`, {
    method: "POST",
    body: JSON.stringify({
      only_retryable: input.onlyRetryable ?? true,
      max_assets: input.maxAssets ?? 20,
    }),
  });
}

export function retryKnowledgeImageAsset(
  imageAssetId: number,
  input: { onlyRetryable?: boolean } = {},
): Promise<KnowledgeImageAssetRetryResponse> {
  return request<KnowledgeImageAssetRetryResponse>(`/api/knowledge/image-assets/${imageAssetId}/retry`, {
    method: "POST",
    body: JSON.stringify({
      only_retryable: input.onlyRetryable ?? true,
      max_assets: 1,
    }),
  });
}

export function searchKnowledge(input: {
  query: string;
  spaceId?: number | null;
  topK?: number;
  mode?: "hybrid" | "vector" | "keyword";
}): Promise<KnowledgeSearchResponse> {
  return request<KnowledgeSearchResponse>("/api/knowledge/search", {
    method: "POST",
    body: JSON.stringify({
      query: input.query,
      space_id: input.spaceId ?? null,
      top_k: input.topK ?? 8,
      mode: input.mode ?? "hybrid",
    }),
  });
}
