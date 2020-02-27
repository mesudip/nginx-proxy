# this one will get the list of environment variables and will process them.
from typing import Dict

from nginx import Url
from nginx_proxy import ProxyConfigData
from nginx_proxy.Host import Host

htaccess_folder = "/etc/nginx/generated/htaccess"
from docker.models.containers import Container


def process_default_server(container: Container, environments: Dict[str, str], vhosts: ProxyConfigData):
    if 'PROXY_DEFAULT_SERVER' in environments:
        server: str = environments['PROXY_DEFAULT_SERVER']
        if server.strip().lower() == 'true':
            url = Url.parse("")
        else:
            url = Url.parse(server, default_port=80)
        if url.hostname is None:
            if len(vhosts) is 1:
                for host in vhosts.host_list():
                    pass
            else:
                print("DEFAULT_SERVER configured for ", container.name, "but has multiple hosts")
        else:
            host = vhosts.getHost(hostname=url.hostname)
            if host is None:
                host = Host.fromurl(url)
                vhosts.add_host(host)
        host.update_extras_content("default_server", "default_server")
