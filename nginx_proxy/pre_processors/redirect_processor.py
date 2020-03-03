# this one will get the list of environment variables and will process them.
# this one can call itself before each configuration and modify the dhparam parameter.
import re
from typing import Dict

from nginx import Url
from nginx_proxy import Container, Host


def process_redirection(container: Container, environments: map, vhost_map: Dict[str, Dict[int, Host]]):
    redirect_env = [e[1] for e in environments.items() if e[0].startswith("PROXY_FULL_REDIRECT")]
    hosts = []
    for port_map in vhost_map.values():
        for vhosts in port_map.values():
            hosts.append(vhosts)
    single_host = len(hosts) == 1
    if len(redirect_env):
        for redirect_entry in redirect_env:
            redirect_entry = re.sub(r"\s+", "", redirect_entry, flags=re.UNICODE)

            split = redirect_entry.split("->")
            if len(split) is 2:
                _sources, target = split
                sources = [Url.parse(source) for source in _sources.split(',')]
                target = Url.parse(target, default_port=80)
                if single_host:
                    if target.hostname is None:
                        target = single_host
                elif target.hostname is None:
                    print("Unknown target to redirect with PROXY_FULL_REDIRECT" + redirect_entry)
                    continue
                for source in sources:
                    if source.hostname is not None:
                        port = 80 if source.port is None else source.port
                        if source.hostname not in vhost_map:
                            host = Host(source.hostname, port)
                            host.full_redirect = target
                            vhost_map[source.hostname] = {port: host}
                        else:
                            if port in vhost_map[source.hostname]:
                                existing_host = vhost_map[source.hostname][port]
                                existing_host.full_redirect = target
                            else:
                                host = Host(source.hostname, port)
                                host.full_redirect = target
                                vhost_map[source.hostname][port] = host
                        if target.hostname not in vhost_map:
                            vhost_map[target.hostname] = {target.port: Host(target.hostname, target.port)}
                        elif target.port not in vhost_map[target.hostname]:
                            vhost_map[target.hostname][target.port] = Host(target.hostname, target.port)
            else:
                print("Invalid entry of PROXY_FULL_REDIRECT :" + redirect_entry)
