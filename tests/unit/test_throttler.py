import time

from nginx_proxy.Throttler import Throttler


def test_immediate_run_cancels_pending_task():
    throttler = Throttler(0.2)
    calls: list[str] = []

    try:
        throttler.throttle(lambda: calls.append("initial"))
        throttler.throttle(lambda: calls.append("pending"))
        throttler.throttle(lambda: calls.append("forced"), immediate=True)

        time.sleep(0.25)

        assert calls == ["initial", "forced"]
    finally:
        throttler.shutdown()
