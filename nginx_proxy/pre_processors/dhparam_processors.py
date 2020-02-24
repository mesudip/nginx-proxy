# this one can call itself before each configuration and modify the dhparam parameter.
from typing import Dict

from nginx_proxy import Container, Host


def process_dhparam(container: Container, environments: map, vhost_map: Dict[str, Dict[int, Host]]):
    auth_env = [e[1] for e in environments.items() if e[0].startswith("PROXY_BASIC_AUTH")]
    if len(auth_env):
        pass
