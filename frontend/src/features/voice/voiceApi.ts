import type {
  ElfVoiceMode,
  VoiceDesignInput,
  VoiceDesignResponse,
  VoiceProfile,
  VoiceProfileUpdateInput,
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

export function listVoiceProfiles(): Promise<VoiceProfile[]> {
  return request<VoiceProfile[]>("/api/voice/profiles");
}

export function updateVoiceProfile(profileId: number, input: VoiceProfileUpdateInput): Promise<VoiceProfile> {
  return request<VoiceProfile>(`/api/voice/profiles/${profileId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function activateVoiceProfile(profileId: number): Promise<VoiceProfile> {
  return request<VoiceProfile>(`/api/voice/profiles/${profileId}/activate`, {
    method: "POST",
  });
}

export async function deleteVoiceProfile(profileId: number): Promise<void> {
  await request<void>(`/api/voice/profiles/${profileId}`, {
    method: "DELETE",
  });
}

export function designVoiceProfile(input: VoiceDesignInput): Promise<VoiceDesignResponse> {
  return request<VoiceDesignResponse>("/api/voice/profiles/design", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function previewVoiceProfile(profileId: number, text?: string, emoji?: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/voice/profiles/${profileId}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, emoji }),
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.blob();
}

export function getElfVoiceMode(): Promise<ElfVoiceMode> {
  return request<ElfVoiceMode>("/api/elf/voice/mode");
}

export function updateElfVoiceMode(enabled: boolean): Promise<ElfVoiceMode> {
  return request<ElfVoiceMode>("/api/elf/voice/mode", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}
