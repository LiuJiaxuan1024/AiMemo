const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface RuntimeConfig {
  elf: {
    enabled: boolean;
  };
}

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await fetch(`${API_BASE_URL}/api/config/runtime`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<RuntimeConfig>;
}
