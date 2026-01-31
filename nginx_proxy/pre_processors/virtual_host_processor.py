from nginx_proxy import Host, ProxyConfigData
from nginx_proxy.BackendTarget import BackendTarget, NoHostConfiguration, UnreachableNetwork
from nginx_proxy.utils import split_url


def process_virtual_hosts(backend: BackendTarget, known_networks: set) -> ProxyConfigData:
    """

    :param backend:
    :param known_networks: networks known to the nginx-proxy container
    :return:
    """
    hosts = ProxyConfigData()
    try:
        for host, location, proxied_backend, extras in host_generator(backend, known_networks=known_networks):
            websocket = "ws" in host.scheme or "wss" in host.scheme
            secured = "https" in host.scheme or "wss" in host.scheme
            http = "http" in host.scheme or "https" in host.scheme
            # it might return string if there's a error in processing
            if type(host) is not str:
                host.add_container(location, proxied_backend, websocket=websocket, http=http)
                if len(extras):
                    injections = []
                    for k, v in extras.items():
                        if v is None:
                            injections.append(k)
                        else:
                            injections.append(f"{k} {v}")
                    host.locations[location].update_extras({"injected": injections})
                hosts.add_host(host)
        print(
            "Valid configuration   ",
            f"{backend.type:>9}".title() + " Id: " + backend.id[:12],
            backend.name,
            sep="\t",
        )
        return hosts
    except NoHostConfiguration:
        print(
            "No VIRTUAL_HOST       ",
            f"{backend.type:>9}".title() + " Id: " + backend.id[:12],
            backend.name,
            sep="\t",
        )
    except UnreachableNetwork as e:
        print(
            "Unreachable Network   ",
            f"{backend.type:>9}".title() + " Id: " + backend.id[:12],
            backend.name,
            "networks: " + ", ".join(list(e.network_names)),
            sep="\t",
        )
    return hosts


def _parse_host_entry(entry_string: str):
    """

    :param entry_string:
    :return: (dict,dict)
    """
    configs = entry_string.split(";", 1)
    extras = {}
    if len(configs) > 1:
        entry_string = configs[0]
        for x in configs[1].split(";"):
            x = x.strip()
            if x:
                # Parse as key value, e.g. 'client_max_body_size 200M'
                if " " in x:
                    k, v = x.split(" ", 1)
                    extras[k.strip()] = v.strip()
                else:
                    extras[x] = None
    host_list = entry_string.strip().split("->")
    external, internal = host_list if len(host_list) == 2 else (host_list[0], "")
    external, internal = (split_url(external), split_url(internal))
    c = BackendTarget(
        None,
        scheme=list(internal["scheme"])[0] if len(internal["scheme"]) else "http",
        address=internal["host"] if internal["host"] else None,
        port=int(internal["port"]) if internal["port"] else None,
        path=internal["location"] if internal["location"] else "",
    )
    h = Host(
        external["host"] if external["host"] else None,
        # having https port on 80 will be detected later and used for redirection.
        int(external["port"]) if external["port"] else 80,
        scheme=external["scheme"] if external["scheme"] else {"http"},
    )
    return (h, external["location"] if external["location"] else "/", c, extras)


def host_generator(backend: BackendTarget, known_networks: set = {}):
    """
    :param backend:
    :param known_networks:
    :return: (Host,str,Container,set)
    """
    env_map = backend.env

    # List all the environment variables with VIRTUAL_HOST and list them.
    virtual_hosts = [x[1] for x in env_map.items() if x[0].startswith("VIRTUAL_HOST")]
    static_hosts = [x[1] for x in env_map.items() if x[0].startswith("STATIC_VIRTUAL_HOST")]
    if len(virtual_hosts) == 0 and len(static_hosts) == 0:
        raise NoHostConfiguration()

    known_networks = set(known_networks)
    unknown = True

    # We need a clean object to return that represents the target
    target_base = BackendTarget(
        backend.id, name=backend.name, env=backend.env, labels=backend.labels, backend_type=backend.type
    )

    found_ip = None

    if hasattr(backend, "network_settings") and backend.network_settings:
        for name, detail in backend.network_settings.items():
            target_base.add_network(detail.get("NetworkID"))
            if detail.get("NetworkID") and detail.get("NetworkID") in known_networks and unknown:
                found_ip = detail.get("IPAddress")
                # if detail["Aliases"] is not None: ...
                if found_ip:
                    break

    if found_ip is None:
        # If checking against known networks failed or no common network
        raise UnreachableNetwork(target_base.networks)

    for host_config in static_hosts:
        host, location, container_data, extras = _parse_host_entry(host_config)
        if location and not location.endswith("/") and container_data.path and container_data.path.endswith("/"):
            location = location + "/"
        container_data.id = backend.id
        container_data.name = backend.name
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
        host, location, container_data, extras = _parse_host_entry(host_config)
        # Protect double / in urls.
        if location and not location.endswith("/") and container_data.path and container_data.path.endswith("/"):
            location = location + "/"
        container_data.address = found_ip
        container_data.id = backend.id
        container_data.name = backend.name

        if container_data.port is None:
            if override_port:
                container_data.port = override_port
            elif hasattr(backend, "ports") and backend.ports and len(backend.ports) == 1:
                # backend.ports expected to be dict or list of ports
                # original: keys of dict
                container_data.port = int(list(backend.ports.keys())[0].split("/")[0])
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
