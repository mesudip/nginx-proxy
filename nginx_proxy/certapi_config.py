import os
from urllib.parse import urlparse


def load_certapi_config(url: str | None = None) -> dict | None:
    """Parse the CertAPI origin shared by nginx routing and certificate clients."""
    url = (os.getenv("CERTAPI_URL", "") if url is None else url).strip().rstrip("/")
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("CERTAPI_URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("CERTAPI_URL must include a hostname")
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
