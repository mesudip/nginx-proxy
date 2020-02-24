from typing import Dict, Set, Generator, Tuple

from nginx_proxy.Host import Host


class ProxyConfigData:
    """
    All the configuration data that are obtained from the current state of container.
    nginx configuration or any other reverse proxy configuration can be generated using the data available here.
    """

    def __init__(self):
        # map the hostname -> port -> hostCofiguration
        self.config_map: Dict[str, Dict[int, Host]] = {}
        self.containers: Set[str] = set()

    def add_host(self, host: Host) -> None:
        if host.hostname in self.config_map:
            port_map = self.config_map[host.hostname]
            if host.port in port_map:
                existing_host: Host = port_map[host.port]
                for location in host.locations.values():
                    for container in location.containers:
                        existing_host.add_container(location.name, container, location.websocket, location.http)
                        self.containers.add(container.id)
                    existing_host.locations[location.name].update_extras(location.extras)
                return
            else:
                port_map[host.port] = host

        else:
            self.config_map[host.hostname] = {host.port: host}

        for location in host.locations.values():
            for container in location.containers:
                self.containers.add(container.id)

    def remove_container(self, container_id: str) -> Set[Tuple[str, int]]:
        removed_domains = set()
        if container_id in self.containers:
            for host in self.host_list():
                if host.remove_container(container_id):
                    self.containers.remove(container_id)
                    if host.isEmpty():
                        removed_domains.add((host.hostname, host.port))
        return removed_domains

    def has_container(self, container_id):
        return container_id in self.containers

    def host_list(self) -> Generator[Host, None, None]:
        for port_map in self.config_map.values():
            for host in port_map.values():
                yield host
