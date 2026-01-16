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
        self.update_threshold = (90 * 24 * 3600) - (3 * 60) - 3600  # Trigger refresh within 4 minutes for testing
        # self.update_threshold = 52 * 24 * 3600 # 52 days in seconds (must not be more thaan 70 days)

        self.self_signed: Set[str] = set()
        self.nginx: Nginx = nginx
        self.ssl: SSL = SSL(
            ssl_dir,
            nginx,
            update_threshold_seconds=self.update_threshold,
            server=server,
            start_ssl_thread=start_ssl_thread,
        )
        self.server: WebServer = server

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
        if host.hostname in self.ssl.cache:
            host.ssl_file = host.hostname
            registered.add(host.hostname)
            return True

        # Reuse the wildcard certificate if available and registered
        wildcard = self.wildcard_domain_name(host.hostname)
        if wildcard is not None:
            if (wildcard in registered) or (wildcard in self.ssl.cache):
                host.ssl_file = wildcard
                return True
        return False

    def _update_host_ssl_info(self, host: Host, registered: Set[str]):
        """
        Updates host.ssl_file based on registration status.
        Assumes host.secured is True.
        """
        if (host.hostname in registered) or (host.hostname in self.ssl.cache):
            host.ssl_file = host.hostname

        else:
            wildcard_domain = self.wildcard_domain_name(host.hostname)
            if wildcard_domain and ((wildcard_domain in registered) or (wildcard_domain in self.ssl.cache)):
                host.ssl_file = wildcard_domain
            else:
                host.ssl_file = host.hostname + ".selfsigned"
                self.self_signed.add(host.hostname)

    def process_ssl_certificates(self, hosts: List[Host]):
        if not hosts:
            return
        registered: Set[str] = set()
        new_certs: List[IssuedCert] = []
        non_wildcards: List[Host] = []
        try:
            # First pass: Handle wildcard certificates immediately, one by one.
            for host in hosts:
                if host.secured:
                    self._prepare_host_for_ssl(host)
                    if host.hostname.startswith("*."):
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
            if len(missing_certs) > 0:
                try:
                    new_registrations = self.ssl.register_certificate_or_selfsign(missing_certs)
                    registered.update(domain for x in new_registrations for domain in x.domains)
                    new_certs.extend(new_registrations)
                except Exception as e:
                    print(f"Error processing certificates for {missing_certs}: {e}")
                    traceback.print_exception(e)

            # Final pass: Update SSL info for all hosts
            for host in hosts:
                if host.secured:
                    self._update_host_ssl_info(host, registered)
            if len(new_certs) > 0:
                self.ssl.update_expiry_cache(new_certs)

        except Exception as e:
            print("Unexpected error processing ssl certificates.")
            traceback.print_exception(e)

    def wildcard_domain_name(self, domain, wild_char="*"):
        slices = domain.split(".")
        if len(slices) > 2:
            return wild_char + "." + (".".join(slices[1 : len(slices)]))
        return None

    def shutdown(self):
        self.ssl.shutdown()
