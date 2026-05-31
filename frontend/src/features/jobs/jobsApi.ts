import type { Job, JobGraph } from "./types";

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

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("/api/jobs");
}

export function getJobGraph(jobId: number): Promise<JobGraph> {
  return request<JobGraph>(`/api/jobs/${jobId}/graph`);
}

export function retryJob(jobId: number): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/retry`, {
    method: "POST",
  });
}

export function deleteJob(jobId: number): Promise<void> {
  return request<void>(`/api/jobs/${jobId}`, {
    method: "DELETE",
  });
}
