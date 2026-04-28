from typing import Dict, Any, List, Union

from .BackendTarget import BackendTarget


class Location:
    """
    Location Represents the Location block in
    """

    def __init__(self, name, is_websocket_backend=False, is_http_backend=True):
        self.http = is_http_backend
        self.websocket = is_websocket_backend
        self.name = name
        self.backends: List[BackendTarget] = []
        self.extras: Dict[str, Any] = {}

    def update_extras(self, extras: Dict[str, Any]):
        for x in extras:
            if x == "injected_by_backend" and isinstance(extras[x], dict):
                existing = self.extras.setdefault("injected_by_backend", {})
                for backend_id, directives in extras[x].items():
                    normalized = directives if isinstance(directives, list) else [directives]
                    existing[backend_id] = list(dict.fromkeys(normalized))
                self._sync_injected_from_backend_map()
                continue
            if x in self.extras:
                data = self.extras[x]
                if type(data) in (dict, set):
                    self.extras[x].update(extras[x])
                elif isinstance(data, list):
                    new_values = extras[x] if isinstance(extras[x], list) else [extras[x]]
                    existing = set(data)
                    for value in new_values:
                        if value not in existing:
                            data.append(value)
                            existing.add(value)
                else:
                    self.extras[x] = extras[x]
            else:
                self.extras[x] = extras[x]

    def _sync_injected_from_backend_map(self):
        backend_map = self.extras.get("injected_by_backend")
        if not isinstance(backend_map, dict):
            return

        merged: list[str] = []
        seen = set()
        for directives in backend_map.values():
            if not isinstance(directives, list):
                directives = [directives]
            for directive in directives:
                if directive not in seen:
                    seen.add(directive)
                    merged.append(directive)
        self.extras["injected"] = merged

    def remove_backend_extras(self, backend_id: str):
        backend_map = self.extras.get("injected_by_backend")
        if not isinstance(backend_map, dict):
            return
        if backend_id in backend_map:
            del backend_map[backend_id]
            self._sync_injected_from_backend_map()
            if not backend_map:
                del self.extras["injected_by_backend"]
            if not self.extras.get("injected"):
                self.extras.pop("injected", None)

    def add(self, container: BackendTarget):
        if not any(c.id == container.id for c in self.backends):
            self.backends.append(container)

    def isempty(self):
        return len(self.backends) == 0

    def remove(self, container: Union[BackendTarget, str]):
        container_id = container.id if isinstance(container, BackendTarget) else container
        for i, c in enumerate(self.backends):
            if c.id == container_id:
                del self.backends[i]
                return c
        return False

    def __eq__(self, other) -> bool:
        if type(other) is Location:
            return other.name == self.name
        return False

    def __repr__(self):
        return str({"name": self.name, "backends": self.backends, "websocket": self.websocket})
