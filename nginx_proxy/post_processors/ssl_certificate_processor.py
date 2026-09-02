import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

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
        self._status_rows: List[Tuple[str, str, Optional[datetime]]] = []
        self._last_logged_status: Optional[Tuple] = None
        # The renewal worker may invoke the callback repeatedly (once a second) while a renewal pass is
        # still in flight on the reload thread. Only one forced reload is queued until that reload has run.
        self._renewal_reload_lock = threading.Lock()
        self._renewal_reload_pending = False
        # Expiry of every certificate file seen in the last pass, used to detect files replaced by a
        # renewal so that nginx is reloaded even when the rendered configuration text is unchanged.
        self._known_file_expiry: dict = {}
        self._certificates_changed = False

        if start_ssl_thread:
            self.start()

    def start(self):
        self.renewal_manager.start()
        backend = f"certapi {self.certapi_url}" if self.use_certapi_server else "local ACME"
        print(
            f"[SSL Refresh Thread] Started with backend={backend}, "
            f"renew threshold {self._format_duration(self.update_threshold_secs)}, "
            f"watching {len(self._status_rows)} domains"
        )
        self._log_next_check()

    def ssl_renewal_callback(self):
        if self.server is None:
            return
        with self._renewal_reload_lock:
            if self._renewal_reload_pending:
                return
            self._renewal_reload_pending = True
        print("[SSL Refresh Thread] Renewal due, requesting forced nginx reload")
        try:
            # Forced on purpose: a renewal replaces certificate file contents, not paths, so the rendered
            # config is byte-identical and a plain reload would be skipped by the config diff.
            self.server.enqueue_reload(force=True)
        except Exception:
            with self._renewal_reload_lock:
                self._renewal_reload_pending = False
            raise

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
            try:
                self.renewal_manager.update_watch_domains(secured_domains)
            finally:
                with self._renewal_reload_lock:
                    self._renewal_reload_pending = False

        for host in secured_hosts:
            host.ssl_file = self._select_ssl_file(host)

        if update_watch_domains:
            self.log_certificate_status(secured_hosts)
            self._detect_certificate_changes()

    def _detect_certificate_changes(self):
        """Flag a forced reload when a certificate file already in use now carries a different expiry."""
        current = {}
        for _domain, ssl_file, expiry in self._status_rows:
            if ssl_file and expiry is not None:
                current[ssl_file] = expiry
        changed = [f for f, e in current.items() if f in self._known_file_expiry and self._known_file_expiry[f] != e]
        if changed:
            print(
                f"[SSL Refresh Thread] Certificates renewed on disk, nginx reload required: {', '.join(sorted(changed))}"
            )
            self._certificates_changed = True
        self._known_file_expiry = current

    def pop_certificate_changes(self) -> bool:
        """Return True once if certificate files changed since the last call, then reset."""
        changed = self._certificates_changed
        self._certificates_changed = False
        return changed

    # ------------------------------------------------------------------
    # Status logging
    # ------------------------------------------------------------------

    def _certificate_expiry(self, cert_name: str) -> Optional[datetime]:
        try:
            result = self.key_store.find_key_and_cert_by_cert_id(cert_name)
        except Exception:
            return None
        if not isinstance(result, (tuple, list)) or len(result) < 2:
            return None
        certs = result[1]
        if not isinstance(certs, (list, tuple)) or not certs:
            return None
        expiry = getattr(certs[0], "not_valid_after_utc", None)
        return expiry if isinstance(expiry, datetime) else None

    def _collect_status_rows(self, hosts: List[Host]) -> List[Tuple[str, str, Optional[datetime]]]:
        rows = {}
        for host in hosts:
            if host.hostname in rows:
                continue
            ssl_file = host.ssl_file or ""
            rows[host.hostname] = (host.hostname, ssl_file, self._certificate_expiry(ssl_file) if ssl_file else None)
        return [rows[name] for name in sorted(rows)]

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Human friendly duration: '30 days', '5 hours' or '29 minutes'."""
        seconds = max(0, int(seconds))
        days, rem = divmod(seconds, 24 * 3600)
        if days:
            return f"{days} day{'s' if days != 1 else ''}"
        hours, rem = divmod(rem, 3600)
        if hours:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        minutes = max(1, rem // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    @staticmethod
    def _format_remaining(delta: timedelta) -> str:
        """'75 days, 17:00:58' style: no microseconds, zero padded time so columns line up."""
        total = int(delta.total_seconds())
        days, rem = divmod(total, 24 * 3600)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        prefix = f"{days} day{'s' if days != 1 else ''}, " if days else ""
        return f"{prefix}{hours:02}:{minutes:02}:{seconds:02}"

    def _row_text(self, hostname: str, ssl_file: str, expiry: Optional[datetime], now: datetime) -> str:
        if not ssl_file or expiry is None:
            return "no certificate"
        if expiry <= now:
            text = f"EXPIRED {self._format_remaining(now - expiry)} ago"
        else:
            text = self._format_remaining(expiry - now)
        if ssl_file != hostname:
            text += f" ({ssl_file})"
        return text

    def _next_check(self, now: datetime) -> Optional[Tuple[float, str, datetime]]:
        """Return (seconds_until_check, domain, expiry) for the earliest real certificate, or None."""
        candidates = [
            (expiry, domain)
            for domain, ssl_file, expiry in self._status_rows
            if expiry is not None and not ssl_file.endswith(".selfsigned") and expiry > now
        ]
        if not candidates:
            return None
        expiry, domain = min(candidates)
        threshold = getattr(self.renewal_manager, "update_threshold_secs", None)
        if not isinstance(threshold, (int, float)):
            threshold = self.update_threshold_secs
        slack = getattr(self.renewal_manager, "sleep_slack_seconds", None)
        if not isinstance(slack, (int, float)):
            slack = 300
        max_sleep = getattr(self.renewal_manager, "max_sleep_seconds", None)
        if not isinstance(max_sleep, (int, float)):
            max_sleep = 32 * 24 * 3600
        wait = (expiry - now).total_seconds() - threshold
        wait = min(wait + slack, max_sleep) if wait > 0 else 0
        return wait, domain, expiry

    def _log_next_check(self, now: Optional[datetime] = None):
        now = now or datetime.now(timezone.utc)
        next_check = self._next_check(now)
        if next_check is None:
            print("[SSL Refresh Thread] Looks like there are no ssl certificates, sleeping until there's one")
            return
        wait, domain, expiry = next_check
        if wait <= 0:
            print(
                f"[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire "
                f"({domain} expires in {self._format_remaining(expiry - now)})"
            )
        else:
            print(
                f"[SSL Refresh Thread] All the certificates are up to date sleeping for {self._format_duration(wait)}."
            )

    def log_certificate_status(self, hosts: List[Host], force: bool = False):
        """
        Print the watched domains with the time left on the certificate each one serves, followed by
        when the renewal thread will check again. Printed only when something changed unless forced.
        """
        try:
            now = datetime.now(timezone.utc)
            self._status_rows = self._collect_status_rows(hosts)
            snapshot = tuple((d, f, e.isoformat() if e else None) for d, f, e in self._status_rows)
            if not force and snapshot == self._last_logged_status:
                return
            self._last_logged_status = snapshot

            real_rows = [row for row in self._status_rows if not row[1].endswith(".selfsigned")]
            self_signed = [row[0] for row in self._status_rows if row[1].endswith(".selfsigned")]
            print("[SSL Refresh Thread] SSL certificate status:")
            max_size = max([len(d) for d, _, _ in real_rows] + [0])
            for domain, ssl_file, expiry in real_rows:
                print(f"  {domain:<{max_size + 2}} - {self._row_text(domain, ssl_file, expiry, now)}")
            self._log_next_check(now)
            if self_signed:
                print(f"[SSL Refresh Thread] Selfsigned: {', '.join(self_signed)}")
        except Exception as e:  # logging must never break a reload
            print(f"[SSL Refresh Thread] Could not render certificate status: {e.__class__.__name__}: {e}")

    def wildcard_domain_name(self, domain, wild_char="*"):
        slices = domain.split(".")
        if len(slices) > 2:
            return wild_char + "." + (".".join(slices[1 : len(slices)]))
        return None

    def shutdown(self):
        self.renewal_manager.stop()
