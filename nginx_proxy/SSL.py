import threading
from datetime import datetime, timezone, date
import logging
import os
import shutil
import sys
import time
from os.path import join
from typing import List, Dict, Union, Set

from nginx.Nginx import Nginx
from certapi import CloudflareChallengeSolver, AcmeCertManager, FileSystemKeyStore, CertApiException
from certapi.issuers import AcmeCertIssuer, SelfCertIssuer
from certapi.http.types import IssuedCert
from certapi.crypto import certs_from_pem, Key
from nginx.NginxChallengeSolver import NginxChallengeSolver
from certapi.client.cert_manager_client import CertManagerClient
import traceback as tb
from nginx_proxy.utils.Blacklist import Blacklist


class SSL:

    def __init__(
        self, ssl_path, nginx: Nginx, update_threshold_seconds: float, server=None, start_ssl_thread: bool = False
    ):
        self.ssl_path = ssl_path
        self.nginx = nginx
        self.server = server
        self.blacklist = Blacklist()
        self.update_threshold_secs = update_threshold_seconds
        self.cert_min_renew_threshold_secs = max(self.update_threshold_secs, 10 * 24 * 3600)

        # Internal state for background refresh
        self.cache: Dict[str, date] = {}
        self.next_ssl_expiry: Union[datetime, None] = None
        self.shutdown_requested: bool = False
        self.lock: threading.Condition = threading.Condition()
        self.certificate_expiry_thread: threading.Thread = threading.Thread(
            target=self.update_ssl_certificates, name="SSL-Refresh-Thread"
        )

        x = os.environ.get("LETSENCRYPT_API")
        if x is not None:
            if x.startswith("https://") or x.startswith("http://"):
                self.api_url = x
            else:
                self.api_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
        else:
            self.api_url = "https://acme-v02.api.letsencrypt.org/directory"

        # Check if CertManagerClient should be used
        certapi_url = os.environ.get("CERTAPI_URL", "").strip()
        self.use_certapi_server = bool(certapi_url)
        self.key_store = FileSystemKeyStore(ssl_path, keys_dir_name="private")

        if self.use_certapi_server:
            self.certapi_client = CertManagerClient(certapi_url, self.key_store)
            self.cert_backend = self.certapi_client
            self.cert_manager = None
            acme_account_key = self.key_store._get_or_generate_key("acme_account.key", key_type="ecdsa")

        else:
            self.certapi_client = None
            self.challenge_store = NginxChallengeSolver(nginx.challenge_dir, nginx)
            cert_issuer = AcmeCertIssuer.with_keystore(self.key_store, self.challenge_store, acme_url=self.api_url)

            all_stores = [self.challenge_store]
            for key, value in os.environ.items():
                if key.startswith("CLOUDFLARE_API_KEY"):
                    if value:  # Ensure the value is not None or empty
                        cloudflare = CloudflareChallengeSolver(value.strip())
                        all_stores.append(cloudflare)
                        cloudflare.cleanup_old_challenges()

            cert_min_renew_threshold_days = self.cert_min_renew_threshold_secs // (24 * 3600)
            self.cert_manager = AcmeCertManager(
                self.key_store, cert_issuer, all_stores, renew_threshold_days=cert_min_renew_threshold_days
            )
            self.cert_manager.setup()
            self.cert_backend = self.cert_manager
            acme_account_key=cert_issuer.acme.account_key,

        self.self_signer = SelfCertIssuer(
            acme_account_key, "NP", "Bagmati", "Buddhanagar", "nginx-proxy", "local.nginx-proxy.com"
        )

        if start_ssl_thread:
            self.certificate_expiry_thread.start()

    def update_ssl_certificates(self):
        while True:
            with self.lock:
                if self.shutdown_requested:
                    break

                if self.next_ssl_expiry is None:
                    print("[SSL Refresh Thread]  Looks like there no ssl certificates, Sleeping until  there's one")
                    self.lock.wait()
                    continue

                now = datetime.now(timezone.utc)
                remaining_seconds = (self.next_ssl_expiry - now).total_seconds()

                if remaining_seconds > self.update_threshold_secs:
                    print("[SSL Refresh Thread] SSL certificate status:")

                    max_size = max([len(x) for x in self.cache]) if self.cache else 10
                    for host in self.cache:
                        print(
                            f"  {host:<{max_size + 2}} -  {self._format_duration((self.cache[host] - now).total_seconds())}"
                        )

                    # Sleep until threshold, but cap at 32 days even if expiry is far away
                    max_sleep_seconds = 32 * 24 * 3600
                    # sleep 5 mins more to avoid race condition
                    sleep_seconds = min(remaining_seconds - self.update_threshold_secs + 300, max_sleep_seconds)

                    print(
                        f"[SSL Refresh Thread] All the certificates are up to date sleeping for {self._format_duration(sleep_seconds)}"
                    )
                    self.lock.wait(sleep_seconds)
                    continue

                else:
                    print(
                        f"[SSL Refresh Thread] Update threshold reached: {self._format_duration(self.update_threshold_secs)}"
                    )
                    for host_name in self.cache:
                        print(
                            f"Remaining  : {host_name} : {self._format_duration((self.cache[host_name] - now).total_seconds())}"
                        )

                    # at least gather all the certificates that expires in 10 days
                    update_threshold_secs = max(self.update_threshold_secs, self.cert_min_renew_threshold_secs)
                    expired_hosts = [
                        host_name
                        for host_name in self.cache
                        if (self.cache[host_name] - now).total_seconds() < update_threshold_secs
                    ]

                    for host_name in expired_hosts:
                        del self.cache[host_name]

            if self.server:
                self.server.reload()

    def register_certificate(self, req_domain) -> List[IssuedCert]:
        domain = [req_domain] if type(req_domain) is str else req_domain

        ## this will automatically use the configured backend
        result = self.cert_backend.issue_certificate(domain, key_type="ecdsa")
        if len(result.issued):
            print(
                "[ New Certificates      ] : ", ", ".join(flatten_2d_array(sorted([x.domains for x in result.issued])))
            )
        if len(result.existing):
            print(
                "[ Existing Certificates ] : ",
                ", ".join(flatten_2d_array(sorted([x.domains for x in result.existing]))),
            )
        return result.issued + result.existing

    def update_expiry_cache(self, certs: List[IssuedCert]):
        with self.lock:
            for cert in certs:
                for domain in cert.domains:
                    full_chain = certs_from_pem(cert.certificate.encode("utf-8"))
                    self.cache[domain] = full_chain[0].not_valid_after_utc

            if len(self.cache):
                expiry = min(self.cache.values())
                if expiry != self.next_ssl_expiry:
                    self.next_ssl_expiry = expiry
                    self.lock.notify()

    def register_certificate_or_selfsign(self, domain, no_self_check=False, ignore_existing=False) -> List[IssuedCert]:
        obtained_certificates: List[IssuedCert] = []
        for i in range(0, len(domain), 50):
            sub_list = domain[i : i + 50]
            # Filter out blacklisted domains from the sublist
            valid_list, blacklisted = self.blacklist.partition(sub_list)

            if len(blacklisted) > 0:
                print("[Blacklist] ignoring previously failed domain for 3 mins:", ", ".join(blacklisted))

            try:
                # Proceed only with the filtered sublist
                obtained: List[IssuedCert] = (
                    self.register_certificate(
                        valid_list,
                    )
                    if valid_list
                    else []
                )

                if obtained:
                    obtained_certificates.extend(obtained)
            except CertApiException as e:
                tb.print_exception(e)
                pass
        processed_set = set(flatten_2d_array([c.domains for c in obtained_certificates])).union(set(blacklisted))
        self_signed = [x for x in domain if x not in processed_set]
        if self_signed:
            # Add the self-signed domains to the blacklist
            for domain_item in self_signed:
                self.blacklist.add(domain_item)
            print("[   Self Signing        ] : ", ", ".join(self_signed))
            self.register_certificate_self_sign(self_signed)

        return obtained_certificates

    def register_certificate_self_sign(self, domain):
        if type(domain) is str:
            domain = [domain]
        for d in domain:
            if not self.key_store.find_key_by_name(d + ".selfsigned"):
                (key, cert) = self.self_signer.generate_key_and_cert_for_domain(d, key_type="ecdsa")
                key_id = self.key_store.save_key(key, d + ".selfsigned")
                self.key_store.save_cert(key_id, cert, [d], name=d + ".selfsigned")

    def _format_duration(self, seconds: float) -> str:
        days, remainder = divmod(int(seconds), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days > 0:
            parts.append(f"{days} days")
        if hours > 0 or days > 0:
            parts.append(f"{hours} hours")
        if minutes > 0 or hours > 0 or days > 0:
            parts.append(f"{minutes} minutes")
        parts.append(f"{seconds} seconds")

        return ", ".join(parts)

    def shutdown(self):
        with self.lock:
            self.shutdown_requested = True
            self.lock.notify()
        if self.certificate_expiry_thread.is_alive():
            self.certificate_expiry_thread.join(timeout=2)


def flatten_2d_array(two_d_array):
    return [item for sublist in two_d_array for item in sublist]
