import threading
from datetime import date, datetime, timezone
from typing import List, Dict, Set, Union

from nginx.Nginx import Nginx
from nginx_proxy import WebServer
from nginx_proxy.Host import Host
from nginx_proxy.SSL import SSL
from certapi.http.types import IssuedCert
from certapi.crypto import certs_from_pem


import traceback
class SslCertificateProcessor:
    def __init__(self, nginx: Nginx, server: WebServer, start_ssl_thread=False, ssl_dir="/etc/ssl"):
        self.cache: Dict[str:date] = {}
        self.self_signed: Set[str] = set()
        self.shutdown_requested: bool = False
        self.lock: threading.Condition = threading.Condition()
        self.nginx: Nginx = nginx
        self.ssl: SSL = SSL(ssl_dir, nginx)
        self.server: WebServer = server
        self.next_ssl_expiry: Union[datetime, None] = None
        self.certificate_expiry_thread: threading.Thread = threading.Thread(target=self.update_ssl_certificates)
        self.update_threshold = (90 * 24 * 3600) - (4 * 60) # Trigger refresh within 4 minutes for testing
        # self.update_threshold = 2 * 24 * 3600 # 2 days in seconds (must not be more thaan 70 days)

        if start_ssl_thread:
            self.certificate_expiry_thread.start()

    def update_ssl_certificates(self):
        self.lock.acquire()
        while not self.shutdown_requested:
            if self.next_ssl_expiry is None:
                print("[SSL Refresh Thread]  Looks like there no ssl certificates, Sleeping until  there's one")
                self.lock.wait()
            else:
                now = datetime.now(timezone.utc)
                remaining_seconds = (self.next_ssl_expiry - now).total_seconds()
                
                if remaining_seconds > self.update_threshold:
                    print("[SSL Refresh Thread] SSL certificate status:")

                    max_size = max([len(x) for x in self.cache])
                    for host in self.cache:
                        remaining = self.cache[host] - now
                        days = remaining.days
                        hours, remainder = divmod(remaining.seconds, 3600)
                        minutes, seconds = divmod(remainder, 60)
                        
                        print(
                            f"  {host:<{max_size + 2}} -  {days} days, {hours:02} hours, {minutes:02} minutes, {seconds:02} sec"
                        )
                    # Sleep until threshold, but cap at 32 days if expiry is far away
                    max_sleep_seconds = 32 * 24 * 3600
                    sleep_seconds = min(remaining_seconds - self.update_threshold, max_sleep_seconds)
                    
                    print(
                        f"[SSL Refresh Thread] All the certificates are up to date sleeping for {sleep_seconds} seconds."
                    )
                    self.lock.wait(sleep_seconds)
                else:
                    print("[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire")
                    for x in self.cache:
                        print("Remaining days :", x, ":", (self.cache[x] - now).days)
                    
                    # Refresh certificates that are within (threshold + 5 days) for some buffer
                    buffer_seconds = 5 * 24 * 3600
                    x = [x for x in self.cache if (self.cache[x] - now).total_seconds() < (self.update_threshold + buffer_seconds)]

                    for host in x:
                        del self.cache[host]
                    self.server.reload() # this doesn't renew the certificates. 

    def _prepare_host_for_ssl(self, host: Host):
        """Sets SSL redirect and port if applicable."""
        if int(host.port) in (80, 443):
            host.ssl_redirect = True
            host.port = 443

    def _assign_existing_cert(self, host: Host, registered: Set[str]) -> bool:
        """
        Checks for existing certificate in cache or as wildcard and assigns it.
        Returns True if a certificate was assigned, False otherwise.
        """
        if host.hostname in self.cache:
            host.ssl_file = host.hostname
            registered.add(host.hostname)
            return True
        
        # Reuse the wildcard certificate if available and registered
        wildcard = self.wildcard_domain_name(host.hostname)
        if wildcard is not None:
            if  (wildcard in registered) or (wildcard in self.cache):
                host.ssl_file = wildcard
                return True
        return False

    def _update_host_ssl_info(self, host: Host, registered: Set[str],certs:List[IssuedCert]):
        """
        Updates host.ssl_file, self.cache, and host.ssl_expiry based on registration status.
        Assumes host.secured is True.
        """
        if (host.hostname  in registered) or (host.hostname in self.cache):
            host.ssl_file = host.hostname 
             
        else:
            wildcard_domain = self.wildcard_domain_name(host.hostname)
            if wildcard_domain and ((wildcard_domain in registered) or (wildcard_domain in self.cache)):
                host.ssl_file = wildcard_domain
            else:
                host.ssl_file = host.hostname + ".selfsigned"
                self.self_signed.add(host.hostname) 

    def process_ssl_certificates(self, hosts: List[Host]):
        if not hosts:
            return
        self.lock.acquire()
        registered: Set[str] = set()
        new_certs:List[IssuedCert]=[]
        non_wildcards: List[Host]=[]
        try:
            # First pass: Handle wildcard certificates immediately, one by one.
            for host in hosts:
                if host.secured :
                    self._prepare_host_for_ssl(host)
                    if host.hostname.startswith('*.'):
                        if not self._assign_existing_cert(host, registered):
                                try:
                                    registered_ssl = self.ssl.register_certificate(host.hostname)
                                    if len(registered_ssl) > 0:
                                        registered.add(host.hostname)
                                        new_certs.extend(registered_ssl)
                                        continue
                                except Exception as e:
                                    print(f"Self signing certificate {host.hostname}: {e}")
                                    traceback.print_exception(e)
                                self.ssl.register_certificate_self_sign(host.hostname)

                    else:
                        non_wildcards.append(host)
            
            missing_certs: List[str] = []

            # First read from the cache.
            for host in non_wildcards:
                if not self._assign_existing_cert(host, registered):
                    missing_certs.append(host.hostname)

            # Batch process regular certificates
            # TODO missing_cert includes the domains already included by wildcard too.
            if len(missing_certs) > 0:
                try:
                    new_registrations = self.ssl.register_certificate_or_selfsign(
                        missing_certs
                    )
                    registered.update(domain for x in new_registrations for domain in x.domains)
                    new_certs.extend(new_registrations)
                except Exception as e:
                    print(f"Self signing certificate {host.hostname}: {e}")
                    traceback.print_exception(e)

            # Final pass: Update SSL info for all hosts
            for host in hosts:
                if host.secured:
                    self._update_host_ssl_info(host, registered,new_certs)

            for cert in new_certs:
                for domain in cert.domains:
                    full_chain = certs_from_pem(cert.certificate.encode('utf-8'))
                    self.cache[domain]=full_chain[0].not_valid_after_utc
                    
            # # Update next_ssl_expiry
            if len(self.cache):
                expiry = min(self.cache.values())
                if expiry != self.next_ssl_expiry:
                    self.next_ssl_expiry = expiry
                    if (self.next_ssl_expiry - datetime.now(timezone.utc)).total_seconds() < self.update_threshold:
                        self.lock.notify()
        except Exception as e:
            print("Unexpected error processing ssl certificates.")
            traceback.print_exception(e)
        finally:    
            self.lock.release()

    def wildcard_domain_name(self, domain, wild_char="*"):
        slices = domain.split(".")
        if len(slices) > 2:
            return wild_char + "." + (".".join(slices[1 : len(slices)]))
        return None

    def shutdown(self):
        self.lock.acquire()
        self.shutdown_requested = True
        self.lock.notify()
        self.lock.release()
        pass
