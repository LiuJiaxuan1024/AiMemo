export type VoiceProfileStatus = "draft" | "generating" | "ready" | "failed";
export type VoiceProfileSourceType = "builtin" | "designed" | "cloned" | "draft";

export interface VoiceProfile {
  id: number;
  name: string;
  description: string;
  voice_prompt: string;
  style_prompt: string;
  preview_text: string;
  language: string;
  speed: number;
  energy: number;
  emotion_bias: Record<string, unknown>;
  remote_provider: string;
  remote_model: string;
  remote_target_model: string;
  remote_voice_id: string;
  source_type: VoiceProfileSourceType;
  status: VoiceProfileStatus;
  last_error: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface VoiceProfileUpdateInput {
  name?: string;
  description?: string;
  voice_prompt?: string;
  style_prompt?: string;
  preview_text?: string;
  language?: string;
  speed?: number;
  energy?: number;
  emotion_bias?: Record<string, unknown>;
  remote_voice_id?: string;
  status?: VoiceProfileStatus;
}

export interface VoiceDesignInput {
  description: string;
  name_hint?: string;
  preview_text?: string;
  language?: string;
}

export interface VoiceDesignResponse {
  profile: VoiceProfile;
  voice_prompt: string;
  warnings: string[];
}

export interface ElfVoiceMode {
  enabled: boolean;
}
