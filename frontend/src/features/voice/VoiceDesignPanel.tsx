import { Wand2 } from "lucide-react";
import { useState } from "react";

import { Button } from "../../shared/ui";
import type { VoiceDesignInput, VoiceDesignResponse } from "./types";

interface VoiceDesignPanelProps {
  onDesign: (input: VoiceDesignInput) => Promise<VoiceDesignResponse>;
  onDesigned: (profileId: number) => void;
}

export function VoiceDesignPanel({ onDesign, onDesigned }: VoiceDesignPanelProps) {
  const [description, setDescription] = useState("");
  const [nameHint, setNameHint] = useState("");
  const [previewText, setPreviewText] = useState("今天也一起把事情慢慢做好吧。");
  const [result, setResult] = useState<VoiceDesignResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit() {
    if (!description.trim()) {
      return;
    }
    setIsSubmitting(true);
    try {
      const response = await onDesign({
        description,
        name_hint: nameHint || undefined,
        preview_text: previewText || undefined,
        language: "zh",
      });
      setResult(response);
      onDesigned(response.profile.id);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="voice-design-panel">
      <div className="voice-panel-title">
        <span className="voice-panel-title-main">
          <Wand2 aria-hidden="true" size={16} />
          <span>声音设计</span>
        </span>
        <small>Qwen Voice Design</small>
      </div>

      <label className="voice-field">
        <span>声线描述</span>
        <textarea
          onChange={(event) => setDescription(event.target.value)}
          placeholder="例如：温柔、轻快、像陪伴型二次元助手，语速中等，尾音有一点元气。"
          rows={6}
          value={description}
        />
      </label>

      <div className="voice-design-hints" aria-label="声线描述参考">
        <button type="button" onClick={() => setDescription("年轻成年女性声线，柔软、慵懒、亲近，带一点困倦感和慢悠悠的学姐气质。音色偏中高但不尖，略带轻微鼻音和沙软质感，语速中等偏慢，温柔可靠，不要幼态。")}>
          慵懒学姐
        </button>
        <button type="button" onClick={() => setDescription("清澈明亮的年轻女性声线，语气轻快、聪明、带一点俏皮感，像住在桌面里的陪伴型工作伙伴。自然不夸张，适合长时间对话。")}>
          桌面精灵
        </button>
        <button type="button" onClick={() => setDescription("成熟温柔的女性声线，低饱和、稳定、治愈，像深夜电台一样放松，吐字清楚，节奏慢一点，适合陪伴和解释复杂问题。")}>
          深夜电台
        </button>
      </div>

      <label className="voice-field">
        <span>名字提示</span>
        <input onChange={(event) => setNameHint(event.target.value)} placeholder="可选，例如 暖糖" value={nameHint} />
      </label>

      <label className="voice-field">
        <span>试听文本</span>
        <textarea rows={3} onChange={(event) => setPreviewText(event.target.value)} value={previewText} />
      </label>

      <Button disabled={!description.trim() || isSubmitting} onClick={handleSubmit} size="lg" variant="primary">
        <Wand2 aria-hidden="true" size={16} />
        {isSubmitting ? "生成中" : "创建声线"}
      </Button>

      {result ? (
        <div className="voice-design-result">
          <strong>{result.profile.name}</strong>
          <p>{result.voice_prompt}</p>
          {result.warnings.length > 0 ? (
            <div className="voice-error-text">{result.warnings.join("\n")}</div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
