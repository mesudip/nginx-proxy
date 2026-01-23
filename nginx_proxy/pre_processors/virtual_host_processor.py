from docker.models.containers import Container as DockerContainer

from nginx_proxy import Host, ProxyConfigData
from nginx_proxy.Container import Container, NoHostConiguration, UnreachableNetwork, InvalidHostConfiguration
from nginx_proxy.utils import split_url


def process_virtual_hosts(container: DockerContainer, environments: map, known_networks: set) -> ProxyConfigData:
    """

    :param container:
    :param environments: parsed container environment
    :param known_networks: networks known to the nginx-proxy container
    :return:
    """
    hosts = ProxyConfigData()
    try:
        for host, location, proxied_container, extras in host_generator(container, known_networks=known_networks):
            websocket = "ws" in host.scheme or "wss" in host.scheme
            secured = "https" in host.scheme or "wss" in host.scheme
            http = "http" in host.scheme or "https" in host.scheme
            # it might return string if there's a error in processing
            if type(host) is not str:
                host.add_container(location, proxied_container, websocket=websocket, http=http)
                if len(extras):
                    host.locations[location].update_extras({"injected": extras})
                hosts.add_host(host)
        print(
            "Valid configuration   ",
            "Id:" + container.id[:12],
            "    " + container.attrs["Name"].replace("/", ""),
            sep="\t",
        )
        return hosts
    except NoHostConiguration:
        print(
            "No VIRTUAL_HOST       ",
            "Id:" + container.id[:12],
            "    " + container.attrs["Name"].replace("/", ""),
            sep="\t",
        )
    except UnreachableNetwork as e:
        print(
            "Unreachable Network   ",
            "Id:" + container.id[:12],
            "    " + container.attrs["Name"].replace("/", ""),
            "networks: " + ", ".join(list(e.network_names)),
            sep="\t",
        )
    except InvalidHostConfiguration as e:
        print(
            "Invalid Configuration ",
            "Id:" + container.id[:12],
            "    " + container.attrs["Name"].replace("/", ""),
            sep="\t",
        )
        print(
            "  Error: " + e.message,
        )
        if e.env_var_value:
            print(
                "  Value: " + e.env_var_value,
            )
    return hosts


def _parse_host_entry(entry_string: str, is_static: bool = False):
    """
    Parse a virtual host entry string.
    
    :param entry_string: The host entry string to parse
    :param is_static: Whether this is from STATIC_VIRTUAL_HOST (requires destination)
    :return: (Host, location, Container, extras)
    :raises InvalidHostConfiguration: If configuration is invalid
    """
    configs = entry_string.split(";", 1)
    extras = set()
    if len(configs) > 1:
        entry_string = configs[0]
        for x in configs[1].split(";"):
            x = x.strip()
            if x:
                extras.add(x)
    host_list = entry_string.strip().split("->")
    external, internal = host_list if len(host_list) == 2 else (host_list[0], "")
    external, internal = (split_url(external), split_url(internal))
    
    # Validate STATIC_VIRTUAL_HOST requires a destination
    if is_static and not internal["host"]:
        raise InvalidHostConfiguration(
            f"STATIC_VIRTUAL_HOST must specify a destination address after '->' (e.g., 'example.com -> http://backend:8080')",
            entry_string.strip()
        )
    
    c = Container(
        None,
        scheme=list(internal["scheme"])[0] if len(internal["scheme"]) else "http",
        address=internal["host"] if internal["host"] else None,
        port=internal["port"] if internal["port"] else None,
        path=internal["location"] if internal["location"] else "",
    )
    h = Host(
        external["host"] if external["host"] else None,
        # having https port on 80 will be detected later and used for redirection.
        int(external["port"]) if external["port"] else 80,
        scheme=external["scheme"] if external["scheme"] else {"http"},
    )
    return (h, external["location"] if external["location"] else "/", c, extras)


def host_generator(container: DockerContainer, service_id: str = None, known_networks: set = {}):
    """
    :param container:
    :param service_id:
    :param known_networks:
    :return: (Host,str,Container,set)
    """
    c = Container(container.id)
    network_settings = container.attrs["NetworkSettings"]
    env_map = Container.get_env_map(container)

    # List all the environment variables with VIRTUAL_HOST and list them.
    virtual_hosts = [x[1] for x in env_map.items() if x[0].startswith("VIRTUAL_HOST")]
    static_hosts = [x[1] for x in env_map.items() if x[0].startswith("STATIC_VIRTUAL_HOST")]
    if len(virtual_hosts) == 0 and len(static_hosts) == 0:
        raise NoHostConiguration()

    # Instead of directly processing container details, check whether or not it's accessible through known networks.
    known_networks = set(known_networks)
    unknown = True
    for name, detail in network_settings["Networks"].items():
        c.add_network(detail["NetworkID"])
        if detail["NetworkID"] and detail["NetworkID"] in known_networks and unknown:
            ip_address = detail["IPAddress"]
            # if detail["Aliases"] is not None:  # we might use alias
            #   alias = detail["Aliases"][len(detail["Aliases"]) - 1]
            # network = name
            if ip_address:
                break
    else:
        raise UnreachableNetwork(c.networks)

    container_name = container.attrs["Name"].replace("/", "")

    for host_config in static_hosts:
        host, location, container_data, extras = _parse_host_entry(host_config, is_static=True)
        container_data.id = container.id
        container_data.name = container_name
        host.secured = "https" in host.scheme or "wss" in host.scheme or host.port == 443
        if host.port is None:
            host.port = 443 if host.secured else 80
        if container_data.port is None:
            container_data.port = 443 if ("https" in container_data.scheme or "wss" in container_data.scheme) else 80
        yield (host, location, container_data, extras)

    override_ssl = False
    override_port = None
    if len(virtual_hosts) == 1:
        if "LETSENCRYPT_HOST" in env_map:
            override_ssl = True
        if "VIRTUAL_PORT" in env_map:
            override_port = env_map["VIRTUAL_PORT"]

    for host_config in virtual_hosts:
        host, location, container_data, extras = _parse_host_entry(host_config, is_static=False)
        container_data.address = ip_address
        container_data.id = container.id
        container_data.name = container_name
        if override_port:
            container_data.port = override_port
        elif container_data.port is None:
            if len(network_settings["Ports"]) == 1:
                container_data.port = int(list(network_settings["Ports"].keys())[0].split("/")[0])
            else:
                container_data.port = 80
        if override_ssl:
            if "ws" in host.scheme:
                host.scheme = {"wss", "https"}
                host.secured = True
            else:
                host.scheme = {
                    "https",
                }
        host.secured = "https" in host.scheme or host.port == 443
        yield (host, location, container_data, extras)
