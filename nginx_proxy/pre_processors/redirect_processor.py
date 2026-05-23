# this one will get the list of environment variables and will process them.
# this one can call itself before each configuration and modify the dhparam parameter.
import re
from typing import Dict

from nginx import Url
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host


def _is_certificate_redirect_target(target: Url):
    return "https" in target.scheme or "wss" in target.scheme or int(target.port or 80) == 443


def _hostname_exceeds_certificate_limit(hostname: str) -> bool:
    return bool(hostname) and len(hostname.rstrip(".")) > 64


def process_redirection(backend: BackendTarget, environments: map, vhost_map: Dict[str, Dict[int, Host]]):
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
            if len(split) == 2:
                _sources, target = split
                sources = [Url.parse(source) for source in _sources.split(",")]
                target = Url.parse(target)
                if target.hostname is None and single_host:
                    target.hostname = hosts[0].hostname
                elif target.hostname is None:
                    print("Unknown target to redirect with PROXY_FULL_REDIRECT" + redirect_entry)
                    continue
                target.port = int(target.port) if target.port is not None else None
                if target.port is None and target.hostname in vhost_map:
                    target_host = vhost_map[target.hostname].get(443) or vhost_map[target.hostname].get(80)
                    if target_host is not None:
                        target.port = target_host.port
                        target.scheme = {"https"} if target_host.secured else {"http"}
                if target.port is None:
                    target.port = 443 if "https" in target.scheme or "wss" in target.scheme else 80
                if not target.scheme:
                    target.scheme = {"https"} if target.port == 443 else {"http"}
                if not Url.is_valid_hostname(target.hostname, allow_wildcard=True):
                    print("Invalid PROXY_FULL_REDIRECT target hostname: " + target.hostname)
                    continue
                if _is_certificate_redirect_target(target) and _hostname_exceeds_certificate_limit(target.hostname):
                    print("Invalid PROXY_FULL_REDIRECT target certificate hostname: " + target.hostname)
                    continue
                for source in sources:
                    if source.hostname is not None:
                        if not Url.is_valid_hostname(source.hostname, allow_wildcard=True):
                            print("Invalid PROXY_FULL_REDIRECT source hostname: " + source.hostname)
                            continue
                        port = 80 if source.port is None else int(source.port)
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
