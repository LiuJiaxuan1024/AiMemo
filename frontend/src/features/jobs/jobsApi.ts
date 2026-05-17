import type { Job, JobGraph } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("/api/jobs");
}

export function getJobGraph(jobId: number): Promise<JobGraph> {
  return request<JobGraph>(`/api/jobs/${jobId}/graph`);
}
