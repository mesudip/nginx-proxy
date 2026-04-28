from datetime import datetime, timezone
from typing import List

from certapi.client import RenewalManager
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
            sync_watch_domains=self.sync_watch_domains,
            renew_threshold_days=max(1, int(self.update_threshold_secs // (24 * 3600))),
            batch_domains=self.certapi_batch_domains,
        )

        if start_ssl_thread:
            self.renewal_manager.start()

    def sync_watch_domains(self):
        if self.server is None:
            return
        domains = sorted({host.hostname for host in self.server.config_data.host_list() if host.secured})
        self.renewal_manager.set_watch_domains(domains)

    def is_certificate_fresh(self, domain: str, threshold_seconds: float | None = None) -> bool:
        result = self.key_store.find_key_and_cert_by_domain(domain)
        if result is None:
            return False

        cert = result[2][0]
        expiry = cert.not_valid_after_utc
        threshold = self.update_threshold_secs if threshold_seconds is None else threshold_seconds
        return (expiry - datetime.now(timezone.utc)).total_seconds() > threshold

    def has_certificate(self, domain: str) -> bool:
        return self.key_store.find_key_and_cert_by_domain(domain) is not None

    def has_self_signed_certificate(self, domain: str) -> bool:
        return self.key_store.find_key_by_name(domain + ".selfsigned") is not None

    def _ensure_self_signed_certificate(self, domain: str):
        if self.has_certificate(domain) or self.has_self_signed_certificate(domain):
            return

        register_self_signed = getattr(self.renewal_manager, "_register_self_signed", None)
        if register_self_signed is None:
            return
        register_self_signed(domain)

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
        if self.has_certificate(host.hostname):
            return host.hostname

        wildcard = self.wildcard_domain_name(host.hostname)
        if wildcard is not None and self.is_certificate_fresh(wildcard):
            return wildcard

        return host.hostname + ".selfsigned"

    def process_ssl_certificates(self, hosts: List[Host]):
        if not hosts:
            return

        secured_hosts = [host for host in hosts if host.secured]
        if not secured_hosts:
            return

        for host in secured_hosts:
            self._prepare_host_for_ssl(host)

        secured_domains = sorted({host.hostname for host in secured_hosts})
        self.renewal_manager.set_watch_domains(secured_domains)

        if any(self._host_needs_certificate(host) for host in secured_hosts):
            self.renewal_manager.trigger_now()

        for host in secured_hosts:
            if self._select_ssl_file(host).endswith(".selfsigned"):
                self._ensure_self_signed_certificate(host.hostname)

        for host in secured_hosts:
            host.ssl_file = self._select_ssl_file(host)

    def wildcard_domain_name(self, domain, wild_char="*"):
        slices = domain.split(".")
        if len(slices) > 2:
            return wild_char + "." + (".".join(slices[1 : len(slices)]))
        return None

    def shutdown(self):
        self.renewal_manager.stop()
