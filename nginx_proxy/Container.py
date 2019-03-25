class Container():
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
        return split_url(external), split_url(internal)

    @staticmethod
    def get_contaier_info(container, service_id: str = None, known_networks: set = {}):
        """
        :param container:
        :param service_id:
        :param known_networks:
        :return:
        """
        c = Container(container.id)
        network_settings = container.attrs["NetworkSettings"]
        # first we get the list of tuples each containing data in form (key, value)
        env_list = [x.split("=", 1) for x in container.attrs['Config']['Env']]
        # convert the environment list into map
        env_map = {x[0]: x[1].strip() for x in env_list if len(x) is 2 and x[1].strip()}

        # see if VIRTUAL_HOST entry is present
        if "VIRTUAL_HOST" in env_map:
            external_host, internal_host = Container._parse_host_entry(env_map["VIRTUAL_HOST"])
        else:
            raise NoHostConiguration()
        ssl_host = env_map["LETSENCRYPT_HOST"] if "LETSENCRYPT_HOST" in env_map else None

        # now let's see the legacy VIRTUAL_PORT if port is not provided in the VIRTUAL_HOST entry, we use this entry.
        if (not internal_host["port"]) and "VIRTUAL_PORT" in env_map:
            internal_host["port"] = env_map["VIRTUAL_PORT"]

        # if the  legacy VIRTUAL_PORT is also not provided let's try using the exposed port int he host.
        if (not internal_host["port"]) and len(network_settings["Ports"]) is 1:
            internal_host["port"] = list(network_settings["Ports"].keys())[0].split("/")[0]

        known_networks = set(known_networks)
        unknown = True
        for name, detail in network_settings["Networks"].items():
            c.add_network(detail["NetworkID"])
            if detail["NetworkID"] in known_networks and unknown:
                internal_host["host"] = detail["Aliases"][len(detail["Aliases"]) - 1]
                internal_host["host"] = detail["IPAddress"]
                ip_address = detail["IPAddress"]
                network = name
                unknown = not bool(internal_host["host"])
        if unknown:
            raise UnreachableNetwork()

        c.address = internal_host["host"]
        c.scheme = internal_host["scheme"] if internal_host["scheme"] else "http"
        c.port = internal_host["port"] if internal_host["port"] else "80"
        c.path = internal_host["location"] if internal_host["location"] else "/"

        return (
            "https" if ssl_host else (external_host["scheme"] if external_host["scheme"] else "http"),
            external_host["host"] if external_host["host"] else container.id,
            external_host["port"] if external_host["port"] else "80",
            external_host["location"] if external_host["location"] else "/",
            c,
        )


class UnconfiguredContainer(Exception):
    pass


class UnreachableNetwork(UnconfiguredContainer):
    pass


class NoHostConiguration(UnconfiguredContainer):
    pass

