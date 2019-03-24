from nginx_proxy.Container import Container

class Location:
    def __init__(self, name):
        self.name = name
        self.containers = set()

    def add(self, container: Container):
        self.containers.add(container)

    def remove(self, container: Container):
        self.containers.remove(container)

    def __eq__(self, other) -> bool:
        if type(other) is Location:
            return other.name == self.name
        return False