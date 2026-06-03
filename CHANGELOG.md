# Changelog

## v3.2.0

### Features
- **Static Site Hosting**: Added support for serving static sites directly from `nginx-proxy`.
    - Added `STATIC_SITE_ROOT` for scanning domain directories.
    - Serves files from `$STATIC_SITE_ROOT/$domain/current`.
    - Supports release-style symlink swaps while rejecting symlinks that resolve outside the static root.
    - Allows container routes on more specific paths to coexist with static site roots.
- **Built-In Lost Page Domains**: Added `DEFAULT_SSL_DOMAINS` for serving the bundled lost page over HTTPS for configured domains and wildcard domains.
- **Docker Swarm Prefer-Local Mode**: Added `DOCKER_SWARM=prefer-local`.
    - Routes to healthy local Swarm task containers first.
    - Keeps the Swarm service VIP as a backup upstream when local tasks are unavailable.
- **Backend Startup Handling**: Added `BACKEND_START_GRACE_SECONDS` to delay registration of containers without Docker healthchecks.
- **Reload Command**: Added a `reload` helper command to rescan Docker state and reload nginx configuration.
- **Nginx Resolver Configuration**: Added `NGINX_RESOLVER` support for runtime DNS lookups, especially when proxying ACME challenges to `CERTAPI_URL`.

### Fixes
- **Event Processing**: Reworked Docker event handling around a dispatcher queue to make container, service, network, health, reload, and delayed activation events more consistent.
- **Swarm Reliability**: Improved local and Swarm backend selection, service update handling, and retry behavior for delayed service events.
- **Nginx Configuration**:
    - Improved duplicate and wildcard host handling.
    - Fixed handling of `=` in injected nginx directives.
    - Added conflict resolution for scalar nginx location directives such as `client_max_body_size` and proxy timeout settings.
    - Improved per-backend configuration validation.
- **Static SSL**: Fixed fallback certificate handling for static SSL domains during reloads.
- **Static Site Safety**: Added validation for static root paths, domain directory names, and symlink targets.
- **Error Handling**: Improved handling of invalid nginx stderr output.
- **Redirects**: Fixed full proxy redirect handling.

### CI/CD
- Upgraded base image versions.
- Improved GitHub Actions test behavior, including single test runs per SHA.
- Fixed workflow warnings and timing-sensitive tests.

---

## v3.1.2

### Fixes
- **Certificate Renewal**: Delegated certificate renewal behavior to `certapi` and improved renewal architecture.
- **SSL Callbacks**: Fixed SSL renewal callback handling.
- **Dependencies**: Upgraded `certapi` to `1.1.9`.
- **Cleanup**: Removed unused blacklist helper code.

---

## v3.1.1

### Fixes
- **Wildcard Certificates**: Avoided preferring stale wildcard certificates.

### CI/CD
- Added package publishing setup for Python package releases.

---

## v3.1.0

### Features
- **HTTPS Redirects**: Added HTTPS auto-redirect support.
- **HTTP and HTTPS Serving**: Added support for serving HTTP and HTTPS for the same host configuration.

### Fixes
- **Regression Tests**: Fixed failing regression tests.

---

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
