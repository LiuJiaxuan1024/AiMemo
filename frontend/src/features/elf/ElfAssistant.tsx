import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Bot, Loader2 } from "lucide-react";

import { createLive2DElf, type Live2DInstance } from "./live2dAdapter";
import { deriveElfStateFromJobs } from "./elfState";
import type { ElfAssistantProps } from "./types";

/**
 * 精灵助手入口。
 * 第一版只接入 jobs 状态：精灵负责提示后台任务状态，并作为精灵工坊的打开入口。
 */
export function ElfAssistant({
  activeCount,
  failedCount,
  isWorkshopOpen,
  jobs,
  onToggleWorkshop,
}: ElfAssistantProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const live2dRef = useRef<Live2DInstance | null>(null);
  const announcedCompletedJobIdsRef = useRef<Set<number>>(new Set());
  const [live2dStatus, setLive2DStatus] = useState<"loading" | "ready" | "failed">("loading");
  const elfState = useMemo(
    () =>
      deriveElfStateFromJobs(jobs, {
        announcedCompletedJobIds: announcedCompletedJobIdsRef.current,
      }),
    [jobs],
  );

  useEffect(() => {
    if (!hostRef.current || live2dRef.current) {
      return;
    }

    let canceled = false;

    hostRef.current.replaceChildren();

    createLive2DElf(hostRef.current, () => canceled)
      .then((instance) => {
        if (canceled) {
          return;
        }
        live2dRef.current = instance;
        instance.onLoad((status) => {
          if (status === "success") {
            setLive2DStatus("ready");
          }
          if (status === "fail") {
            setLive2DStatus("failed");
          }
        });
      })
      .catch(() => {
        if (!canceled) {
          setLive2DStatus("failed");
        }
      });

    return () => {
      canceled = true;
      live2dRef.current = null;
    };
  }, []);

  useEffect(() => {
    live2dRef.current?.tipsMessage(elfState.message, 4200, elfState.priority);

    if (elfState.mood === "success" && elfState.jobId) {
      announcedCompletedJobIdsRef.current.add(elfState.jobId);
    }
  }, [elfState]);

  return (
    <section className={`elf-assistant mood-${elfState.mood}`}>
      <button
        aria-label={isWorkshopOpen ? "收起精灵工坊" : "打开精灵工坊"}
        className="elf-assistant-hitbox"
        onClick={onToggleWorkshop}
        type="button"
      />

      <div className="elf-assistant-bubble">
        {elfState.mood === "error" ? <AlertTriangle aria-hidden="true" size={15} /> : null}
        {elfState.mood === "working" || elfState.mood === "thinking" ? (
          <Loader2 aria-hidden="true" className="elf-spin" size={15} />
        ) : null}
        {elfState.message}
      </div>

      <div className="elf-live2d-frame">
        <div className="elf-live2d-host" ref={hostRef} />
        {live2dStatus !== "ready" ? (
          <div className="elf-live2d-fallback">
            <Bot aria-hidden="true" size={28} />
            {live2dStatus === "failed" ? "精灵加载失败" : "精灵加载中"}
          </div>
        ) : null}
      </div>

      {failedCount > 0 ? <strong className="elf-badge danger">{failedCount}</strong> : null}
      {failedCount === 0 && activeCount > 0 ? <strong className="elf-badge">{activeCount}</strong> : null}
    </section>
  );
}
