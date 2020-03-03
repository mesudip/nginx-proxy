from nginx_proxy import ProxyConfigData


class RedirectProcessor:
    def __init__(self, ):
        pass

    def process_redirection(self, config: ProxyConfigData):
        redirected_hosts = {}
        for host in config.host_list():
            if host.isredirect():
                redirected_hosts[host.hostname] = host.full_redirect
                target = config.getHost(host.full_redirect.hostname)
                if target is not None:
                    if target.hostname == host.hostname:
                        host.full_redirect = None
                    if target.hostname in redirected_hosts:
                        continue
                    target.update_with_host(host)
                    host.container_set = set()
                    host.locations = {}
                    host.secured = host.secured or target.secured
                    target.secured = host.secured
                    target.update_extras(extras=host.extras)
                    host.extras = {}
            host.is_redirect = host.isredirect()
