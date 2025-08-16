from typing import Dict, Any, List, Union

from . import Container


class Location:
    """
    Location Represents the Location block in
    """

    def __init__(self, name, is_websocket_backend=False, is_http_backend=True):
        self.http = is_http_backend
        self.websocket = is_websocket_backend
        self.name = name
        self.containers: List[Container.Container] = []
        self.extras: Dict[str, Any] = {}

    def update_extras(self, extras: Dict[str, Any]):
        for x in extras:
            if x in self.extras:
                data = self.extras[x]
                if type(data) in (dict, set):
                    self.extras[x].update(extras[x])
                elif type(data) in list:
                    self.extras[x].extend(extras[x])
                else:
                    self.extras[x] = extras[x]
            else:
                self.extras[x] = extras[x]

    def add(self, container: Container.Container):
        if not any(c.id == container.id for c in self.containers):
            self.containers.append(container)

    def isempty(self):
        return len(self.containers) == 0

    def remove(self, container: Union[Container.Container, str]):
        container_id = container.id if isinstance(container, Container.Container) else container
        for i, c in enumerate(self.containers):
            if c.id == container_id:
                del self.containers[i]
                return c
        return False

    def __eq__(self, other) -> bool:
        if type(other) is Location:
            return other.name == self.name
        return False

    def __repr__(self):
        return str({"name": self.name, "conatiners": self.containers, "websocket": self.websocket})
