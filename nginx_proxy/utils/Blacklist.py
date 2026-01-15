from typing import Any, List, Tuple

import time


class Blacklist:
    def __init__(self, blacklist_duration_secs: int = 180) -> None:
        self.blacklisted_items: dict[Any, float] = {}
        self.default_duration: int = blacklist_duration_secs

    def _clean_blacklist(self) -> None:
        current_time = time.time()
        expired_items = [item for item, expiry in self.blacklisted_items.items() if expiry <= current_time]
        for item in expired_items:
            del self.blacklisted_items[item]

    def filter(self, items: List[Any]) -> List[Any]:
        self._clean_blacklist()
        return [item for item in items if item not in self.blacklisted_items]

    def add(self, item: Any, duration_seconds: int | None = None) -> None:
        if duration_seconds is None:
            duration_seconds = self.default_duration
        self.blacklisted_items[item] = time.time() + duration_seconds

    def list(self) -> List[Any]:
        self._clean_blacklist()
        return list(self.blacklisted_items.keys())

    def partition(self, items: List[Any]) -> Tuple[List[Any], List[Any]]:
        self._clean_blacklist()
        whitelisted_items: List[Any] = []
        blacklisted_items: List[Any] = []
        for item in items:
            if item in self.blacklisted_items:
                blacklisted_items.append(item)
            else:
                whitelisted_items.append(item)
        return (whitelisted_items, blacklisted_items)
