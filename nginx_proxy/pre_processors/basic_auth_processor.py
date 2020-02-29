# this one will get the list of environment variables and will process them.
from typing import Dict, Tuple, List

from nginx import Url
from nginx_proxy.Host import Host

htaccess_folder = "/etc/nginx/generated/htaccess"
from docker.models.containers import Container

def process_basic_auth(container: Container, environments: map, vhost_map: Dict[str, Dict[int, Host]]):
    def get_auth_map(credentials: str) -> Dict[str, str]:
        auth_map = {}
        for credential in credentials.split(','):
            username_password = credential.split(':')
            if len(username_password) == 2:
                u = username_password[0].strip()
                p = username_password[1].strip()
                if len(u) > 2 and len(p) > 2:
                    auth_map[u] = p
        return auth_map

    def update_security():
        if basic_auth_host.location == '/':
            host.update_extras_content('security', keys)
        else:
            for location in host.locations.values():
                if location.name.startswith(basic_auth_host.location):
                    if 'security' in location.extras:
                        location.extras['security'].update(keys)
                    else:
                        location.extras['security'] = keys

    auth_env = [e[1] for e in environments.items() if e[0].startswith("PROXY_BASIC_AUTH")]
    if len(auth_env):
        auth_list: List[Tuple[Url, Dict[str, str]]] = []
        for auth_entry in auth_env:
            host_list = auth_entry.split("->")
            if len(host_list) is 2:
                url = host_list[0]
                keys = get_auth_map(host_list[1])
                auth_list.append((Url.parse(url, default_location='/',default_port=80), keys))
            elif len(host_list) is 1:
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
                if basic_auth_host.port in port_map:
                    host = port_map[basic_auth_host.port]
                    update_security()
                else:
                    print("Basic Auth for "+basic_auth_host.hostname+":"+str(basic_auth_host.port)+" in container with "+str(list(vhost_map.keys())))
            else:
                print("Unknown hostname : "+basic_auth_host.hostname+"+ in PROXY_BASIC_AUTH in container: " + container.name)
