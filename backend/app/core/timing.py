import json
import logging
from time import perf_counter
from typing import Any


logger = logging.getLogger(__name__)


def now_counter() -> float:
    """返回高精度计时起点。"""

    return perf_counter()


def elapsed_ms(started_at: float) -> int:
    """返回从 started_at 到当前时间的毫秒数。"""

    return int((perf_counter() - started_at) * 1000)


def emit_timing(event: str, **payload: Any) -> None:
    """输出结构化性能调试日志。

    开发阶段优先写 stdout，避免 uvicorn/logger 配置吞掉细粒度排查信息。
    """

    try:
        print(
            json.dumps(
                {"event": event, **payload},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
    except Exception as exc:
        # 性能埋点只能辅助排查，不能反过来打断主流程。
        logger.debug("emit_timing failed for %s: %s", event, exc)
