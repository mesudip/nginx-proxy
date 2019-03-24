from docker import DockerClient

from nginx_proxy import Container
from nginx_proxy.Location import Location


class Host():
    def __init__(self, client: DockerClient, hostname, port, scheme="http"):
        self.port = port
        self.hostname = hostname
        self.locations:dict[str:Location] = {}  # the map of locations.and the container that serve the locations
        self.scheme = scheme

    def set_external_parameters(self, host, port):
        self.hostname = host
        self.port = port

    def add_container(self, location:str,container:Container):
        if location not in self.locations:
            self.locations[location] = Location(location)
        self.locations[location].add(container)

    # def __eq__(self, other):
    #     return self.id == self.id and self.port

    def isManaged(self):
        return False
    def is_redirect(self):
        return False

    def registerContainer(self, event):
        pass

    def __repr__(self):
        return str({
                    "locations": self.locations,
                    "server_name": self.hostname,
                    "port": self.port})
