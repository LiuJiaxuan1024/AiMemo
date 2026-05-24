"""Per-turn SSE event buffer with replay + live subscribe.

LangGraph 跑动以前内嵌在 HTTP 响应生成器里，浏览器一断开（用户切会话、刷新、
关闭标签页），GeneratorExit 就会传到 graph 把它直接掐掉。用户的痛点是：他们
本来期望 agent 继续跑、回来还能看到结果，而不是 "切走 = 终止"。

本模块把 graph 执行和 HTTP 连接解耦：每个 turn 对应一个内存里的事件 buffer，
producer 把 SSE 事件按顺序 append 进去，多个 subscriber 都可以从 index 0 开始
重放历史事件，然后跟着 live 拿后续事件，直到 buffer mark_done。

设计要点：
- 同一个 turn_id 的 buffer 只有一份；并发 subscriber 共享同一份事件列表。
- subscribe() 把整段历史先 yield 一遍再 wait —— 这样首次连接和断开重连走同一条路径。
- mark_done() 是终态，之后 subscribe() 会把剩余事件 drain 完后立刻 return。
- 留 retention 秒数让"完成后立刻重连"也能拿到完整 done/error 事件；超时后由
  cleanup_expired() 释放。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field


# 完成后保留 5 分钟，足够前端 done/error 后再短暂重连，又不会无限占用内存。
RETENTION_SECONDS = 5 * 60


@dataclass
class TurnBuffer:
    """单个 turn 的事件 buffer。

    `events` 用 list 顺序追加；`cond` 同步 producer/subscriber 之间的可见性。
    `done` 是终态：进入终态后 cond 被广播一次，所有阻塞的 subscriber 唤醒并
    退出循环。
    """

    turn_id: int
    events: list[str] = field(default_factory=list)
    done: bool = False
    done_at: float = 0.0
    cond: threading.Condition = field(default_factory=threading.Condition)

    def append(self, event: str) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def mark_done(self) -> None:
        with self.cond:
            if self.done:
                return
            self.done = True
            self.done_at = time.time()
            self.cond.notify_all()

    def subscribe(self, *, from_index: int = 0, wait_timeout: float = 30.0) -> Iterator[str]:
        """从 from_index 开始重放并跟随 live 事件。

        - from_index <= len(events) 时立刻 yield 历史；
        - 历史耗尽后进入 wait，新事件 append 会唤醒；
        - mark_done 后把剩余事件 drain 完再 return。

        `wait_timeout` 给的是单次 wait 的上限，主要为了在 producer 永久挂死的极端
        情况下，subscriber 仍能定期检查 done 标志。
        """

        cursor = max(0, from_index)
        while True:
            with self.cond:
                while cursor >= len(self.events) and not self.done:
                    self.cond.wait(timeout=wait_timeout)
                if cursor < len(self.events):
                    event = self.events[cursor]
                    cursor += 1
                else:
                    return
            yield event


_BUFFERS: dict[int, TurnBuffer] = {}
_BUFFERS_LOCK = threading.Lock()


def get_or_create(turn_id: int) -> TurnBuffer:
    """拿到对应 turn 的 buffer；不存在则创建。"""

    with _BUFFERS_LOCK:
        buf = _BUFFERS.get(turn_id)
        if buf is None:
            buf = TurnBuffer(turn_id=turn_id)
            _BUFFERS[turn_id] = buf
        return buf


def get(turn_id: int) -> TurnBuffer | None:
    """返回 buffer；如果已经被 cleanup 回收则返回 None。"""

    with _BUFFERS_LOCK:
        return _BUFFERS.get(turn_id)


def cleanup_expired(*, now: float | None = None) -> int:
    """删除 done 且超过 retention 的 buffer，返回回收数量。

    新一轮 chat 启动时机会性触发，避免长期运行后内存里堆积大量完成的 buffer。
    """

    threshold = (now if now is not None else time.time()) - RETENTION_SECONDS
    dropped = 0
    with _BUFFERS_LOCK:
        for tid in list(_BUFFERS.keys()):
            buf = _BUFFERS[tid]
            if buf.done and buf.done_at < threshold:
                _BUFFERS.pop(tid, None)
                dropped += 1
    return dropped


def reset_for_tests() -> None:
    """测试用：清空全部 buffer。生产代码不要调用。"""

    with _BUFFERS_LOCK:
        _BUFFERS.clear()
