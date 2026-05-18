import type { ElfEvent } from "./types";

type ElfEventListener = (event: Required<Pick<ElfEvent, "id" | "createdAt">> & ElfEvent) => void;

let nextEventId = 1;
const listeners = new Set<ElfEventListener>();

/**
 * 精灵事件总线。
 * 业务模块只需要 emit 事件，不需要知道 ElfAssistant 如何渲染这些状态。
 */
export const elfEvents = {
  emit(event: ElfEvent) {
    const normalizedEvent = normalizeElfEvent(event);
    listeners.forEach((listener) => listener(normalizedEvent));
    return normalizedEvent.id;
  },

  subscribe(listener: ElfEventListener) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  clear() {
    listeners.clear();
  },
};

function normalizeElfEvent(event: ElfEvent): Required<Pick<ElfEvent, "id" | "createdAt">> & ElfEvent {
  return {
    ...event,
    id: event.id ?? `elf-event-${nextEventId++}`,
    createdAt: event.createdAt ?? Date.now(),
  };
}
