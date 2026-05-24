import { useEffect, useState } from "react";

// 6 帧字符脉冲，灵感来自 Claude Code Spinner（macOS 优先字符集）：
// submodules/Claude-Code/src/components/Spinner/utils.ts
const PULSE_FRAMES = ["·", "✢", "✳", "✶", "✻", "✽"] as const;
const FRAME_MS = 80;

interface PulseGlyphProps {
  active?: boolean;
}

export function PulseGlyph({ active = true }: PulseGlyphProps) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (!active) {
      return;
    }
    const id = window.setInterval(() => {
      setFrame((value) => (value + 1) % PULSE_FRAMES.length);
    }, FRAME_MS);
    return () => window.clearInterval(id);
  }, [active]);

  return (
    <span className="chat-pulse-glyph" aria-hidden="true">
      {active ? PULSE_FRAMES[frame] : "∴"}
    </span>
  );
}
