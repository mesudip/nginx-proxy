"""
A new https domain whose first issuance fails must be retried by later renders with the same domain set.

certapi's renewal worker schedules wake-ups only from its expiry cache. A domain whose first obtain fails gets a
`<domain>.selfsigned` certificate and a blacklist entry, but no cache entry (the self-signed one is stored under
its own name) and no scheduled retry (that is only done for domains that already have a certificate). The retry
therefore depends on nginx-proxy publishing the watch set again once the blacklist window is over, which happens
on every render: Docker events and the `reload` command.

Runs against the real certapi RenewalManager and FileSystemKeyStore; only the ACME/CertAPI backend is faked.
"""

import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from certapi import FileSystemKeyStore
from certapi.crypto import Key
from certapi.http.types import CertificateResponse, IssuedCert
from certapi.issuers import SelfCertIssuer

import nginx_proxy.post_processors.ssl_certificate_processor as ssl_processor_module
from nginx_proxy.Host import Host


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


class FlakyBackend:
    """Fails like an ACME order for a domain whose DNS does not point here yet, until `ready` is set."""

    def __init__(self, key_store: FileSystemKeyStore):
        self.key_store = key_store
        self.issuer = SelfCertIssuer(Key.generate("ecdsa"), common_name="test CA")
        self.ready = False
        self.calls = []

    def obtain(self, domains, **kwargs):
        self.calls.append(list(domains))
        if not self.ready:
            raise RuntimeError("DNS for the domain does not point at this proxy yet")
        issued = []
        for domain in domains:
            key, cert = self.issuer.generate_key_and_cert_for_domain(domain, key_type="ecdsa")
            key_id = self.key_store.save_key(key, domain)
            self.key_store.save_cert(key_id, cert, [domain])
            issued.append(IssuedCert(key=key, cert=cert, domains=[domain]))
        return CertificateResponse(issued=issued, existing=[])


def test_failed_first_issuance_is_retried_by_later_renders(tmp_path):
    key_store = FileSystemKeyStore(str(tmp_path), keys_dir_name="private")
    backend = FlakyBackend(key_store)
    backend_info = SimpleNamespace(
        backend=backend,
        key_store=key_store,
        certapi_url="",
        use_certapi_server=False,
        batch_domains=True,
        cert_manager=None,
        certapi_client=None,
        challenge_store=None,
    )
    server = SimpleNamespace(config={}, enqueue_reload=Mock())
    with patch.object(ssl_processor_module, "build_certificate_backend", return_value=backend_info):
        processor = ssl_processor_module.SslCertificateProcessor(
            SimpleNamespace(challenge_dir=str(tmp_path)), server=server, update_threshold_days=30
        )
    # 180 seconds in production; shortened so the retry window opens within the test.
    processor.renewal_manager.blacklist_duration_seconds = 0.2
    host = Host("new.example.com", 443, {"https"})

    try:
        processor.process_ssl_certificates([host])
        processor.start()
        assert wait_for(lambda: os.path.exists(tmp_path / "certs" / "new.example.com.selfsigned.crt"))
        assert backend.calls == [["new.example.com"]]

        # DNS gets fixed and the blacklist window passes. Docker events and `reload` re-render the same set.
        backend.ready = True
        time.sleep(0.3)
        for _ in range(3):
            processor.process_ssl_certificates([host])
            time.sleep(0.1)

        assert wait_for(lambda: len(backend.calls) >= 2), f"issuance was never retried: {backend.calls}"
        assert wait_for(lambda: key_store.find_key_and_cert_by_domain("new.example.com") is not None)
        assert wait_for(lambda: server.enqueue_reload.call_count >= 2)  # self-signed first, then the real one
        processor.process_ssl_certificates([host])
        assert host.ssl_file == "new.example.com"
    finally:
        processor.shutdown()
