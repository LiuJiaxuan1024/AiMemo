import { Check, Library, Sparkles, Volume2 } from "lucide-react";

import { Badge } from "../../shared/ui";
import type { VoiceProfile } from "./types";

interface VoiceProfileListProps {
  activeId: number | null;
  profiles: VoiceProfile[];
  onSelect: (profileId: number) => void;
}

const statusTone = {
  draft: "neutral",
  generating: "info",
  ready: "success",
  failed: "danger",
} as const;

const statusLabel = {
  draft: "草稿",
  generating: "生成中",
  ready: "可用",
  failed: "失败",
};

export function VoiceProfileList({ activeId, profiles, onSelect }: VoiceProfileListProps) {
  return (
    <aside className="voice-profile-list">
      <div className="voice-panel-title">
        <span className="voice-panel-title-main">
          <Library aria-hidden="true" size={16} />
          <span>声线库</span>
        </span>
        <small>{profiles.length} 条</small>
      </div>

      <div className="voice-profile-items">
        {profiles.map((profile) => (
          <button
            className={`voice-profile-item ${profile.id === activeId ? "selected" : ""}`}
            key={profile.id}
            onClick={() => onSelect(profile.id)}
            type="button"
          >
            <span className="voice-profile-item-main">
              <span className="voice-profile-name">{profile.name}</span>
              {profile.description ? <span className="voice-profile-description">{profile.description}</span> : null}
              <span className="voice-profile-meta">
                {profile.source_type === "designed" ? (
                  <Sparkles aria-hidden="true" size={13} />
                ) : (
                  <Volume2 aria-hidden="true" size={13} />
                )}
                {profile.source_type}
              </span>
            </span>
            <span className="voice-profile-item-side">
              {profile.is_active ? <Check aria-label="默认声线" size={16} /> : null}
              <Badge tone={statusTone[profile.status]}>{statusLabel[profile.status]}</Badge>
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}
