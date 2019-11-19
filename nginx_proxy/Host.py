from nginx_proxy import Container
from nginx_proxy.Location import Location


class Host:
    """
    It is equivalent to a nginx Server block.
    It contains the locations and information about which containers serve the location.

    """

    def __init__(self, hostname, port, scheme="http"):
        self.port = port
        self.hostname = hostname
        self.locations: dict[str:Location] = {}  # the map of locations.and the container that serve the locations
        self.container_set: set = set()
        self.scheme = scheme

    def set_external_parameters(self, host, port):
        self.hostname = host
        self.port = port

    def add_container(self, location: str, container: Container, websocket=False, http=True):
        if location not in self.locations:
            self.locations[location] = Location(location, is_websocket_backend=websocket, is_http_backend=http)
        elif websocket:
            self.locations[location].websocket = self.locations[location].websocket or websocket
            self.locations[location].http = self.locations[location].http or http
        self.locations[location].add(container)
        self.container_set.add(container.id)

    def remove_container(self, container_id):
        removed = False
        deletions = []
        if container_id in self.container_set:
            for path, location in self.locations.items():
                removed = location.remove(container_id) or removed
                if location.isEmpty():
                    deletions.append(path)
        for path in deletions:
            del self.locations[path]
        if removed:
            self.container_set.remove(container_id)
        return removed

    def isEmpty(self):
        return len(self.container_set) == 0

    def isManaged(self):
        return False

    def is_redirect(self):
        return False

    def __repr__(self):
        return str({
            "scheme": self.scheme,
            "locations": self.locations,
            "server_name": self.hostname,
            "port": self.port})
