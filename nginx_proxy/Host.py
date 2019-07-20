from docker import DockerClient

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
        self.container_map = {}
        self.scheme = scheme

    def set_external_parameters(self, host, port):
        self.hostname = host
        self.port = port

    def add_container(self, location: str, container: Container):
        if location not in self.locations:
            self.locations[location] = Location(location)
        self.locations[location].add(container)
        self.container_map[container.id] = location

    def remove_container(self, container_id):
        if container_id in self.container_map:
            location = self.container_map[container_id]
            del self.container_map[container_id]
            if self.locations[location].remove(container_id):
                if self.locations[location].isEmpty():
                    del self.locations[location]
                return True

        return False

    def isEmpty(self):
        return len(self.container_map) == 0

    def isManaged(self):
        return False

    def is_redirect(self):
        return False

    def __repr__(self):
        return str({
            "locations": self.locations,
            "server_name": self.hostname,
            "port": self.port})
