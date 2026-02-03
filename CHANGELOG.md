# Changelog

## v3.0.1

### Fixes
- **Dockerfile**: Fixed incorrect template directory path in Dockerfile which caused issues with finding `nginx.conf.jinja2`.

## v3.0.0

### Features
- **Docker Swarm Support**: Introduced support for Docker Swarm.
    - Added `DOCKER_SWARM` environment variable to control behavior (`enable`, `strict`, `exclude`).
    - Added support for watching Swarm services and handling service updates.
    - Added `SWARM_DOCKER_HOST` configuration for remote Swarm managers.
- **Sticky Sessions**: Added support for session affinity (sticky sessions) via `NGINX_STICKY_SESSION` environment variable on backend containers.
- **Nginx Configuration Templating**:
    - Added support for templating the base `nginx.conf` file using `nginx.conf.jinja2`.
    - Added support for `NGINX_WORKER_PROCESSES` (default: auto) and `NGINX_WORKER_CONNECTIONS` (default: 65535).
- **Improved SSL/CertAPI**:
    - Integrated with `certapi` library for SSL certificate requests.
    - Added support for remote `certapi` client for certificate management.
    - Made certificate renewal threshold configurable via `CERT_RENEW_THRESHOLD_DAYS`.
- **IPv6 Support**: Added support for listening on IPv6 addresses (`ENABLE_IPV6`).
- **Validation**: Added validation and error reporting for `STATIC_VIRTUAL_HOST` configuration.

### Fixes
- **Docker Compatibility**:
    - Fixed compatibility with Docker v28 and v29+ event formats (Actor.ID changes).
    - Fixed `client_max_body_size` duplication in generated configs.
- **Reliability**:
    - Improved config flushing to disk to prevent corruption (`os.sync`).
    - Fixed issues with duplicate extra entries in locations.
    - Fixed various integration tests and race conditions.
    - Fixed "proxy won't start when certapi is down" issue.
- **General**:
    - Upgraded dependencies (certapi, jinja2).
    - Improved logging for error diagnosis.

### CI/CD
- Added GitHub Actions for testing.
- Added codecov coverage reporting.

---

## v2.0.0

### Features
- **Cloudflare & Wildcard SSL**:
    - Added support for **Cloudflare DNS** challenges to issue wildcard certificates.
    - Added `CLOUDFLARE_API_KEY*` support.
    - Implemented a Cloudflare Tunnel Manager.
- **Static Virtual Hosts**: Added `STATIC_VIRTUAL_HOST` to map domains to external (non-Docker) IPs/URLs.
- **WebSockets**:
    - Added implicit and explicit WebSocket support (`wss://` scheme).
    - Support for simultaneous HTTP/WS on the same host.
- **SSL Enhancements**:
    - Transitioned to using `certapi` for easier certificate management.
    - Added support for self-verifying domains before ACME requests.
    - Configurable `DHPARAM_SIZE`.
- **Custom Error Pages**: Added support for custom error pages.

### Fixes
- **Network Discovery**:
    - Fixed container IP selection and network discovery logic.
    - Fixed network ID detection for newer Docker versions.
    - Fixed `cargroupfs2` detection issue.
- **SSL**:
    - Fixed missing certificate issues with Let's Encrypt rate-limiting response.
    - Fixed SSL auto-renew loops and threading issues.
    - Fixed CSR serialization issues with newer `pyopenssl`.
- **Configuration**:
    - Fixed empty location entry generation.
    - Fixed `client_max_body_size` handling.
- **General**:
    - Replaced `pycryptography` with `pycryptodome`.
    - Fixed container removal handling logic.
    - Ratelimited Nginx reloads to prevent thrashing.

---

## v1.0.0 (Initial Release Era)

*Note: This version covers the initial development up to the comprehensive v2.0 release.*

### Features
- **Core Reverse Proxy**: Automated Nginx configuration based on `VIRTUAL_HOST` environment variables.
- **SSL Automation**:
    - Automatic Let's Encrypt certificate generation using `getssl`.
    - Support for self-signed certificates as fallbacks.
    - Multiple domains per SSL certificate support.
- **Routing**:
    - Path-based routing support.
    - Multiple virtual hosts per container.
    - Load balancing support (Round Robin) for scaled containers.
- **Basic Auth**: Support for HTTP Basic Auth via `PROXY_BASIC_AUTH`.
- **Customization**:
    - Support for custom Nginx directives in environment variables.
    - `nginx-proxy` container configuration variables.

### Key Changes
- Implemented `DummyNginx` for testing config generation without running Nginx.
- Refactored SSL logic to support multiple hosts.
- Added Docker event listening for dynamic updates (start/die events).
- initial `getssl` script integration.
