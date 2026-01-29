import pytest
import docker
import time
import requests
import websocket
import os
import re
from urllib.parse import urlparse, urljoin
import socket
from dotenv import load_dotenv

from tests.helpers.docker_test_client import DockerTestClient

# Load environment variables from .env file
load_dotenv()


@pytest.fixture(scope="session")
def docker_host_ip():
    docker_host = os.environ.get("DOCKER_HOST")

    # Regex to match both tcp://hostname:port, unix://socket, or ip:port
    regex = r"^(?:(tcp|unix)://)?([a-zA-Z0-9.-]+)(?::\d+)?$"

    if docker_host:
        match = re.match(regex, docker_host)
        if match:
            protocol, host = match.groups()
            if protocol == "unix":
                return "unix"
            return host  # Return the IP or hostname
    return "localhost"


@pytest.fixture(scope="session")
def docker_client():
    client: docker.DockerClient = docker.from_env()

    # Check if Swarm is active, if not try to init
    try:
        info = client.info()
        if info.get("Swarm", {}).get("LocalNodeState") != "active":
            print("Swarm not active. Initializing...")
            client.swarm.init(advertise_addr="127.0.0.1")
            print("Swarm initialized.")
    except Exception as e:
        print(f"Warning: Failed to ensure Swarm state: {e}")

    yield client
    client.close()


@pytest.fixture(scope="session")
def test_network(docker_client: docker.DockerClient,swarm_mode):
    network_name = "nginx-proxy-test-" + swarm_mode
    server_details = docker_client.info()
    is_swarm = server_details.get("Swarm", {}).get("LocalNodeState") == "active"

    driver = "overlay" if is_swarm else "bridge"
    attachable = is_swarm  # overlay needs attachable for standalone containers

    try:
        network = docker_client.networks.get(network_name)
    except docker.errors.NotFound:
        network = docker_client.networks.create(network_name, driver=driver, attachable=attachable)
    yield network
    print(f"Waiting a moment before removing network {network_name}...")
    time.sleep(2)  # Give Docker time to clean up endpoints
    try:
        network.reload()  # Reload network to get updated container list
        for container in network.containers:
            print(f"Stopping and removing container {container.name} from network {network_name}...")
            try:
                container.remove(force=True)
                print(f"Container {container.name} stopped and removed.")
            except docker.errors.APIError as container_e:
                print(f"Error stopping/removing container {container.name}: {container_e}")
        network.remove()
        print(f"Network {network_name} removed successfully.")
    except docker.errors.APIError as e:
        print(f"Error removing network {network_name}: {e}")


@pytest.fixture(scope="session", params=["ignore", "exclude", "enable", "strict"], ids=["swarm_ignore", "swarm_exclude", "swarm_enable", "swarm_strict"])
def swarm_mode(request):
    return request.param


