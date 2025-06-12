import threading
from datetime import date, datetime
from typing import List, Dict, Set, Union

from nginx.Nginx import Nginx
from nginx_proxy import WebServer
from nginx_proxy.Host import Host
from nginx_proxy.SSL import SSL


class SslCertificateProcessor():
    def __init__(self, nginx: Nginx, server: WebServer, start_ssl_thread=False,ssl_dir="/etc/ssl"):
        self.cache: Dict[str:date] = {}
        self.self_signed: Set[str] = set()
        self.shutdown_requested: bool = False
        self.lock: threading.Condition = threading.Condition()
        self.nginx: Nginx = nginx
        self.ssl: SSL = SSL(ssl_dir, nginx)
        self.server: WebServer = server
        self.next_ssl_expiry: Union[datetime, None] = None
        self.certificate_expiry_thread: threading.Thread = threading.Thread(target=self.update_ssl_certificates)
        if start_ssl_thread:
            self.certificate_expiry_thread.start()

    def update_ssl_certificates(self):
        self.lock.acquire()
        while not self.shutdown_requested:
            if self.next_ssl_expiry is None:
                print("[SSL Refresh Thread]  Looks like there no ssl certificates, Sleeping until  there's one")
                self.lock.wait()
            else:
                now = datetime.now()
                remaining_days = (self.next_ssl_expiry - now).days

                if remaining_days > 2:
                    print("[SSL Refresh Thread] SSL certificate status:")

                    max_size = max([len(x) for x in self.cache])
                    for host in self.cache:
                        print('  {host: <{width}} - {remain}'.format(host=host, width=max_size + 2,
                                                                   remain=self.cache[host] - now))
                    sleep_time = (32 if remaining_days > 30 else remaining_days) - 2
                    print(
                        "[SSL Refresh Thread] All the certificates are up to date sleeping for " + str(
                            sleep_time) + " days.")
                    self.lock.wait(sleep_time * 3600 * 24 - 10)
                else:
                    print(
                        "[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire")
                    for x in self.cache:
                        print("Remaining days :", x, ":", (self.cache[x] - now).days)
                    x = [x for x in self.cache if (self.cache[x] - now).days < 6]
                    for host in x:
                        del self.cache[host]
                    self.server.reload()

    def process_ssl_certificates(self, hosts: List[Host]):
        ssl_requests: Set[Host] = set()
        wildcard_requests: Set[Host] = set()
        self.lock.acquire()
        for host in hosts:
            if host.secured:
                is_wildcard = '*' in host.hostname

                if int(host.port) in (80, 443):
                    host.ssl_redirect = True
                    host.port = 443
                if host.hostname in self.cache:
                    host.ssl_file = host.hostname
                elif is_wildcard:
                    wildcard_requests.add(host)
                else:
                    ## reuse the wildcard certificate.
                    wildcard = self.ssl.wildcard_domain_name(host.hostname)
                    if wildcard is not None:
                        if self.ssl.cert_exists(wildcard):
                            host.ssl_file = wildcard
                            continue
                    # find the ssl certificate if it exists
                    time = self.ssl.expiry_time(host.hostname)
                    if (time - datetime.now()).days > 2:
                        self.cache[host.hostname] = time
                        host.ssl_file = host.hostname
                    else:
                        ssl_requests.add(host)



        if len(ssl_requests)>0:
            registered = self.ssl.register_certificate_or_selfsign([h.hostname for h in ssl_requests],
                                                                   ignore_existing=True)
            for host in ssl_requests:
                if host.hostname not in registered:
                    host.ssl_file = host.hostname + ".selfsigned"
                    self.self_signed.add(host.hostname)
                else:
                    host.ssl_file = host.hostname
                    self.cache[host.hostname] = self.ssl.expiry_time(host.hostname)
                    host.ssl_expiry = self.cache[host.hostname]
        if len(wildcard_requests) > 0:
            for host in wildcard_requests:
                self.ssl.register_certificate_wildcard(domain=host.hostname)

        if len(self.cache):
            expiry = min(self.cache.values())
            if expiry != self.next_ssl_expiry:
                self.next_ssl_expiry = expiry
                self.lock.notify()
        self.lock.release()

    def shutdown(self):
        self.lock.acquire()
        self.shutdown_requested = True
        self.lock.notify()
        self.lock.release()
        pass
