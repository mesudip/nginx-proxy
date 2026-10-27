import ipaddress
import os
import re
from urllib.parse import urlparse

# DNS names and Docker service names (which may contain "_"); IP literals are checked separately.
HOST_NAME = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9_-]{1,63}(?<!-))*\.?$")


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(HOST_NAME.match(host))


def load_certapi_config(url: str | None = None) -> dict | None:
    """Parse the CertAPI origin shared by nginx routing and certificate clients."""
    # A trailing "/", or an empty query or fragment ("http://certapi:8080/?#"), still names just the origin.
    url = (os.getenv("CERTAPI_URL", "") if url is None else url).strip().rstrip("/?#")
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("CERTAPI_URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("CERTAPI_URL must include a hostname")
    if not _valid_host(parsed.hostname):
        raise ValueError(f"CERTAPI_URL has an invalid hostname: {parsed.hostname}")
    if parsed.path or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError("CERTAPI_URL must be an HTTP(S) origin without a path, query, fragment, or credentials")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"Invalid CERTAPI_URL port: {error}") from error
    if port == 0:
        raise ValueError("CERTAPI_URL port must be between 1 and 65535")
    port = port or (443 if parsed.scheme == "https" else 80)
    endpoint_host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    default_port = 443 if parsed.scheme == "https" else 80
    return {
        "url": url,
        "host": parsed.hostname,
        "scheme": parsed.scheme,
        "port": port,
        "endpoint": f"{endpoint_host}:{port}",
        # Host header for requests proxied to CertAPI, so a vhost-routed CertAPI sees its own name.
        "host_header": endpoint_host if port == default_port else f"{endpoint_host}:{port}",
    }