@pytest.fixture(scope="session")
def nginx_proxy_container(docker_client: docker.DockerClient, test_network, docker_host_ip, swarm_mode):
    image_name = "mesudip/nginx-proxy:test"
    container_name = "nginx-proxy-test-container-swarm_"+swarm_mode

    # Ensure previous container is stopped and removed
    try:
        existing_container = docker_client.containers.get(container_name)
        print(f"Found existing container '{container_name}'. Stopping and removing...")
        existing_container.stop()
        existing_container.remove()
        print(f"Removed existing container '{container_name}'.")
    except docker.errors.NotFound:
        print(f"No existing container '{container_name}' found. Proceeding.")
    except Exception as e:
        print(f"Error cleaning up existing container: {e}")
        # Do not raise, try to proceed with build/run

    print(f"\nBuilding {image_name}...")
    try:
        docker_client.images.build(path=".", tag=image_name, rm=True)
        print(f"Successfully built {image_name}")
    except docker.errors.BuildError as e:
        print(f"Docker build failed: {e}")
        raise

    print(f"Starting {image_name} container...")
    container = None
    try:
        container = docker_client.containers.run(
            image_name,
            detach=True,
            ports={"80/tcp": None, "443/tcp": None},  # Let Docker assign random ports
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "ro"},
                "nginx-test-dhparam": {"bind": "/etc/nginx/dhparam", "mode": "rw"},
                "nginx-test-ssl": {"bind": "/etc/ssl", "mode": "rw"},
            },
            network=test_network.name,
            name=container_name,
            environment={
                "LETSENCRYPT_API": "https://acme-staging-v02.api.letsencrypt.org/directory",
                "DHPARAM_SIZE": "256",
                "VHOSTS_TEMPLATE_DIR": "/app/vhosts_template",
                "CHALLENGE_DIR": "/etc/nginx/acme-challenges",
                "DOCKER_SWARM": swarm_mode,
            },
            restart_policy={"Name": "no"},
        )

        # Get the dynamically assigned ports
        time.sleep(1)
        container.reload()
        port_80 = container.ports["80/tcp"][0]["HostPort"]
        port_443 = container.ports["443/tcp"][0]["HostPort"]

        print(f"nginx-proxy running on host ports: HTTP={port_80}, HTTPS={port_443}")

        # Wait for nginx-proxy to be ready
        ready = False
        for i in range(120):  # wait up to 120 seconds (2 minutes)
            try:
                # Use localhost for health check as it's from within the test runner's perspective
                response = requests.get(
                    f"http://{docker_host_ip}:{port_80}", headers={"Host": "nonexistent.example.com"}, timeout=1
                )
                if response.status_code == 503:  # Default 503 response means nginx is up
                    print(f"nginx-proxy is ready after {i+1} seconds.")
                    ready = True
                    break
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(1)

        if not ready:
            print("\nnginx-proxy did not become ready in time. Container logs:")
            if container:
                print(container.logs().decode("utf-8"))
            raise RuntimeError("nginx-proxy did not become ready in time.")

        yield container, port_80, port_443  # Yield container and ports
    finally:
        if container:
            print("Stopping and removing nginx-proxy-test-container...")
            print("=========================== Container Logs Start ===========================")
            print(container.logs().decode("utf-8"))
            print("=========================== Container Logs End ===========================")
            container.stop()
            container.remove()


class NginxRequest(requests.Session):

    def __init__(self, base_url_http: str, base_url_https: str):
        super().__init__()
        self.verify = False  # Disable SSL verification for testing
        self.base_url_http = base_url_http
        self.base_url_https = base_url_https

    def request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", {})
        allow_redirects = kwargs.pop("allow_redirects", True)
        max_redirects = kwargs.pop("max_redirects", 10)
        if not allow_redirects:
            max_redirects = 0

        current_url = url
        method = method.upper()
        redirects = 0

        while True:
            parsed = urlparse(current_url)
            scheme = parsed.scheme
            base = self.base_url_https if scheme == "https" else self.base_url_http
            headers["Host"] = parsed.netloc  # Includes port if present

            full_path = parsed.path or "/"
            if parsed.query:
                full_path += "?" + parsed.query
            if parsed.fragment:
                full_path += "#" + parsed.fragment
            target = urljoin(base, full_path)

            if "verify" not in kwargs and scheme == "https":
                kwargs["verify"] = False

            # Make a copy of headers to avoid mutating the original
            request_headers = dict(headers)

            resp = super().request(method, target, headers=request_headers, allow_redirects=False, **kwargs)

            if not (300 <= resp.status_code < 400) or "Location" not in resp.headers:
                return resp

            redirects += 1
            if redirects > max_redirects:
                return resp

            location = resp.headers["Location"]
            current_url = urljoin(current_url, location)

            if resp.status_code == 303 and method not in ("GET", "HEAD"):
                method = "GET"
                kwargs.pop("data", None)
                kwargs.pop("json", None)

    def websocket_connect(self, url: str, **kwargs):
        parsed_url = urlparse(url)
        if parsed_url.scheme not in ("ws", "wss"):
            raise ValueError("URL scheme must be 'ws' or 'wss'")

        base = self.base_url_https if parsed_url.scheme == "wss" else self.base_url_http
        parsed_base = urlparse(base)
        target_host = parsed_base.hostname
        target_port = parsed_base.port or (443 if parsed_url.scheme == "wss" else 80)

        sock = socket.create_connection((target_host, target_port))

        ws = websocket.WebSocket()
        try:
            ws.connect(url, socket=sock, **kwargs)
        except TypeError:
            ws.connect(url, sock=sock, **kwargs)

        return ws

@pytest.fixture
def nginx_request(nginx_proxy_container, docker_host_ip):
    _, port_80, port_443 = nginx_proxy_container
    base_url_http = f"http://{docker_host_ip}:{port_80}"
    base_url_https = f"https://{docker_host_ip}:{port_443}"
    return NginxRequest(base_url_http, base_url_https)
