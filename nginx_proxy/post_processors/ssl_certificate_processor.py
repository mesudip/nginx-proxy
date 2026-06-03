from datetime import datetime, timezone
from typing import List, Tuple

from certapi.client import RenewalManager
from certapi.crypto import Key, Certificate
from nginx.Nginx import Nginx
from nginx_proxy import WebServer
from nginx_proxy.Host import Host
from nginx_proxy.certificate_backend import build_certificate_backend


class SslCertificateProcessor:
    def __init__(
        self, nginx: Nginx, server: WebServer, start_ssl_thread=False, ssl_dir="/etc/ssl", update_threshold_days=10
    ):
        self.nginx: Nginx = nginx
        self.server: WebServer = server
        self.update_threshold_secs = (10 if update_threshold_days is None else update_threshold_days) * 24 * 3600
        self.cert_min_renew_threshold_secs = max(self.update_threshold_secs, 10 * 24 * 3600)
        backend_info = build_certificate_backend(
            ssl_dir,
            nginx,
            config=server.config if server is not None else {},
            renew_threshold_days=self.cert_min_renew_threshold_secs // (24 * 3600),
        )
        self.backend = backend_info.backend
        self.key_store = backend_info.key_store
        self.certapi_url = backend_info.certapi_url
        self.use_certapi_server = backend_info.use_certapi_server
        self.certapi_batch_domains = backend_info.batch_domains
        self.cert_manager = backend_info.cert_manager
        self.certapi_client = backend_info.certapi_client
        self.challenge_store = backend_info.challenge_store
        self.renewal_manager = RenewalManager(
            self.backend,
            renewal_callback=self.ssl_renewal_callback,
            renew_threshold_days=max(1, int(self.update_threshold_secs // (24 * 3600))),
            batch_domains=self.certapi_batch_domains,
        )

        if start_ssl_thread:
            self.start()

    def start(self):
        self.renewal_manager.start()

    def ssl_renewal_callback(self):
        print("[SSL] Renewal callback triggered")
        if self.server is None:
            return
        self.server.enqueue_reload(force=True)

    def _find_certificate_for_domain(self, domain: str) -> None | Tuple[str, Key, List[Certificate]]:
        if hasattr(self.key_store, "find_key_and_cert_covering_domain"):
            result = self.key_store.find_key_and_cert_covering_domain(domain)
            if isinstance(result, tuple) and len(result) == 4:
                matched_domain, _cert_id, key, certs = result
                return (matched_domain, key, certs)
        result = self.key_store.find_key_and_cert_by_domain(domain)
        if result is None:
            return None
        return (domain, result[1], result[2])

    def is_certificate_fresh(self, domain: str, threshold_seconds: float | None = None) -> bool:
        result = self._find_certificate_for_domain(domain)
        if result is None:
            return False

        cert = result[2][0]
        expiry = cert.not_valid_after_utc
        threshold = self.update_threshold_secs if threshold_seconds is None else threshold_seconds
        return (expiry - datetime.now(timezone.utc)).total_seconds() > threshold

    def has_certificate(self, domain: str) -> bool:
        return self._find_certificate_for_domain(domain) is not None

    def _prepare_host_for_ssl(self, host: Host):
        """Sets SSL redirect and port if applicable."""
        if int(host.port) in (80, 443):
            host.ssl_redirect = True
            host.port = 443

    def _has_fresh_wildcard_certificate(self, hostname: str) -> bool:
        wildcard = self.wildcard_domain_name(hostname)
        return wildcard is not None and self.is_certificate_fresh(wildcard)

    def _host_needs_certificate(self, host: Host) -> bool:
        if self.has_certificate(host.hostname):
            return False
        return not self._has_fresh_wildcard_certificate(host.hostname)

    def _select_ssl_file(self, host: Host) -> str:
        if not host.hostname.startswith("*."):
            wildcard = self.wildcard_domain_name(host.hostname)
            if wildcard is not None and self.is_certificate_fresh(wildcard):
                return wildcard

        result = self._find_certificate_for_domain(host.hostname)
        if result is not None:
            return result[0]

        wildcard = self.wildcard_domain_name(host.hostname)
        if wildcard is not None and self.has_certificate(wildcard):
            return wildcard

        return host.hostname + ".selfsigned"

    def process_ssl_certificates(self, hosts: List[Host], update_watch_domains: bool = True):
        if not hosts:
            return

        secured_hosts = [host for host in hosts if host.secured]
        if not secured_hosts:
            return

        for host in secured_hosts:
            self._prepare_host_for_ssl(host)

        secured_domains = sorted({host.hostname for host in secured_hosts})
        if update_watch_domains:
            self.renewal_manager.update_watch_domains(secured_domains)

        for host in secured_hosts:
            host.ssl_file = self._select_ssl_file(host)

    def wildcard_domain_name(self, domain, wild_char="*"):
        slices = domain.split(".")
        if len(slices) > 2:
            return wild_char + "." + (".".join(slices[1 : len(slices)]))
        return None

    def shutdown(self):
        self.renewal_manager.stop()
