import type {
  KnowledgeChunk,
  KnowledgeDocument,
  KnowledgeDocumentUploadResponse,
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

export function deleteKnowledgeDocument(documentId: number): Promise<KnowledgeDocument> {
  return request<KnowledgeDocument>(`/api/knowledge/documents/${documentId}`, {
    method: "DELETE",
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
