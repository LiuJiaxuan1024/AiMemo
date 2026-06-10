import { Mic2, Radio, Volume2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  activateVoiceProfile,
  deleteVoiceProfile,
  designVoiceProfile,
  getElfVoiceMode,
  listVoiceProfiles,
  previewVoiceProfile,
  updateElfVoiceMode,
  updateVoiceProfile,
} from "./voiceApi";
import { VoiceDesignPanel } from "./VoiceDesignPanel";
import { VoiceProfileDetail } from "./VoiceProfileDetail";
import { VoiceProfileList } from "./VoiceProfileList";
import type { VoiceDesignInput, VoiceDesignResponse, VoiceProfile } from "./types";

export function VoiceWorkshop() {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isVoiceModeEnabled, setIsVoiceModeEnabled] = useState(false);
  const [isVoiceModeSaving, setIsVoiceModeSaving] = useState(false);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  async function refresh(nextSelectedId?: number) {
    const nextProfiles = await listVoiceProfiles();
    setProfiles(nextProfiles);
    if (nextSelectedId) {
      setSelectedId(nextSelectedId);
      return;
    }
    setSelectedId((current) => current ?? nextProfiles.find((profile) => profile.is_active)?.id ?? nextProfiles[0]?.id ?? null);
  }

  useEffect(() => {
    setIsLoading(true);
    Promise.all([refresh(), getElfVoiceMode().then((mode) => setIsVoiceModeEnabled(mode.enabled))])
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setIsLoading(false));
  }, []);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? profiles[0] ?? null,
    [profiles, selectedId],
  );

  async function run(callback: () => Promise<void>) {
    setError(null);
    try {
      await callback();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function handlePreview(profileId: number, text?: string) {
    await run(async () => {
      audioRef.current?.pause();
      const blob = await previewVoiceProfile(profileId, text, "soft");
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      setPlayingId(profileId);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        setPlayingId(null);
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        setPlayingId(null);
      };
      await audio.play();
    });
  }

  async function handleDesign(input: VoiceDesignInput): Promise<VoiceDesignResponse> {
    const response = await designVoiceProfile(input);
    await refresh(response.profile.id);
    return response;
  }

  async function handleVoiceModeToggle(enabled: boolean) {
    setError(null);
    setIsVoiceModeSaving(true);
    try {
      const mode = await updateElfVoiceMode(enabled);
      setIsVoiceModeEnabled(mode.enabled);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setIsVoiceModeSaving(false);
    }
  }

  return (
    <div className="voice-workshop">
      {error ? <div className="workshop-error-slot voice-workshop-error">{error}</div> : null}
      {isLoading ? (
        <div className="module-loading">正在加载语音工坊...</div>
      ) : (
        <>
          <section className="voice-studio-header">
            <div className="voice-studio-heading">
              <span className="voice-avatar">
                <Volume2 aria-hidden="true" size={22} />
              </span>
              <div>
                <span className="voice-kicker">Voice Studio</span>
                <h2>语音工坊</h2>
                <p>设计精灵声线，试听角色语气，并切换桌面语音对话模式。</p>
              </div>
            </div>
            <div className="voice-studio-stats">
              <span>
                <Volume2 aria-hidden="true" size={15} />
                {profiles.length} 条声线
              </span>
              <span>
                <Radio aria-hidden="true" size={15} />
                {selectedProfile?.name ?? "未选择"}
              </span>
            </div>
          </section>
          <section className="voice-workshop-note">
            <span>建议顺序</span>
            <p>先在左侧挑一个现成声线试听，再在中间调整描述和默认文本，右侧用于创建新的声线草稿。</p>
          </section>
          <section className="voice-mode-panel">
            <span className="voice-mode-icon">
              <Mic2 aria-hidden="true" size={18} />
            </span>
            <div>
              <strong>持续语音对话</strong>
              <span>开启后，桌面精灵会显示按住说话入口。普通文字消息仍然保持静默。</span>
            </div>
            <label className="voice-mode-switch">
              <input
                checked={isVoiceModeEnabled}
                disabled={isVoiceModeSaving}
                onChange={(event) => void handleVoiceModeToggle(event.target.checked)}
                type="checkbox"
              />
              <span>{isVoiceModeEnabled ? "已开启" : "已关闭"}</span>
            </label>
          </section>
          <div className="voice-workshop-grid">
            <VoiceProfileList activeId={selectedProfile?.id ?? null} profiles={profiles} onSelect={setSelectedId} />
            <VoiceProfileDetail
              isPlaying={playingId === selectedProfile?.id}
              onActivate={(profileId) => run(async () => refresh((await activateVoiceProfile(profileId)).id))}
              onDelete={(profileId) => run(async () => {
                await deleteVoiceProfile(profileId);
                await refresh();
              })}
              onPreview={handlePreview}
              onSave={(profileId, input) => run(async () => refresh((await updateVoiceProfile(profileId, input)).id))}
              profile={selectedProfile}
            />
            <VoiceDesignPanel
              onDesign={(input) => runWithResult(() => handleDesign(input), setError)}
              onDesigned={setSelectedId}
            />
          </div>
        </>
      )}
    </div>
  );
}

async function runWithResult<T>(callback: () => Promise<T>, setError: (message: string | null) => void): Promise<T> {
  setError(null);
  try {
    return await callback();
  } catch (caught) {
    const message = errorMessage(caught);
    setError(message);
    throw caught;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "语音工坊操作失败。";
}
