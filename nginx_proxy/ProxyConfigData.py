from typing import Dict, Set, Generator, Tuple, Union

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
        self._len = 0

    def getHost(self, hostname: str, port: int = 80) -> Union[None, Host]:
        if hostname in self.config_map:
            if port in self.config_map[hostname]:
                return self.config_map[hostname][port]
        return None

    def add_host(self, host: Host) -> None:
        if host.hostname in self.config_map:
            port_map = self.config_map[host.hostname]
            if host.port in port_map:
                existing_host: Host = port_map[host.port]
                existing_host.secured = host.secured or existing_host.secured
                existing_host.update_extras(host.extras)
                for location in host.locations.values():
                    for container in location.containers:
                        existing_host.add_container(location.name, container, location.websocket, location.http)
                        self.containers.add(container.id)
                    existing_host.locations[location.name].update_extras(location.extras)
                return
            else:
                self._len = self._len + 1
                port_map[host.port] = host

        else:
            self._len = self._len + 1
            self.config_map[host.hostname] = {host.port: host}

        for location in host.locations.values():
            for container in location.containers:
                self.containers.add(container.id)

    def remove_container(self, container_id: str) -> Tuple[bool, Set[Tuple[str, int]]]:
        removed_domains = set()
        result = False
        if container_id in self.containers:
            self.containers.remove(container_id)
            for host in self.host_list():
                if host.remove_container(container_id):
                    result = True
                    if host.isempty():
                        removed_domains.add((host.hostname, host.port))
        return result, removed_domains

    def has_container(self, container_id):
        return container_id in self.containers

    def host_list(self) -> Generator[Host, None, None]:
        for port_map in self.config_map.values():
            for host in port_map.values():
                yield host

    def __len__(self):
        return self._len

    def print(self):

        for host in self.host_list():
            if host.port != 80:
                url = "-   " + ("https" if host.secured else "http") + "://" + host.hostname + ":" + str(host.port)
            else:
                url = "-   " + ("https" if host.secured else "http") + "://" + host.hostname
            if host.isredirect():
                print(url)
                print("      redirect : ", host.full_redirect)
            else:
                if len(host.extras):
                    print(url)
                    self.printextra("      ", host.extras)
                for location in host.locations.values():
                    print(url + location.name)
                    print("      Type: ", "Websocket" if location.websocket else "Http")
                    if len(location.extras):
                        self.printextra("      ", location.extras)

    @staticmethod
    def printextra(gap, extra):
        print(gap + "Extras:")
        for x in extra:
            if x is 'security' or type(x) in (set, list):
                print(gap + "  " + x + ":")
                for s in extra[x]:
                    print(gap + "    " + s)
            elif type(x) is dict:
                print(gap + "  " + x + ":")
                for s in extra[x]:
                    print(gap + "    " + s + ":" + extra[x][s])
            else:
                print(gap + "  " + x + " : " + str(extra[x]))
