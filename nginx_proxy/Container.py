from docker.models.containers import Container as DockerContainer


class Container:
    def __init__(self, id: str, scheme=None, address=None, port=None, path=None):
        self.id = id
        self.address = address
        self.port = port
        self.path = path
        self.scheme = scheme
        self.networks = set()  # the list networks through which this container is accessible.

    def add_network(self, network_id: str):
        self.networks.add(network_id)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other) -> bool:
        if type(other) is Container:
            return self.id == other.id
        if type(other) is str:
            return self.id == other
        return False

    def __repr__(self):
        return str({"scheme": self.scheme, "address": self.address, "port": self.port, "path": self.path})

    @staticmethod
    def get_env_map(container: DockerContainer):
        # first we get the list of tuples each containing data in form (key, value)
        env_list = [x.split("=", 1) for x in container.attrs['Config']['Env']]
        # convert the environment list into map
        return {x[0]: x[1].strip() for x in env_list if len(x) is 2}


class UnconfiguredContainer(Exception):
    pass


class UnreachableNetwork(UnconfiguredContainer):
    pass


class NoHostConiguration(UnconfiguredContainer):
    pass
