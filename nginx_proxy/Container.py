from nginx_proxy import Host


class Container:
    def __init__(self, id, scheme=None, address=None, port=None, path=None):
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
    def _parse_host_entry(entry_string: str):
        """

        :param entry_string:
        :return: (dict,dict)
        """

        def split_url(entry_string: str):
            # Tried parsing urls with urllib.parse.urlparse but it doesn't work quiet
            # well when scheme( eg: "https://") is missing eg "example.com"
            # it says that example.com is path not the hostname.
            split_scheme = entry_string.strip().split("://", 1)
            scheme, host_part = split_scheme if len(split_scheme) is 2 else (None, split_scheme[0])
            host_entries = host_part.split("/", 1)
            hostport, location = (host_entries[0], "/" + host_entries[1]) if len(host_entries) is 2 else (
                host_entries[0], None)
            hostport_entries = hostport.split(":", 1)
            host, port = hostport_entries if len(hostport_entries) is 2 else (hostport_entries[0], None)

            return {
                "scheme": scheme,
                "host": host if host else None,
                "port": port,
                "location": location
            }

        host_list = entry_string.strip().split("->")
        external, internal = host_list if len(host_list) is 2 else (host_list[0], "")
        external, internal = (split_url(external), split_url(internal))
        c = Container(None,
                      scheme=internal["scheme"] if internal["scheme"] else "http",
                      address=None,
                      port=internal["port"] if internal["port"] else None,
                      path=internal["location"] if internal["location"] else "/")
        h = Host.Host(
            external["host"] if external["host"] else None,
            # having https port on 80 will be detected later and used for redirection.
            external["port"] if external["port"] else "80",
            scheme=external["scheme"] if external["scheme"] else "http"
        )

        return (h,
                external["location"] if external["location"] else "/",
                c)


    @staticmethod
    def host_generator(container, service_id: str = None, known_networks: set = {}):
        """
        :param container:
        :param service_id:
        :param known_networks:
        :return: (Host,str,Container)
        """
        c = Container(container.id)
        network_settings = container.attrs["NetworkSettings"]
        # first we get the list of tuples each containing data in form (key, value)
        env_list = [x.split("=", 1) for x in container.attrs['Config']['Env']]
        # convert the environment list into map
        env_map = {x[0]: x[1].strip() for x in env_list if len(x) is 2 and x[1].strip()}

        # List all the environment variables with VIRTUAL_HOST and list them.
        virtual_hosts = [x[1] for x in env_map.items() if x[0].startswith("VIRTUAL_HOST")]
        if len(virtual_hosts) is 0:
            raise NoHostConiguration()

        # Instead of directly processing container details, check whether or not it's accessible through known networks.
        known_networks = set(known_networks)
        unknown = True
        for name, detail in network_settings["Networks"].items():
            c.add_network(detail["NetworkID"])
            # fix for https://trello.com/c/js37t4ld
            if detail["Aliases"] is not None:
                if detail["NetworkID"] in known_networks and unknown:
                    alias = detail["Aliases"][len(detail["Aliases"]) - 1]
                    ip_address = detail["IPAddress"]
                    network = name
                    if ip_address:
                        break
        else:
            raise UnreachableNetwork()

        override_ssl = False
        override_port = None
        if len(virtual_hosts) is 1:
            if "LETSENCRYPT_HOST" in env_map:
                override_ssl = True
            if "VIRTUAL_PORT" in env_map:
                override_port=env_map["VIRTUAL_PORT"]

        for host_config in virtual_hosts:
            host, location, container_data = Container._parse_host_entry(host_config)
            container_data.address = ip_address
            container_data.id = container.id
            if override_port:
                container_data.port = override_port
            elif container_data.port is None:
                if len(network_settings["Ports"]) is 1:
                    container_data.port = list(network_settings["Ports"].keys())[0].split("/")[0]
                else:
                    container_data.port = "80"
            if override_ssl:
                host.scheme="https"
            yield (host, location, container_data)

class UnconfiguredContainer(Exception):
    pass


class UnreachableNetwork(UnconfiguredContainer):
    pass


class NoHostConiguration(UnconfiguredContainer):
    pass
