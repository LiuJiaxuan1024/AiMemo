import type { BackgroundTask, BackgroundTaskOutput } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
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

export function listBackgroundTasks(): Promise<BackgroundTask[]> {
  return request<BackgroundTask[]>("/api/background_tasks?limit=200");
}

export function getBackgroundTaskOutput(
  taskId: string,
  sinceLine: number = 0,
  maxLines: number = 200,
): Promise<BackgroundTaskOutput> {
  const params = new URLSearchParams({
    since_line: String(sinceLine),
    max_lines: String(maxLines),
  });
  return request<BackgroundTaskOutput>(`/api/background_tasks/${taskId}/output?${params}`);
}

export function killBackgroundTask(taskId: string): Promise<BackgroundTask> {
  return request<BackgroundTask>(`/api/background_tasks/${taskId}/kill`, {
    method: "POST",
  });
}

export function pruneBackgroundTask(taskId: string): Promise<void> {
  return request<void>(`/api/background_tasks/${taskId}`, {
    method: "DELETE",
  });
}
