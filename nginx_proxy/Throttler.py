import threading
import time
from typing import Callable, Optional


class Throttler:
    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._last_run_time = 0.0

    def _trigger(self, task: Callable):
        with self._lock:
            self._timer = None
            self._last_run_time = time.time()
            task()

    def throttle(self, task: Callable, immediate: bool = False):
        """
        Executes a task with throttling/debouncing.

        If called within the `interval` since the last execution, it schedules the task
        to run after the interval passes (if not already scheduled). If called after
        the interval, it runs the task immediately.

        Args:
            task: The callable to execute.
            immediate: If True, bypasses throttling and runs the task immediately.

        Returns:
            The result of the task if it ran immediately, or `False` if it was throttled.
        """
        with self._lock:
            current_time = time.time()
            # If immediate is true, we always run immediately
            should_run_now = immediate or (current_time >= self._last_run_time + self.interval)

            if should_run_now:
                # Cancel existing timer if we are running now
                if self._timer:
                    self._timer.cancel()
                    self._timer = None
                self._last_run_time = current_time
                return task()
            else:
                # Too soon, schedule if not already scheduled
                if not self._timer:
                    wait_time = (self._last_run_time + self.interval) - current_time
                    self._timer = threading.Timer(wait_time, self._trigger, args=[task])
                    self._timer.start()
                return False

    def shutdown(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
