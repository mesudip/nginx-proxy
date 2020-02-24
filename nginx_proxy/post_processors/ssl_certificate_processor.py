import threading
from datetime import date, datetime
from typing import List, Dict, Set

from nginx.Nginx import Nginx
from nginx_proxy import WebServer
from nginx_proxy.Host import Host
from nginx_proxy.SSL import SSL


class SslCertificateProcessor():
    def __init__(self, nginx: Nginx, server: WebServer):
        self.cache: Dict[str:date] = {}
        self.self_signed: Set[str] = set()
        self.shutdown_requested: bool = False
        self.lock: threading.Condition = threading.Condition()
        self.nginx: Nginx = nginx
        self.ssl: SSL = SSL("/etc/ssl", nginx)
        self.server: WebServer = server
        # self.certificate_expiry_thread = threading.Thread(target=self.check_certificate_expiry)
        #         self.certificate_expiry_thread.start()

    def update_ssl_certificates(self):
        self.lock.acquire()
        while True:
            if self.shutdown_requested:
                return
            if self.next_ssl_expiry is None:
                print("[SSL Refresh Thread]  Looks like there no ssl certificates, Sleeping until  there's one")
                self.lock.wait()
            else:
                now = datetime.now()
                remaining_days = (self.next_ssl_expiry - now).days
                remaining_days = 30 if remaining_days > 30 else remaining_days

                if remaining_days > 2:
                    print(
                        "[SSL Refresh Thread] All the certificates are up to date sleeping for" + str(
                            remaining_days) + "days.")
                    self.lock.wait((remaining_days - 2) * 3600 * 24)
                else:
                    print("[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire")
                    for x in self.cache:
                        print("Remaining days :", x, ":", (self.cache[x] - now).days)
                    x = [x for x in self.cache if (self.cache[x] - now).days < 6]
                    acme_ssl_certificates = set(self.ssl.register_certificate_or_selfsign(x, ignore_existing=True))
                    for host in x:
                        if host not in acme_ssl_certificates:
                            del self.cache[host]
                            if not self.ssl.cert_exists_wildcard(host):
                                self.self_signed.add(host)
                        else:
                            self.cache[host] = self.ssl.expiry_time(domain=host)
                    self.next_ssl_expiry = min(self.cache.values())
                    self.server.reload(forced=True)

    def process_ssl_certificates(self, hosts: List[Host]):
        ssl_requests: Set[Host] = set()
        for host in hosts:
            if host.secured:
                if int(host.port) in (80, 443):
                    host.ssl_redirect = True
                    host.port = 443
                if host.hostname in self.cache:
                    host.ssl_file = host.hostname
                else:
                    wildcard = self.ssl.wildcard_domain_name(host.hostname)
                    if wildcard is not None:
                        if self.ssl.cert_exists(wildcard):
                            host.ssl_file = wildcard
                            continue
                    # find the ssl certificate if it exists
                    time = self.ssl.expiry_time(host.hostname)
                    if (time - datetime.now()).days > 1:
                        self.cache[host.hostname] = time
                        host.ssl_file = host.hostname
                    else:
                        ssl_requests.add(host)

        if len(ssl_requests):
            registered = self.ssl.register_certificate_or_selfsign([h.hostname for h in ssl_requests],
                                                                   ignore_existing=False)
            for host in ssl_requests:
                if host.hostname not in registered:
                    host.ssl_file = host.hostname + ".selfsigned"
                    self.self_signed.add(host.hostname)
                else:
                    host.ssl_file = host.hostname
                    self.cache[host.hostname] = self.ssl.expiry_time(host.hostname)
                    host.ssl_expiry = self.cache[host.hostname]
