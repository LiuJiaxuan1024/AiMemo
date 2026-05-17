import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { deriveElfStateFromJobs } from "./elfState";
import { MemoExpressionRenderer } from "./memoExpressionRenderer";
import type { ElfAssistantProps, ElfMotion, ElfMood } from "./types";

const ELF_POSITION_STORAGE_KEY = "ai-note-elf-position";
const ELF_WIDTH = 260;
const ELF_HEIGHT = 360;
const ELF_VIEWPORT_PADDING = 12;
const IDLE_MOTIONS: ElfMotion[] = ["blink"];

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
  const hasBootstrappedJobsRef = useRef(false);
  const observedActiveJobIdsRef = useRef<Set<number>>(new Set());
  const pendingCompletedAnnouncementIdsRef = useRef<Set<number>>(new Set());
  const completedAnnouncementTimersRef = useRef<Map<number, number>>(new Map());
  const idleMotionTimerRef = useRef<number | null>(null);
  const idleMotionResetTimerRef = useRef<number | null>(null);
  const dragStateRef = useRef<{
    pointerId: number;
    startLeft: number;
    startTop: number;
    startX: number;
    startY: number;
  } | null>(null);
  const dragMovedRef = useRef(false);
  const [elfPosition, setElfPosition] = useState<ElfPosition | null>(() => readStoredElfPosition());
  const [isDraggingElf, setIsDraggingElf] = useState(false);
  const [isHoveringElf, setIsHoveringElf] = useState(false);
  const [idleMotion, setIdleMotion] = useState<ElfMotion>("breathe");
  const [announcedCompletedJobIds, setAnnouncedCompletedJobIds] = useState<Set<number>>(
    () => new Set(),
  );
  const effectiveAnnouncedCompletedJobIds = useMemo(() => {
    const next = new Set(announcedCompletedJobIds);

    for (const job of jobs) {
      if (job.status !== "completed") {
        continue;
      }

      // 首次加载进来的 completed 都是历史任务，不应该被当成“刚刚完成”。
      // 后续如果一个任务没有在本页被观察到 pending/running，也同样视为历史任务。
      if (!hasBootstrappedJobsRef.current || !observedActiveJobIdsRef.current.has(job.id)) {
        next.add(job.id);
      }
    }

    return next;
  }, [announcedCompletedJobIds, jobs]);
  const elfState = useMemo(
    () =>
      deriveElfStateFromJobs(jobs, {
        announcedCompletedJobIds: effectiveAnnouncedCompletedJobIds,
      }),
    [effectiveAnnouncedCompletedJobIds, jobs],
  );
  const displayMood = useMemo<ElfMood>(() => {
    if (elfState.mood !== "idle") {
      return elfState.mood;
    }
    if (isWorkshopOpen || isHoveringElf || isDraggingElf) {
      return "talking";
    }
    return "idle";
  }, [elfState.mood, isDraggingElf, isHoveringElf, isWorkshopOpen]);
  const displayMotion = useMemo<ElfMotion>(() => {
    if (isDraggingElf) {
      return "dragging";
    }
    if (elfState.mood === "thinking") {
      return "thinking";
    }
    if (elfState.mood === "working") {
      return "working";
    }
    if (elfState.mood === "success") {
      return "success";
    }
    if (elfState.mood === "error" || elfState.mood === "warning") {
      return "error";
    }
    if (isWorkshopOpen || isHoveringElf) {
      return "look";
    }
    return idleMotion;
  }, [elfState.mood, idleMotion, isDraggingElf, isHoveringElf, isWorkshopOpen]);

  useEffect(() => {
    return () => {
      completedAnnouncementTimersRef.current.forEach((timeoutId) => window.clearTimeout(timeoutId));
      completedAnnouncementTimersRef.current.clear();
      clearIdleMotionTimers(idleMotionTimerRef, idleMotionResetTimerRef);
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
    clearIdleMotionTimers(idleMotionTimerRef, idleMotionResetTimerRef);

    if (elfState.mood !== "idle" || isDraggingElf || isHoveringElf || isWorkshopOpen) {
      setIdleMotion("breathe");
      return;
    }

    function scheduleNextIdleMotion() {
      const delayMs = 12000 + Math.floor(Math.random() * 10000);
      idleMotionTimerRef.current = window.setTimeout(() => {
        const nextMotion = IDLE_MOTIONS[Math.floor(Math.random() * IDLE_MOTIONS.length)] ?? "blink";
        setIdleMotion(nextMotion);

        idleMotionResetTimerRef.current = window.setTimeout(() => {
          setIdleMotion("breathe");
          scheduleNextIdleMotion();
        }, 900);
      }, delayMs);
    }

    // 空闲随机动作只在真正 idle 时运行，避免打断任务状态和用户交互。
    setIdleMotion("breathe");
    scheduleNextIdleMotion();

    return () => clearIdleMotionTimers(idleMotionTimerRef, idleMotionResetTimerRef);
  }, [elfState.mood, isDraggingElf, isHoveringElf, isWorkshopOpen]);

  useEffect(() => {
    let shouldUpdateAnnouncedIds = false;
    const nextAnnouncedIds = new Set(announcedCompletedJobIds);

    for (const job of jobs) {
      if (job.status === "pending" || job.status === "running") {
        observedActiveJobIdsRef.current.add(job.id);
        continue;
      }

      if (job.status !== "completed") {
        continue;
      }

      // jobs 首次拉取时可能已经包含大量 completed，这些都属于历史快照。
      // 只有本页实际观察过活跃状态的任务，才允许进入“刚刚完成”的短提醒。
      if (!hasBootstrappedJobsRef.current || !observedActiveJobIdsRef.current.has(job.id)) {
        if (!nextAnnouncedIds.has(job.id)) {
          nextAnnouncedIds.add(job.id);
          shouldUpdateAnnouncedIds = true;
        }
      }
    }

    hasBootstrappedJobsRef.current = true;
    if (shouldUpdateAnnouncedIds) {
      setAnnouncedCompletedJobIds(nextAnnouncedIds);
    }
  }, [announcedCompletedJobIds, jobs]);

  useEffect(() => {
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
    setIsDraggingElf(false);
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
    setIsDraggingElf(true);
    // 记录拖拽开始时的容器位置，移动过程中只根据指针偏移量更新外层精灵坐标。
    // 这样 Memo 图片、任务角标、点击热区会作为一个整体移动，不依赖第三方插件内部状态。
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
        onPointerEnter={() => setIsHoveringElf(true)}
        onPointerLeave={() => setIsHoveringElf(false)}
        type="button"
      />

      {elfState.mood !== "idle" ? (
        <div className="elf-assistant-bubble">
          {elfState.mood === "error" ? <AlertTriangle aria-hidden="true" size={15} /> : null}
          {elfState.mood === "working" || elfState.mood === "thinking" ? (
            <Loader2 aria-hidden="true" className="elf-spin" size={15} />
          ) : null}
          {elfState.message}
        </div>
      ) : null}

      <MemoExpressionRenderer mood={displayMood} motion={displayMotion} />

      {failedCount > 0 ? <strong className="elf-badge danger">{failedCount}</strong> : null}
      {failedCount === 0 && activeCount > 0 ? <strong className="elf-badge">{activeCount}</strong> : null}
    </section>
  );
}

function clearIdleMotionTimers(
  idleMotionTimerRef: MutableRefObject<number | null>,
  idleMotionResetTimerRef: MutableRefObject<number | null>,
) {
  if (idleMotionTimerRef.current !== null) {
    window.clearTimeout(idleMotionTimerRef.current);
    idleMotionTimerRef.current = null;
  }
  if (idleMotionResetTimerRef.current !== null) {
    window.clearTimeout(idleMotionResetTimerRef.current);
    idleMotionResetTimerRef.current = null;
  }
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
