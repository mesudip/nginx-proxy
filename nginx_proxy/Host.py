from typing import Set, Dict, Union, Any

from nginx import Url
from nginx_proxy import Container
from nginx_proxy.Location import Location


class Host:
    """
    It is equivalent to a nginx Server block.
    It contains the locations and information about which containers serve the location.

    """

    @staticmethod
    def fromurl(url: Url):
        return Host(url.hostname, url.port, url.scheme)

    def __init__(self, hostname: str, port: int, scheme=None):
        if scheme is None:
            scheme = {
                "http",
            }
        self.port: int = port
        self.hostname: str = hostname
        self.locations: Dict[str, Location] = {}  # the map of locations.and the container that serve the locations
        self.container_set: Set[str] = set()
        self.scheme: set = scheme
        self.secured: bool = "https" in scheme or "wss" in scheme
        self.full_redirect: Union[Url, None] = None
        self.extras: Dict[str, Any] = {}

    def set_external_parameters(self, host, port) -> None:
        self.hostname = host
        self.port = port

    def update_extras(self, extras: Dict[str, Any]) -> None:
        for x in extras:
            self.update_extras_content(x, extras[x])

    def update_extras_content(self, key: str, value: Any) -> None:
        if key in self.extras:
            data = self.extras[key]
            if type(data) in (dict, set):
                self.extras[key].update(value)
            elif type(data) is list:
                self.extras[key].extend(value)
            else:
                self.extras[key] = value
        else:
            self.extras[key] = value

    def add_container(self, location: str, container: Container, websocket=False, http=True) -> None:
        if location not in self.locations:
            self.locations[location] = Location(location, is_websocket_backend=websocket, is_http_backend=http)
        elif websocket:
            self.locations[location].websocket = websocket
            self.locations[location].http = self.locations[location].http or http
        self.locations[location].add(container)
        self.container_set.add(container.id)

    def update_with_host(self, host: "Host") -> None:
        for location in host.locations.values():
            for container in location.containers:
                self.add_container(location.name, container, location.websocket, location.http)
                self.container_set.add(container.id)
            self.locations[location.name].update_extras(location.extras)

    def remove_container(self, container_id) -> None:
        removed = False
        deletions = []
        if container_id in self.container_set:
            for path, location in self.locations.items():
                removed = location.remove(container_id) or removed
                if location.isempty():
                    deletions.append(path)
            self.container_set.remove(container_id)
        for path in deletions:
            del self.locations[path]            
        return removed

    def isempty(self) -> bool:
        return len(self.container_set) == 0

    def ismanaged(self) -> bool:
        return False

    def isredirect(self) -> bool:
        return self.full_redirect is not None

    def __repr__(self):
        return str(
            {"scheme": self.scheme, "locations": self.locations, "server_name": self.hostname, "port": self.port}
        )

    def __str__(self):
        return self.__repr__()
        # hostname= "%s:%s" % (
        #         self.hostname if self.hostname else '?',
        #         str(self.port) if self.port is not None else '?')
