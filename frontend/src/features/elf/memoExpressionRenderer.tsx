import { useEffect } from "react";

import type { ElfMood } from "./types";

const MEMO_EXPRESSION_BY_MOOD: Record<ElfMood, string> = {
  idle: "/elf/memo/01_idle_soft.png",
  thinking: "/elf/memo/02_thinking.png",
  working: "/elf/memo/03_working_focus.png",
  success: "/elf/memo/04_success_smile.png",
  warning: "/elf/memo/05_error_worried.png",
  error: "/elf/memo/05_error_worried.png",
  talking: "/elf/memo/07_curious.png",
};

const MEMO_EXPRESSION_ASSETS = Array.from(new Set(Object.values(MEMO_EXPRESSION_BY_MOOD)));

interface MemoExpressionRendererProps {
  mood: ElfMood;
}

/**
 * Memo 精灵的第一代表现层。
 * 这里先用透明 PNG 做状态切换，后续升级 Live2D 时只需要替换这一层渲染实现。
 */
export function MemoExpressionRenderer({ mood }: MemoExpressionRendererProps) {
  const imageSrc = MEMO_EXPRESSION_BY_MOOD[mood] ?? MEMO_EXPRESSION_BY_MOOD.idle;

  useEffect(() => {
    // 预加载所有常用表情，避免 job 状态切换时出现短暂空白或闪烁。
    MEMO_EXPRESSION_ASSETS.forEach((src) => {
      const image = new Image();
      image.src = src;
    });
  }, []);

  return (
    <div className="elf-memo-frame" aria-hidden="true">
      <img className="elf-memo-image" src={imageSrc} alt="" draggable={false} />
    </div>
  );
}
