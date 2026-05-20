import type { Memory, MemoryDetail, MemoryStatus, MemoryUpdateInput } from "./types";

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

export function listMemories(params: {
  status: MemoryStatus;
  category?: string;
  limit?: number;
  offset?: number;
}): Promise<Memory[]> {
  const search = new URLSearchParams({
    status: params.status,
    level: "4",
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.category) {
    search.set("category", params.category);
  }
  return request<Memory[]>(`/api/memories?${search.toString()}`);
}

export function updateMemory(memoryId: number, input: MemoryUpdateInput): Promise<Memory> {
  return request<Memory>(`/api/memories/${memoryId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function getMemoryDetail(memoryId: number): Promise<MemoryDetail> {
  return request<MemoryDetail>(`/api/memories/${memoryId}/detail`);
}

export function archiveMemory(memoryId: number): Promise<Memory> {
  return request<Memory>(`/api/memories/${memoryId}`, {
    method: "DELETE",
  });
}

export function activateMemory(memoryId: number): Promise<Memory> {
  return updateMemory(memoryId, { status: "active" });
}

export async function deleteDisabledMemory(memoryId: number): Promise<void> {
  await request<void>(`/api/memories/${memoryId}/hard`, {
    method: "DELETE",
  });
}
