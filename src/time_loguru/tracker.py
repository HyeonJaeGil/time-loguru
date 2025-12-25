from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from loguru import logger as _loguru_logger
except Exception:  # pragma: no cover
    _loguru_logger = None


@dataclass(frozen=True)
class TaskStats:
    task: str
    count: int
    total_s: float
    avg_s: float
    min_s: float
    max_s: float
    last_s: float


class _TaskContext:
    def __init__(self, tracker: "TimeTracker", task: str, level_name: str) -> None:
        self._tracker = tracker
        self._task = task
        self._level_name = level_name
        self._t0: Optional[float] = None

    def __enter__(self) -> "_TaskContext":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        t1 = time.perf_counter()
        t0 = self._t0 if self._t0 is not None else t1
        elapsed = max(0.0, t1 - t0)
        self._tracker._record(self._task, elapsed, level_name=self._level_name, exc_type=exc_type)
        return False  # never swallow exceptions


class TimeTracker:
    """
    Time tracker that delegates output handling to Loguru.

    Only supported level API (by request):
        with tracker.info("TASK"): ...
        with tracker.debug("TASK"): ...
        with tracker.warning("TASK"): ...
        ... etc.

    Notes:
    - Tracker owns: timing + aggregation + summary computation
    - Loguru owns: sinks/handlers, formatting, rotation, retention, filtering, serialization
    """

    def __init__(self, *, logger=None) -> None:
        if logger is None:
            if _loguru_logger is None:
                raise RuntimeError("loguru is not installed. Install loguru or pass a compatible logger.")
            logger = _loguru_logger

        self._logger = logger
        self._lock = threading.Lock()
        self._records: Dict[str, List[float]] = {}

        # tracker-owned knobs
        self._emit_each: bool = False
        self._time_unit: str = "s"  # "ms" or "s"
        self._summary_level: str = "INFO"

        # Optional bookkeeping: sink ids created by add_event_sink()
        self._event_sink_ids: List[int] = []

    # -----------------------------
    # Level-specific context managers (ONLY public tracking API)
    # -----------------------------
    def trace(self, task: str) -> _TaskContext:
        return self._ctx(task, "TRACE")

    def debug(self, task: str) -> _TaskContext:
        return self._ctx(task, "DEBUG")

    def info(self, task: str) -> _TaskContext:
        return self._ctx(task, "INFO")

    def success(self, task: str) -> _TaskContext:
        return self._ctx(task, "SUCCESS")

    def warning(self, task: str) -> _TaskContext:
        return self._ctx(task, "WARNING")

    def error(self, task: str) -> _TaskContext:
        return self._ctx(task, "ERROR")

    def critical(self, task: str) -> _TaskContext:
        return self._ctx(task, "CRITICAL")

    def _ctx(self, task: str, level_name: str) -> _TaskContext:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task name must be a non-empty string")
        return _TaskContext(self, task.strip(), level_name)

    # -----------------------------
    # Optional: access underlying logger
    # -----------------------------
    @property
    def logger(self):
        """Expose the underlying loguru logger so user can configure sinks via logger.add/remove/etc."""
        return self._logger

    def configure(
        self,
        *,
        emit_each: Optional[bool] = None,
        time_unit: Optional[str] = None,
        summary_level: Optional[str] = None,
    ) -> "TimeTracker":
        """
        Configure tracker-owned behavior.

        emit_each:
            If True, emits one log line per completed task block.
        time_unit:
            "ms" or "s"
        summary_level:
            Loguru level used by summary().
        """
        with self._lock:
            if emit_each is not None:
                self._emit_each = bool(emit_each)
            if time_unit is not None:
                if time_unit not in ("ms", "s"):
                    raise ValueError('time_unit must be "ms" or "s"')
                self._time_unit = time_unit
            if summary_level is not None:
                self._summary_level = str(summary_level)
        return self

    def add_event_sink(self, file_path: str, **add_kwargs) -> int:
        """
        Optional convenience: add a Loguru sink that receives ONLY tracker events.

        Delegates to loguru.logger.add(file_path, filter=..., **add_kwargs)

        Example:
            tracker.add_event_sink("timing_only.log", rotation="10 MB", retention="7 days")
        """
        def _only_tracker_events(record) -> bool:
            return record.get("extra", {}).get("event") == "time_logger"

        sink_id = self._logger.add(file_path, filter=_only_tracker_events, **add_kwargs)
        with self._lock:
            self._event_sink_ids.append(sink_id)
        return sink_id

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    # -----------------------------
    # Summary
    # -----------------------------
    def summary(
        self,
        *,
        sort_by: str = "total",
        descending: bool = True,
        limit: Optional[int] = None,
        reset: bool = False,
        title: str = "Time Consumption Summary",
    ) -> str:
        """
        Compute and emit a summary table to the underlying Loguru logger.

        The table is emitted as raw text so its formatting is preserved.
        """
        stats = self._compute_stats()

        key_funcs = {
            "total": lambda s: s.total_s,
            "avg": lambda s: s.avg_s,
            "count": lambda s: s.count,
            "max": lambda s: s.max_s,
            "min": lambda s: s.min_s,
            "task": lambda s: s.task.lower(),
        }
        if sort_by not in key_funcs:
            raise ValueError(f"sort_by must be one of: {', '.join(key_funcs.keys())}")

        stats.sort(key=key_funcs[sort_by], reverse=descending)
        if limit is not None:
            stats = stats[: max(0, int(limit))]

        rendered = self._render_summary(stats, title=title)

        # Tag it as a tracker summary event
        self._logger.bind(event="time_logger", kind="summary").opt(raw=True).log(
            self._summary_level, rendered + "\n"
        )

        if reset:
            self.clear()

        return rendered

    # -----------------------------
    # Internal: record + format
    # -----------------------------
    def _record(self, task: str, elapsed_s: float, *, level_name: str, exc_type=None) -> None:
        with self._lock:
            self._records.setdefault(task, []).append(elapsed_s)

        if self._emit_each:
            status = "OK" if exc_type is None else f"EXC:{getattr(exc_type, '__name__', str(exc_type))}"
            bound = self._logger.bind(
                event="time_logger",
                kind="event",
                task=task,
                elapsed_s=elapsed_s,
                status=status,
            )
            bound.log(
                level_name,
                "task={task} | elapsed={elapsed}",
                # "task={task} status={status} elapsed={elapsed}",
                task=task,
                status=status,
                elapsed=self._fmt_time(elapsed_s),
            )

    def _fmt_time(self, seconds: float) -> str:
        if self._time_unit == "ms":
            return f"{seconds * 1000.0:.3f} ms"
        return f"{seconds:.6f} s"

    def _compute_stats(self) -> List[TaskStats]:
        with self._lock:
            items = list(self._records.items())

        out: List[TaskStats] = []
        for task, d in items:
            if not d:
                continue
            total = float(sum(d))
            count = len(d)
            out.append(
                TaskStats(
                    task=task,
                    count=count,
                    total_s=total,
                    avg_s=total / count,
                    min_s=float(min(d)),
                    max_s=float(max(d)),
                    last_s=float(d[-1]),
                )
            )
        return out

    def _render_summary(self, stats: List[TaskStats], *, title: str) -> str:
        lines: List[str] = []
        lines.append(title)
        lines.append("-" * max(24, len(title)))

        if not stats:
            lines.append("(no data)")
            return "\n".join(lines)

        header = f"{'TASK':30}  {'COUNT':>7}  {'TOTAL':>14}  {'AVG':>14}  {'MIN':>14}  {'MAX':>14}  {'LAST':>14}"
        lines.append(header)
        lines.append("-" * len(header))

        for s in stats:
            lines.append(
                f"{s.task[:30]:30}  "
                f"{s.count:7d}  "
                f"{self._fmt_time(s.total_s):>14}  "
                f"{self._fmt_time(s.avg_s):>14}  "
                f"{self._fmt_time(s.min_s):>14}  "
                f"{self._fmt_time(s.max_s):>14}  "
                f"{self._fmt_time(s.last_s):>14}"
            )

        grand_total = sum(s.total_s for s in stats)
        lines.append("-" * len(header))
        lines.append(f"{'TOTAL (all tasks)':30}  {'':7}  {self._fmt_time(grand_total):>14}")
        return "\n".join(lines)
