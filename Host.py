import docker
import json
from docker import DockerClient
from jinja2 import Template
from urllib.parse import urlparse


class Location():
    """
    Corresponds to the nginx location configuration inside server  for example the one given below
    server{
        location /app  {
            proxy_pass http://portainer_container_alias:9000/portainer;
        }
    }
    the above parameters are represented by this class as follows:
    server {
        location {{external_location}} {
            proxy_pass {{internal_scheme}}://{{internal_host}}:{{internal_port}}/{{internal_location}}
        }
    }
    """

    def __init__(self, location, host_id, internal_host, internal_port, internal_path, internal_scheme=None):
        self.location = location
        self.host_id = host_id
        self.host = internal_host
        self.path = internal_path
        self.port = internal_port
        self.scheme = internal_scheme

    def __repr__(self):
        return str(
            {"location": self.location, "host": self.host, "path": self.path, "port": self.port, "scheme": self.scheme})


class Host():
    def __init__(self, client: DockerClient, id, network, external_hostname, external_port,ssl_host=None):
        self.client = client
        self.id = id
        self.network = network
        self.port = external_port
        self.server_name = external_hostname
        self.locations = {}
        self.ssl_host=ssl_host

    def set_external_parameters(self, host, port):
        self.server_name = host
        self.port = port

    def add_location(self, host_id, location, container_host, container_port, container_path=None, internal_scheme=None):
        self.locations[location] = Location(location, host_id, container_host, container_port, container_path,
                                            internal_scheme)

    def __eq__(self, other):
        return self.id == self.id and self.port

    @staticmethod
    def _parse_host_entry(entry_string: str):
        """

        :param entry_string:
        :return: (dict,dict)
        """

        def split_url(entry_string: str):
            # Tried parsing urls with urllib.parse.urlparse but it doesn't work quiet
            # well when scheme( eg: "https://") is missing

            # split the host entry string from the first occurance of "/"
            entries = entry_string.strip().split("/", 1)

            if len(entries) is 1:
                # this means that the host string has no "/" in it.
                # This is the case when only hostname is given without location.
                location = None
            else:  # we have the location entry and it's the second part in the splitted list.
                location = "/" + entries[1]

            # now lets again split the host part to find if it contains a port definition.
            host_entries = entries[0].split(":")
            if len(host_entries) is 1:  # means that it doesn't contain the port information it's only the host part.
                port = None
            else:
                port = host_entries[1] if len(host_entries[1].strip()) > 0 else None

            host = host_entries[0] if len(host_entries[0].strip()) > 0 else None
            return {
                "host": host,
                "port": port,
                "location": location if location else ""
            }

        host_list = entry_string.strip().split("->")
        external_entry = split_url(host_list[0])
        internal_entry = {"host": None, "port": None, "location": ""} if len(host_list) is 1 else split_url(
            host_list[1])
        return external_entry, internal_entry

    @staticmethod
    def from_container(container, service_id: str = None, known_networks: set = {}):
        """
        :param container:
        :param service_id:
        :param known_networks:
        :return:
        """
        network_settings = container.attrs["NetworkSettings"]
        # first we get the list of tuples each containing data in form (key, value)
        env_list = [x.split("=", 1) for x in container.attrs['Config']['Env']]
        # convert the environment list into map
        env_map = {x[0]: x[1].strip() for x in env_list if len(x) is 2 and x[1].strip()}

        # see if VIRTUAL_HOST entry is present
        if "VIRTUAL_HOST" in env_map:
             external_host, internal_host = Host._parse_host_entry(env_map["VIRTUAL_HOST"])
        else:
            return None
        ssl_host=env_map["LETSENCRYPT_HOST"] if "LETSENCRYPT_HOST" in env_map else None


        # now let's see the legacy VIRTUAL_PORT if port is not provided in the VIRTUAL_HOST entry, we use this entry.
        if (not internal_host["port"]) and "VIRTUAL_PORT" in env_map:
            internal_host["port"] = env_map["VIRTUAL_PORT"]

        # if the  legacy VIRTUAL_PORT is also not provided let's try using the exposed port int he host.
        if (not internal_host["port"]) and len(network_settings["Ports"]) is 1:
            internal_host["port"] = list(network_settings["Ports"].keys())[0].split("/")[0]

        known_networks = set(known_networks)
        for name, detail in network_settings["Networks"].items():
            if detail["NetworkID"] in known_networks:
                internal_host["host"] = detail["Aliases"][len(detail["Aliases"]) - 1]
                ip_address = detail["IPAddress"]
                network = name
                break
        else:
            return
        host = Host(client=container.client,
                    id=container.id if service_id is None else service_id,
                    network=network,
                    external_hostname=external_host["host"] if external_host["host"] else container.id,
                    external_port=external_host["port"] if external_host["port"] else "80",
                    ssl_host=ssl_host)
        host.add_location(host.id,
                          location=external_host["location"] if external_host["location"] else "/",
                          container_host=internal_host["host"],
                          container_port=internal_host["port"] if internal_host["port"] else "80",
                          container_path=internal_host["location"] if internal_host["location"] else "/", )
        return host

    def isManaged(self):
        return False

    def registerContainer(self, event):
        pass

    def __repr__(self):
        return str({"id": self.id,
                    "network": self.network,
                    "locations": self.locations,
                    "server_name": self.server_name,
                    "port": self.port})
