Nginx-Proxy
===================================================
[![Run Tests](https://github.com/mesudip/nginx-proxy/actions/workflows/run-tests.yml/badge.svg?branch=master)](https://github.com/mesudip/nginx-proxy/actions/workflows/run-tests.yml)
[![codecov](https://codecov.io/github/mesudip/nginx-proxy/graph/badge.svg?token=S2GZ0ISOON)](https://codecov.io/github/mesudip/nginx-proxy)
[![Docker Pulls](https://img.shields.io/docker/pulls/mesudip/nginx-proxy)](https://hub.docker.com/layers/mesudip/nginx-proxy/latest)

Fully automated Nginx reverse proxy for Docker and Swarm.

No more writing configuration files. `nginx-proxy` watches for container changes and automatically generates reverse proxy configurations. It supports SSL via Let's Encrypt, Basic Auth, and custom Nginx directives—all configured through environment variables.


### Key Features
- **Zero-config Nginx:** Automatically discovers and routes traffic to containers and services
- **SSL Automation:** Automatic SSL certificates via Let's Encrypt (ACME).
- **Flexible Routing:** Host-based, path-based, and multiple domains per container.
- **Advanced Features:** Sticky sessions, basic auth, redirects, and custom Nginx directives.
- **Swarm Ready:** Compatible with Docker Swarm services.

## Philosophy
`nginx-proxy` is built on the belief that infrastructure should be invisible. Once deployed you should never have to know that this service is running.

## Quick Start
### 1. Start nginx-proxy

**Note** When migrating to v3, old volumes should be removed. 
```bash
docker network create frontend

docker run -d --restart always \
    --name nginx-proxy \
    --network frontend \
    -p 80:80 \
    -p 443:443 \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v nginx_data:/etc/nginx \
    -v nginx_logs:/var/log/nginx \
    mesudip/nginx-proxy:v3
```

### 2. Run a Service
Start a container on the same network and set the `VIRTUAL_HOST` environment variable.

#### Example: WordPress
```bash
docker run -d \
    --name wordpress \
    --network frontend \
    -e VIRTUAL_HOST="blog.example.com" \
    wordpress
```

#### Example: Private Registry (Advanced)
```bash
docker run -d \
    --name registry \
    --network frontend \
    -e VIRTUAL_HOST="htttps://registry.example.com/v2" \
    -e PROXY_BASIC_AUTH="registry.example.com -> user1:pass1,user2:pass2" \
    -e "client_max_body_size=2g" \
    registry:2
```

## Documentation
- [Configuration](#configuration)
- [Virtual Hosts](#virtual-hosts)
- [SSL Support](#ssl-support)
- [Docker Swarm](#docker-swarm-support)
- [Advanced Features](#advanced-features)

### Configuration

#### Volume layouts
- `/etc/nginx/conf.d` – rendered configs (`nginx-proxy.conf`) plus any custom files you mount.
- `/etc/nginx/ssl` – certificate material, split into `/certs` and `/private` (override with `SSL_DIR`, `SSL_CERTS_DIR`, `SSL_KEY_DIR`).
- `/etc/nginx/challenges` – ACME challenge payloads, configurable via `CHALLENGE_DIR` and `WELLKNOWN_PATH`.
- `/var/log/nginx` – access/error logs.

#### NginxProxy Configuration variables
Control the default behavior of `nginx-proxy`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CLIENT_MAX_BODY_SIZE` | `1m` | Default max body size for uploads. |
| `NGINX_WORKER_PROCESSES` | `auto` | Number of Nginx worker processes. |
| `NGINX_WORKER_CONNECTIONS` | `65535` | Max connections per worker. |
| `CERT_RENEW_THRESHOLD_DAYS` | `30` | By default certificates are renewed when they have <=30 days remaining. |
| `ENABLE_IPV6` | `false` | Enable IPv6 support on nginx. |
| `DOCKER_SWARM` | `ignore` | Treats every container like local by defeault. Set  `enable` for Swarm support, `strict` for Swarm-only or`exclude` to not include swarm containers  |
| `SWARM_DOCKER_HOST` | - | URL of the Swarm manager socket (e.g., `tcp://manager:2375`). |
| `CERTAPI_URL` | - | External Certificate API URL. |
| `CHALLENGE_DIR` | `/etc/nginx/challenges/` | Base directory for acme challenge store, when requesting certificates with acme. `.well-known/acme-challenge` folder lives inside this.|
| `CLOUDFLARE_API_KEY_KEY*` | - | Cloudflare api keys to issue DNS certificates.|


## Virtual Hosts
To expose a container, set the `VIRTUAL_HOST*` environment variable. The container must be on the same Docker network as `nginx-proxy`.

### Configuration Format
`VIRTUAL_HOST=[<scheme>://] <domain> [<path>] [-> [:<port>] <path>] [; <nginx_directives>]`

### Examples
| VIRTUAL_HOST  | Description |
| :--- | :--- |
| `example.com` | Proxies to exposed container port. |
| `example.com -> :8080` | Proxies to port 8080. |
| `https://example.com` | HTTPS proxy to container exposed http port |
| `https://example.com/api` | Only /api path is passed on to container. /api prefix is not removed |
| `https://example.com/api -> /v1` | Re-maps path `/api` to `/v1` on contianer exposed port|
| `https://example.com/api -> :8080/v1` | Re-maps path `/api` to `/v1` on contianer port 8080|
| `https://example.com/api -> https://_:8080/v1` | Re-maps path `/api` to `/v1` on port 8080. Https is used to connect to container|

**Note:** Directives separated by `;` are injected into the Nginx `location` block.
Example: `VIRTUAL_HOST=site.com; proxy_read_timeout 900;`

### WebSockets
WebSocket support requires explicit configuration if the protocol is not detected automatically.

- **Explicit:** `VIRTUAL_HOST=wss://ws.example.com -> :8080/websocket`
- **Auto-Upgrade:** `VIRTUAL_HOST=https+wss://example.com` (Supports both HTTP and WSS on te host).

### Multiple Hosts
A single container can serve multiple domains path mappings. All that matters is that ev variable starts with `VIRTUAL_HOST` e.g. `VIRTUAL_HOST1`, `VIRTUAL_HOST2`, etc.

**Example:**
```bash
docker run -d --network frontend \
    -e "VIRTUAL_HOST_API=https://api.example.com -> :3000" \
    -e "VIRTUAL_HOST_ADMIN=https://admin.example.com -> :4000" \
    my-app
```

### Static Virtual Hosts
Proxy to external hosts, (not in Docker) using `STATIC_VIRTUAL_HOST`. The container must be running for the site to be live. 

Format: `STATIC_VIRTUAL_HOST=domain.com->http://192.168.0.1:8080`.

**Note** Beaware that if domain as target, nginx will crash if DNS resolution fails.

## Docker Swarm Support [Preview]
Enable warm mode by setting `DOCKER_SWARM` to `enable` (local & swarm) or `strict` (swarm only).
If current node is not manager, set `SWARM_DOCKER_HOST=tcp://manager:2375`.

**Warning** : Automatic exposed port detection will not work when swrm support is enabled. You must explicitly set port on the `VIRTUAL_HOST` or set `VIRTUAL_PORT` on the container.

## Advanced Features
### Redirection
Redirect traffic from one domain to another.
```bash
-e 'VIRTUAL_HOST=https://example.uk' \
-e 'PROXY_FULL_REDIRECT=example.com,www.example.uk -> example.uk'
```

### Sticky Sessions
Enable session affinity (sticky sessions) for load balancing.
Set `NGINX_STICKY_SESSION` on the **backend container**.
- `true` or `ip_hash` – enable `ip_hash` balancing.
- `false` – disable stickiness (round-robin).
- Any other string – injected verbatim (e.g., `hash $cookie_sessionid consistent`).

## SSL Support
`nginx-proxy` automatically requests and renews Let's Encrypt certificates.

### Automatic SSL
1. Expose ports 80 and 443 on `nginx-proxy`.
2. Map `/etc/nginx/ssl` and `/etc/nginx/challenges`.
3. Set `VIRTUAL_HOST` to start with `https://` in your containers.

### Wildcard Certificates With Cloudflare DNS
To issue wildcard certificates (e.g., `*.example.com`) or avoid exposing port 80, you can use DNS challenges via Cloudflare.

1. Set the `CLOUDFLARE_API_KEY*` environment variables on `nginx-proxy`. `nginx-proxy` supports multiple api-keys and will discover available domains.
2. Set `VIRTUAL_HOST` to a wildcard domain (e.g., `*.example.com`) or just use it for regular domains to use DNS validation.

**Example:**
```bash
docker run -d \
    -e CLOUDFLARE_API_KEY_KEY1="your-cloudflare-api-token" \
    ...
    mesudip/nginx-proxy
```

### Custom Certificates
Mount your own certificates:
- Directory: `/etc/nginx/ssl/certs`
- Naming: `example.com.crt` and `example.com.key`

Wildcard support: `*.example.com.crt` will match `sub.example.com`.

### Manual Certificate Management
Use the `getssl` command inside the container:
```bash
docker exec nginx-proxy getssl example.com
```

## Basic Authorization
Protect your services with HTTP Basic Auth.
Set `PROXY_BASIC_AUTH` environment variable.

- **Per-Host:** `example.com -> user:password`
- **Per-Path:** `example.com/admin -> user:password`

**Note:** Passwords should not be base64 encoded; the proxy handles hashing.

## Default Server
Requests with an unknown `Host` header return **503 Service Unavailable**.
Control this with the `DEFAULT_HOST` variable on the `nginx-proxy` container:
- `true` (default): Returns 503.
- `false`: Passes default requests to the first configured server.

To route unknown traffic to a specific container:
```bash
-e "VIRTUAL_HOST=fallback.example.com" \
-e "PROXY_DEFAULT_SERVER=true"
```

## Manual Certificate Commands
```
docker exec nginx-proxy verify www.example.com ## check if request routes back to this server.

docker exec nginx-proxy getssl www.example.com example.com www2.example.com # issue certificate

```

## 🚀 Roadmap
We are constantly improving `nginx-proxy` to make it the most robust and versatile zero-config reverse proxy solution. Here’s what’s coming next:

- **High Availability & Multi-Node Swarm Deployment:** Comprehensive guides and templates for deploying `nginx-proxy` in a multi-node Swarm environment for maximum uptime.
- **100% Test Coverage:** Ensuring rock-solid stability and reliability for every release.
- **Remote Swarm Cluster Support:** Monitor and route traffic for Swarm clusters running on remote nodes seamlessly.
- **Pangolin Support:** Integration with Pangolin as an alternative reverse-proxy engine.