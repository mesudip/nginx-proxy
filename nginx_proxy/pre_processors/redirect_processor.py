# this one will get the list of environment variables and will process them.
# this one can call itself before each configuration and modify the dhparam parameter.
import re
from typing import Dict

from nginx_proxy import Container, Host, utils


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
                sources = [utils.split_url(source) for source in _sources.split(',')]
                target = utils.split_url(target)
                if target['host'] is None and single_host:
                    target = single_host
                elif target['host'] is None:
                    print("Unknown target to redirect with PROXY_FULL_REDIRECT" + redirect_entry)
                    continue
                for source in sources:
                    if source['host'] is not None:
                        port = 80 if source['port'] is None else int(source['port'])
                        if source['host'] not in vhost_map:
                            host = Host(source['host'], port)
                            host.full_redirect = target
                            vhost_map[source['host']] = {port: host}
                        else:
                            if port in vhost_map[source['host']]:
                                existing_host = vhost_map[source['host']][port]
                                existing_host.full_redirect = target
                            else:
                                host = Host(source['host'], port)
                                host.full_redirect = target
                                vhost_map[source['host']][port] = host
            else:
                print("Invalid entry of PROXY_FULL_REDIRECT :" + redirect_entry)
