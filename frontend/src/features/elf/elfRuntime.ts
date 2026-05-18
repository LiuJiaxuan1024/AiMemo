import { useEffect, useMemo, useState } from "react";

import type { Job } from "../jobs/types";
import { elfEvents } from "./elfEventBus";
import { deriveElfStateFromJobs } from "./elfState";
import type { ElfEvent, ElfMotion, ElfState } from "./types";

const DEDUPE_RETENTION_MS = 60_000;
const RUNTIME_TICK_MS = 500;

type RuntimeElfEvent = Required<Pick<ElfEvent, "id" | "createdAt">> & ElfEvent;

interface UseElfRuntimeOptions {
  announcedCompletedJobIds?: Set<number>;
  fallbackJobs: Job[];
}

export interface ElfRuntimeState {
  state: ElfState;
  motion?: ElfMotion;
  activeEvent?: RuntimeElfEvent;
}

/**
 * 精灵运行时。
 * 它把事件总线中的短生命周期事件和 jobs 派生状态合并成最终展示状态。
 */
export function useElfRuntime({
  announcedCompletedJobIds,
  fallbackJobs,
}: UseElfRuntimeOptions): ElfRuntimeState {
  const [events, setEvents] = useState<RuntimeElfEvent[]>([]);
  const [runtimeNow, setRuntimeNow] = useState(() => Date.now());
  const [seenDedupeKeys, setSeenDedupeKeys] = useState<Map<string, number>>(() => new Map());

  useEffect(() => {
    const unsubscribe = elfEvents.subscribe((event) => {
      setSeenDedupeKeys((currentKeys) => {
        const now = Date.now();
        const nextKeys = pruneDedupeKeys(currentKeys, now);

        if (event.dedupeKey && nextKeys.has(event.dedupeKey)) {
          return nextKeys;
        }

        if (event.dedupeKey) {
          nextKeys.set(event.dedupeKey, now);
        }
        setEvents((currentEvents) => [...currentEvents, event]);
        return nextKeys;
      });
    });

    return unsubscribe;
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      const now = Date.now();
      setRuntimeNow(now);
      setSeenDedupeKeys((currentKeys) => pruneDedupeKeys(currentKeys, now));
      setEvents((currentEvents) => currentEvents.filter((event) => !isEventExpired(event, now)));
    }, RUNTIME_TICK_MS);

    return () => window.clearInterval(intervalId);
  }, []);

  return useMemo(() => {
    const activeEvents = events.filter((event) => !isEventExpired(event, runtimeNow));
    const activeEvent = pickHighestPriorityEvent(activeEvents);

    if (activeEvent) {
      return {
        activeEvent,
        motion: activeEvent.motion,
        state: {
          mood: activeEvent.mood,
          message: activeEvent.message ?? "",
          priority: activeEvent.priority,
          source: activeEvent.source,
          jobId: readNumberMetadata(activeEvent, "jobId"),
          turnId: readNumberMetadata(activeEvent, "turnId"),
        },
      };
    }

    return {
      state: deriveElfStateFromJobs(fallbackJobs, {
        announcedCompletedJobIds,
      }),
    };
  }, [announcedCompletedJobIds, events, fallbackJobs, runtimeNow]);
}

function pickHighestPriorityEvent(events: RuntimeElfEvent[]): RuntimeElfEvent | undefined {
  return [...events].sort((left, right) => {
    if (right.priority !== left.priority) {
      return right.priority - left.priority;
    }
    return right.createdAt - left.createdAt;
  })[0];
}

function isEventExpired(event: RuntimeElfEvent, now: number): boolean {
  return typeof event.ttlMs === "number" && now - event.createdAt >= event.ttlMs;
}

function pruneDedupeKeys(keys: Map<string, number>, now: number): Map<string, number> {
  const next = new Map<string, number>();
  keys.forEach((createdAt, key) => {
    if (now - createdAt <= DEDUPE_RETENTION_MS) {
      next.set(key, createdAt);
    }
  });
  return next;
}

function readNumberMetadata(event: RuntimeElfEvent, key: string): number | undefined {
  const value = event.metadata?.[key];
  return typeof value === "number" ? value : undefined;
}
