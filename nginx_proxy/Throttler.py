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
        A context manager that implements throttling/debouncing.
        Yields True if the task should be executed immediately in the with block.
        Yields False if the task was throttled and (if necessary) scheduled for later.
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
