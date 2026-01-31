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
