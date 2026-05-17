import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { AlertTriangle, Bot, Loader2 } from "lucide-react";

import { createLive2DElf, type Live2DInstance } from "./live2dAdapter";
import { deriveElfStateFromJobs } from "./elfState";
import type { ElfAssistantProps } from "./types";

const ELF_POSITION_STORAGE_KEY = "ai-note-elf-position";
const ELF_WIDTH = 260;
const ELF_HEIGHT = 360;
const ELF_VIEWPORT_PADDING = 12;

interface ElfPosition {
  left: number;
  top: number;
}

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
  const pendingCompletedAnnouncementIdsRef = useRef<Set<number>>(new Set());
  const completedAnnouncementTimersRef = useRef<Map<number, number>>(new Map());
  const dragStateRef = useRef<{
    pointerId: number;
    startLeft: number;
    startTop: number;
    startX: number;
    startY: number;
  } | null>(null);
  const dragMovedRef = useRef(false);
  const [elfPosition, setElfPosition] = useState<ElfPosition | null>(() => readStoredElfPosition());
  const [announcedCompletedJobIds, setAnnouncedCompletedJobIds] = useState<Set<number>>(
    () => new Set(),
  );
  const [live2dStatus, setLive2DStatus] = useState<"loading" | "ready" | "failed">("loading");
  const elfState = useMemo(
    () =>
      deriveElfStateFromJobs(jobs, {
        announcedCompletedJobIds,
      }),
    [announcedCompletedJobIds, jobs],
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
    return () => {
      completedAnnouncementTimersRef.current.forEach((timeoutId) => window.clearTimeout(timeoutId));
      completedAnnouncementTimersRef.current.clear();
      window.removeEventListener("pointermove", handleDragMove);
      window.removeEventListener("pointerup", handleDragEnd);
      window.removeEventListener("pointercancel", handleDragEnd);
    };
  }, []);

  useEffect(() => {
    function handleViewportResize() {
      setElfPosition((current) => {
        if (!current) {
          return current;
        }
        const nextPosition = clampElfPosition(current);
        writeStoredElfPosition(nextPosition);
        return nextPosition;
      });
    }

    window.addEventListener("resize", handleViewportResize);
    return () => window.removeEventListener("resize", handleViewportResize);
  }, []);

  useEffect(() => {
    live2dRef.current?.tipsMessage(elfState.message, 4200, elfState.priority);

    if (elfState.mood === "success" && elfState.jobId) {
      const jobId = elfState.jobId;
      if (pendingCompletedAnnouncementIdsRef.current.has(jobId)) {
        return;
      }

      // 完成提醒是短暂状态：先让用户看到 4.2 秒，再标记为已播报并回到 idle。
      // 如果立刻写入 announcedCompletedJobIds，React 会马上重算成 idle，提示会一闪而过。
      pendingCompletedAnnouncementIdsRef.current.add(jobId);
      const timeoutId = window.setTimeout(() => {
        pendingCompletedAnnouncementIdsRef.current.delete(jobId);
        completedAnnouncementTimersRef.current.delete(jobId);
        setAnnouncedCompletedJobIds((current) => {
          const next = new Set(current);
          next.add(jobId);
          return next;
        });
      }, 4200);
      completedAnnouncementTimersRef.current.set(jobId, timeoutId);
    }
  }, [elfState]);

  function handleDragMove(event: PointerEvent) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }

    const deltaX = event.clientX - dragState.startX;
    const deltaY = event.clientY - dragState.startY;
    if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) {
      dragMovedRef.current = true;
    }

    const nextPosition = clampElfPosition({
      left: dragState.startLeft + deltaX,
      top: dragState.startTop + deltaY,
    });
    setElfPosition(nextPosition);
    writeStoredElfPosition(nextPosition);
  }

  function handleDragEnd(event: PointerEvent) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }

    dragStateRef.current = null;
    window.removeEventListener("pointermove", handleDragMove);
    window.removeEventListener("pointerup", handleDragEnd);
    window.removeEventListener("pointercancel", handleDragEnd);
  }

  function handleDragStart(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) {
      return;
    }

    const currentTarget = event.currentTarget.closest(".elf-assistant");
    if (!(currentTarget instanceof HTMLElement)) {
      return;
    }

    const rect = currentTarget.getBoundingClientRect();
    dragMovedRef.current = false;
    // 记录拖拽开始时的容器位置，移动过程中只根据指针偏移量更新外层精灵坐标。
    // 这样 Live2D、任务角标、点击热区会作为一个整体移动，不依赖第三方插件内部状态。
    dragStateRef.current = {
      pointerId: event.pointerId,
      startLeft: rect.left,
      startTop: rect.top,
      startX: event.clientX,
      startY: event.clientY,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    window.addEventListener("pointermove", handleDragMove);
    window.addEventListener("pointerup", handleDragEnd);
    window.addEventListener("pointercancel", handleDragEnd);
  }

  function handleToggleWorkshop() {
    if (dragMovedRef.current) {
      dragMovedRef.current = false;
      return;
    }
    onToggleWorkshop();
  }

  const positionStyle = elfPosition
    ? {
        bottom: "auto",
        left: `${elfPosition.left}px`,
        right: "auto",
        top: `${elfPosition.top}px`,
      }
    : undefined;

  return (
    <section className={`elf-assistant mood-${elfState.mood}`} style={positionStyle}>
      <button
        aria-label={isWorkshopOpen ? "收起精灵工坊" : "打开精灵工坊"}
        className="elf-assistant-hitbox"
        onClick={handleToggleWorkshop}
        onPointerDown={handleDragStart}
        type="button"
      />

      {live2dStatus !== "ready" ? (
        <div className="elf-assistant-bubble">
          {elfState.mood === "error" ? <AlertTriangle aria-hidden="true" size={15} /> : null}
          {elfState.mood === "working" || elfState.mood === "thinking" ? (
            <Loader2 aria-hidden="true" className="elf-spin" size={15} />
          ) : null}
          {elfState.message}
        </div>
      ) : null}

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

function readStoredElfPosition(): ElfPosition | null {
  try {
    const raw = window.localStorage.getItem(ELF_POSITION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<ElfPosition>;
    if (typeof parsed.left !== "number" || typeof parsed.top !== "number") {
      return null;
    }
    return clampElfPosition({ left: parsed.left, top: parsed.top });
  } catch {
    return null;
  }
}

function clampElfPosition(position: ElfPosition): ElfPosition {
  const maxLeft = Math.max(ELF_VIEWPORT_PADDING, window.innerWidth - ELF_WIDTH - ELF_VIEWPORT_PADDING);
  const maxTop = Math.max(ELF_VIEWPORT_PADDING, window.innerHeight - ELF_HEIGHT - ELF_VIEWPORT_PADDING);
  return {
    left: Math.min(Math.max(position.left, ELF_VIEWPORT_PADDING), maxLeft),
    top: Math.min(Math.max(position.top, ELF_VIEWPORT_PADDING), maxTop),
  };
}

function writeStoredElfPosition(position: ElfPosition) {
  try {
    window.localStorage.setItem(ELF_POSITION_STORAGE_KEY, JSON.stringify(position));
  } catch {
    // localStorage 不可用时只影响持久化，不影响当前拖拽位置。
  }
}
