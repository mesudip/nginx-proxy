# this one will get the list of environment variables and will process them.
from typing import Dict, Tuple, List

from nginx import Url
from nginx_proxy.Host import Host

htaccess_folder = "/etc/nginx/generated/htaccess"
from nginx_proxy.BackendTarget import BackendTarget


def process_basic_auth(backend: BackendTarget, environments: map, vhost_map: Dict[str, Dict[int, Host]]):
    def get_auth_map(credentials: str) -> Dict[str, str]:
        auth_map = {}
        for credential in credentials.split(","):
            username_password = credential.split(":")
            if len(username_password) == 2:
                u = username_password[0].strip()
                p = username_password[1].strip()
                if len(u) > 2 and len(p) > 2:
                    auth_map[u] = p
        return auth_map

    def update_security():
        if basic_auth_host.location == "/":
            host.update_extras_content("security", keys)
        else:
            for location in host.locations.values():
                if location.name.startswith(basic_auth_host.location):
                    if "security" in location.extras:
                        location.extras["security"].update(keys)
                    else:
                        location.extras["security"] = keys

    auth_env = [e[1] for e in environments.items() if e[0].startswith("PROXY_BASIC_AUTH")]
    if len(auth_env):
        auth_list: List[Tuple[Url, Dict[str, str]]] = []
        for auth_entry in auth_env:
            host_list = auth_entry.split("->")
            if len(host_list) == 2:
                url = host_list[0]
                keys = get_auth_map(host_list[1])
                auth_list.append((Url.parse(url, default_location="/"), keys))
            elif len(host_list) == 1:
                keys = get_auth_map(auth_entry)
                if len(keys):
                    auth_list.append((Url.root, keys))

        for basic_auth_host, keys in auth_list:
            # if there is no hostname in auth, then there must be
            if basic_auth_host.hostname is None:
                if len(vhost_map) == 1:
                    port_map = list(vhost_map.values())[0]
                    if len(port_map) == 1:
                        host = list(port_map.values())[0]
                        update_security()
            elif basic_auth_host.hostname in vhost_map:
                port_map = vhost_map[basic_auth_host.hostname]
                ports = _matching_ports(basic_auth_host, port_map)
                if ports:
                    for port in ports:
                        host = port_map[port]
                        update_security()
                else:
                    print(
                        "Basic Auth for "
                        + str(basic_auth_host)
                        + " ignored: no matching vhost port in container "
                        + backend.name
                        + " (available ports: "
                        + ", ".join(str(p) for p in sorted(port_map.keys()))
                        + ")"
                    )
            else:
                print(
                    "Unknown hostname : "
                    + basic_auth_host.hostname
                    + "+ in PROXY_BASIC_AUTH in container: "
                    + backend.name
                )


def _matching_ports(auth_url: Url, port_map: Dict[int, Host]) -> List[int]:
    """
    Resolve which vhost ports a PROXY_BASIC_AUTH entry applies to.

    - explicit port (example.com:8443): only that port
    - scheme without port (https://example.com): the scheme's default port
    - neither (example.com): every port the hostname is served on, so that
      an https-only VIRTUAL_HOST is protected on 443
    - port 80 on a host that only serves 443 (http is redirected): 443
    """
    if auth_url.port is not None:
        try:
            port = int(auth_url.port)
        except (TypeError, ValueError):
            return []
    else:
        # Url.parse yields the scheme as a str when present, otherwise an empty list.
        scheme = auth_url.scheme
        schemes = {scheme.lower()} if isinstance(scheme, str) else {s.lower() for s in scheme or []}
        if schemes & {"https", "wss"}:
            port = 443
        elif schemes & {"http", "ws"}:
            port = 80
        else:
            return list(port_map.keys())

    if port in port_map:
        return [port]
    # Port 80 of an https-only vhost is just a redirect to 443: protect the
    # secure vhost the request ends up on.
    if port == 80 and 443 in port_map:
        return [443]
    return []
