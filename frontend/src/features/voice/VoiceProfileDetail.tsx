import { Check, CircleDot, Play, Save, Trash2, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "../../shared/ui";
import type { VoiceProfile } from "./types";

interface VoiceProfileDetailProps {
  isPlaying: boolean;
  onActivate: (profileId: number) => Promise<void>;
  onDelete: (profileId: number) => Promise<void>;
  onPreview: (profileId: number, text?: string) => Promise<void>;
  onSave: (profileId: number, input: { name: string; description: string; preview_text: string }) => Promise<void>;
  profile: VoiceProfile | null;
}

export function VoiceProfileDetail({
  isPlaying,
  onActivate,
  onDelete,
  onPreview,
  onSave,
  profile,
}: VoiceProfileDetailProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  useEffect(() => {
    setName(profile?.name ?? "");
    setDescription(profile?.description ?? "");
    setPreviewText(profile?.preview_text ?? "");
  }, [profile]);

  if (!profile) {
    return <section className="voice-profile-detail empty">还没有声线。</section>;
  }

  async function run(action: string, callback: () => Promise<void>) {
    setBusyAction(action);
    try {
      await callback();
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <section className="voice-profile-detail">
      <div className="voice-detail-hero">
        <span className="voice-avatar">
          <Volume2 aria-hidden="true" size={22} />
        </span>
        <div>
          <span className="voice-kicker">当前声线</span>
          <h2>{profile.name}</h2>
          <p>{profile.source_type === "designed" ? "文字设计声线" : "系统内置声线"}</p>
        </div>
        {profile.is_active ? <span className="voice-active-pill">默认</span> : null}
      </div>

      <div className="voice-meta-strip">
        <span>
          <CircleDot aria-hidden="true" size={13} />
          {profile.status}
        </span>
        <span>{profile.remote_target_model || profile.remote_model || "default"}</span>
      </div>

      <label className="voice-field">
        <span>名称</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>

      <label className="voice-field">
        <span>描述</span>
        <textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
      </label>

      <label className="voice-field">
        <span>试听文本</span>
        <textarea rows={3} value={previewText} onChange={(event) => setPreviewText(event.target.value)} />
      </label>

      <div className="voice-detail-actions">
        <Button
          disabled={busyAction !== null}
          onClick={() => run("save", () => onSave(profile.id, { name, description, preview_text: previewText }))}
          size="sm"
          variant="primary"
        >
          <Save aria-hidden="true" size={15} />
          保存
        </Button>
        <Button
          disabled={busyAction !== null || profile.status !== "ready"}
          onClick={() => run("preview", () => onPreview(profile.id, previewText))}
          size="sm"
        >
          <Play aria-hidden="true" size={15} />
          {isPlaying ? "播放中" : "试听"}
        </Button>
        <Button
          disabled={busyAction !== null || profile.is_active || profile.status !== "ready"}
          onClick={() => run("activate", () => onActivate(profile.id))}
          size="sm"
        >
          <Check aria-hidden="true" size={15} />
          设为默认
        </Button>
        <Button
          disabled={busyAction !== null || profile.is_active}
          onClick={() => run("delete", () => onDelete(profile.id))}
          size="sm"
          variant="ghost"
        >
          <Trash2 aria-hidden="true" size={15} />
          删除
        </Button>
      </div>

      {profile.last_error ? <div className="voice-error-text">{profile.last_error}</div> : null}

      <div className="voice-prompt-grid">
        <details className="voice-prompt-details">
          <summary>Voice Prompt</summary>
          <pre>{profile.voice_prompt || "未设置"}</pre>
        </details>
        <details className="voice-prompt-details">
          <summary>Style Prompt</summary>
          <pre>{profile.style_prompt || "未设置"}</pre>
        </details>
      </div>
    </section>
  );
}
