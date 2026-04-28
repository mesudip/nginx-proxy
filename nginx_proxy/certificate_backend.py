import os
from dataclasses import dataclass
from typing import Any

from certapi import AcmeCertManager, CloudflareChallengeSolver, FileSystemKeyStore
from certapi.client.cert_manager_client import CertManagerClient
from certapi.issuers import AcmeCertIssuer
from nginx.Nginx import Nginx
from nginx.NginxChallengeSolver import NginxChallengeSolver


@dataclass
class CertificateBackend:
    backend: Any
    key_store: FileSystemKeyStore
    certapi_url: str
    use_certapi_server: bool
    batch_domains: bool
    cert_manager: AcmeCertManager | None = None
    certapi_client: CertManagerClient | None = None
    challenge_store: NginxChallengeSolver | None = None


def build_certificate_backend(
    ssl_path: str,
    nginx: Nginx,
    config: dict | None = None,
    renew_threshold_days: int = 10,
) -> CertificateBackend:
    config = config or {}
    batch_domains = os.getenv("CERTAPI_BATCH_DOMAINS", "true").strip().lower() not in {"0", "false", "no", "off"}
    certapi_url = ""
    use_certapi_server = False

    if config.get("certapi"):
        use_certapi_server = True
        certapi_url = config["certapi"]["url"]
    elif os.environ.get("CERTAPI_URL"):
        use_certapi_server = True
        certapi_url = os.environ.get("CERTAPI_URL").strip()

    key_store = FileSystemKeyStore(ssl_path, keys_dir_name="private")

    if use_certapi_server:
        client = CertManagerClient(certapi_url, key_store)
        return CertificateBackend(
            backend=client,
            key_store=key_store,
            certapi_url=certapi_url,
            use_certapi_server=True,
            batch_domains=batch_domains,
            certapi_client=client,
        )

    acme_url = os.environ.get("LETSENCRYPT_API")
    if acme_url is not None and not acme_url.startswith(("https://", "http://")):
        acme_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
    if acme_url is None:
        acme_url = "https://acme-v02.api.letsencrypt.org/directory"

    challenge_store = NginxChallengeSolver(nginx.challenge_dir, nginx)
    cert_issuer = AcmeCertIssuer.with_keystore(key_store, challenge_store, acme_url=acme_url)

    challenge_solvers = [challenge_store]
    for key, value in os.environ.items():
        if key.startswith("CLOUDFLARE_API_KEY") and value:
            cloudflare = CloudflareChallengeSolver(value.strip())
            challenge_solvers.append(cloudflare)
            cloudflare.cleanup_old_challenges()

    cert_manager = AcmeCertManager(
        key_store,
        cert_issuer,
        challenge_solvers,
        renew_threshold_days=renew_threshold_days,
    )
    cert_manager.setup()

    return CertificateBackend(
        backend=cert_manager,
        key_store=key_store,
        certapi_url=certapi_url,
        use_certapi_server=False,
        batch_domains=batch_domains,
        cert_manager=cert_manager,
        challenge_store=challenge_store,
    )
