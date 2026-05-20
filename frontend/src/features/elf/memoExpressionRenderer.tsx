import { useEffect } from "react";

import type { ElfMood, ElfMotion } from "./types";

const MEMO_EXPRESSION_BY_MOOD: Record<ElfMood, string> = {
  idle: "/elf/memo/01_idle_soft.png",
  thinking: "/elf/memo/02_thinking.png",
  working: "/elf/memo/03_working_focus.png",
  success: "/elf/memo/04_success_smile.png",
  warning: "/elf/memo/05_error_worried.png",
  error: "/elf/memo/05_error_worried.png",
  talking: "/elf/memo/18_relaxed.png",
};

const MEMO_EXPRESSION_ASSETS = [
  "/elf/memo/01_idle_soft.png",
  "/elf/memo/02_thinking.png",
  "/elf/memo/03_working_focus.png",
  "/elf/memo/04_success_smile.png",
  "/elf/memo/05_error_worried.png",
  "/elf/memo/06_sleepy.png",
  "/elf/memo/07_curious.png",
  "/elf/memo/08_memory_glow.png",
  "/elf/memo/09_shy_blush.png",
  "/elf/memo/10_angry_pout.png",
  "/elf/memo/11_surprised.png",
  "/elf/memo/12_sad_teary.png",
  "/elf/memo/13_wronged_pout.png",
  "/elf/memo/14_confused.png",
  "/elf/memo/15_proud.png",
  "/elf/memo/16_playful_wink.png",
  "/elf/memo/17_serious.png",
  "/elf/memo/18_relaxed.png",
  "/elf/memo/19_encouraging.png",
  "/elf/memo/20_speechless.png",
];

interface MemoExpressionRendererProps {
  mood: ElfMood;
  motion: ElfMotion;
}

/**
 * Memo 精灵的第一代表现层。
 * 这里先用透明 PNG 做状态切换，后续升级 Live2D 时只需要替换这一层渲染实现。
 */
export function MemoExpressionRenderer({ mood, motion }: MemoExpressionRendererProps) {
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
      <div className={`elf-memo-sprite motion-${motion}`}>
        <img className="elf-memo-image" src={imageSrc} alt="" draggable={false} />
      </div>
    </div>
  );
}
