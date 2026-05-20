from collections import deque
from datetime import datetime, timedelta, timezone
import threading

from app.schemas.elf import ElfEventCreate, ElfEventRead


class ElfEventService:
    """进程内精灵事件中心。

    这个服务刻意保持轻量：它只保存最近一小段 UI 事件，供桌面精灵和浏览器并行轮询。
    持久状态仍然属于 jobs、messages、turns 等业务表，避免把短生命周期气泡变成数据库负担。
    """

    def __init__(self, *, max_events: int = 200, dedupe_window_seconds: int = 8) -> None:
        self.max_events = max_events
        self.dedupe_window = timedelta(seconds=dedupe_window_seconds)
        self._events: deque[ElfEventRead] = deque(maxlen=max_events)
        self._next_id = 1
        self._lock = threading.Lock()

    def publish(self, event: ElfEventCreate) -> ElfEventRead | None:
        """发布一个精灵事件。

        返回 None 表示事件被 dedupe_key 抑制。这样 job 完成轮询、刷新页面等重复场景
        不会让精灵一直说同一句话。
        """

        now = utc_now()
        with self._lock:
            if event.dedupe_key and self._is_duplicate(event.dedupe_key, now):
                return None
            saved = ElfEventRead(id=self._next_id, created_at=now, **event.model_dump())
            self._next_id += 1
            self._events.append(saved)
            return saved

    def list_after(self, after_id: int = 0, *, limit: int = 50) -> list[ElfEventRead]:
        """读取指定事件 ID 之后的事件。

        参数：
          after_id: 客户端已处理的最后一个事件 ID。
          limit: 本次最多返回条数，防止客户端长时间离线后一次拉取过多。
        """

        with self._lock:
            return [event for event in self._events if event.id > after_id][:limit]

    def latest_id(self) -> int:
        """返回当前最新事件 ID；没有事件时返回 0。"""

        with self._lock:
            if not self._events:
                return 0
            return self._events[-1].id

    def clear(self) -> None:
        """清空事件中心，主要供单元测试使用。"""

        with self._lock:
            self._events.clear()
            self._next_id = 1

    def _is_duplicate(self, dedupe_key: str, now: datetime) -> bool:
        for event in reversed(self._events):
            if event.dedupe_key != dedupe_key:
                continue
            return now - event.created_at <= self.dedupe_window
        return False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


elf_event_service = ElfEventService()


def emit_elf_event(event: ElfEventCreate) -> ElfEventRead | None:
    """业务层发布精灵事件的统一入口。"""

    return elf_event_service.publish(event)
