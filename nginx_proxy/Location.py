from . import Container


class Location:
    """
        Location Represents the Location block in 
    """

    def __init__(self, name):
        self.name = name
        self.containers = set()

    def add(self, container: Container):
        self.containers.add(container)

    def isEmpty(self):
        return len(self.containers) == 0

    def remove(self, container: Container):
        if container in self.containers:
            self.containers.remove(container)
            return True
        return False

    def __eq__(self, other) -> bool:
        if type(other) is Location:
            return other.name == self.name
        return False
