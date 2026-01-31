import docker
import docker.models
import docker.models.containers
import pytest
import requests
import websocket
import time

from tests.helpers.integration_helpers import expect_server_up_integration
from ..helpers import start_backend, stop_backend  # Import helpers


def is_reachable(swarm_mode, backend_type):
    """
    Determines if a backend should be reachable based on swarm mode and backend type.
    """
    if swarm_mode  in ("enable", "ignore"):
        return True
    if swarm_mode == "strict" and backend_type == "service":
        return True
    if swarm_mode == "exclude" and backend_type == "container":
        return True
    return False


def get_request_url(virtual_host, request_path, scheme="http"):
    """
    Extracts the hostname from virtual_host and constructs a request URL.
    """
    # Host part is before '->' and before ';' and before '/'
    host_part = virtual_host.split("->")[0].split(";")[0].strip()
    hostname = host_part.split("/")[0]
    return f"{scheme}://{hostname}{request_path}"


@pytest.mark.parametrize(
    "virtual_host_path, request_path, container_received_path",
    [
        ("", "/", "/"),
        ("/", "", "/"),
        ("/ -> /", "", "/"),
        (" -> /", "", "/"),
        ("/api ", "/api", "/api"), 
        ("/api/ ", "/api", "/api/"), # This is weird case. 
        ("/api/ -> /", "/api", "/"),
        ("/api/ -> /internal", "/api/test", "/internaltest"),
        ("/api/ -> /internal/", "/api/test", "/internal/test"),
        ("/api -> /internal", "/api/test", "/internal/test"),
        ("/api -> /internal/", "/api/test", "/internal/test"),
        ("/api", "/api/test", "/api/test"),
    ],
)
@pytest.mark.parametrize("backend_type", ["container", "service"])
def test_http_routing_discovery(
    nginx_request, docker_client, test_network, virtual_host_path, request_path, container_received_path, swarm_mode, backend_type,request
):
    """
    Test HTTP routing discovery for various swarm modes and backend types.
    """
    hostname = f"{backend_type}.{swarm_mode}.routing.example.com"
    should_be_reachable = is_reachable(swarm_mode, backend_type)

    env = {"VIRTUAL_HOST": hostname+virtual_host_path ,"VIRTUAL_PORT": "8080"}
    backend = None
    try:
        backend = start_backend(docker_client, test_network, env, backend_type=backend_type,pytest_request=request,sleep=False)

        url = "http://" + hostname+request_path
        print(f"\nTesting {swarm_mode} with {backend_type}: URL='{url}', expected={should_be_reachable}")

        # Retrying for async discovery
        response = None
        ex=None
        for x in range(15):
            try:
                ex=None
                response = nginx_request.get(url, timeout=2)
                if should_be_reachable and response.status_code == 200:
                    break
                if not should_be_reachable and response.status_code == 503:
                    break
            except SystemExit or KeyboardInterrupt:
                raise
            except Exception as e:
                ex=e
                print(x,e)

            time.sleep(1)
        
        assert ex is None
        assert response is not None

        if should_be_reachable:
            assert response.status_code == 200
            assert response.text == f"Hello from {container_received_path}"
        else:
            # When ignored, Nginx should return 503 (default server or our generated 503 server)
            assert response.status_code == 503

    finally:
        if backend:
            print(f"Stopping and removing backend {backend_type}...")
            stop_backend(backend)

@pytest.mark.parametrize("backend_type", ["container", "service"])
@pytest.mark.parametrize(
    "virtual_host_base, request_path",
    [
        ("ws.example.com", "/"),
        ("ws.example.com/chat", "/chat"),
        ("ws.example.com/ws_path", "/ws_path"),
    ],
)
def test_websocket_routing(nginx_request, docker_client, test_network, virtual_host_base, request_path, swarm_mode, backend_type):
    """
    Test WebSocket routing for various VIRTUAL_HOST configurations.
    """
    virtual_host = f"{backend_type}.{swarm_mode}.{virtual_host_base} -> :8080"
    should_be_reachable = is_reachable(swarm_mode, backend_type)

    env = {"VIRTUAL_HOST": "ws://" + virtual_host}
    backend = None
    ws = None
    try:
        backend = start_backend(docker_client, test_network, env, backend_type=backend_type)

        # The nginx_request fixture will handle the Host header automatically
        # We need to construct the full URL that the NginxRequest class will parse for the host.
        full_url_for_host_parsing = get_request_url(virtual_host, request_path, scheme="ws")

        print(f"\nTesting WebSocket {swarm_mode} with {backend_type}: VIRTUAL_HOST='{virtual_host}', WS_URL='{full_url_for_host_parsing}', expected={should_be_reachable}")

        # Retrying for async discovery
        connected = False
        for i in range(15):
            try:
                ws = nginx_request.websocket_connect(full_url_for_host_parsing, timeout=5)
                connected = True
                break
            except Exception:
                if not should_be_reachable:
                    # Connection failed as expected (e.g. 503/404)
                    break 
                time.sleep(1)

        if should_be_reachable:
            assert connected is True, "WebSocket failed to connect when it should be reachable"
            message = "Hello WebSocket!"
            ws.send(message)
            result = ws.recv()

            assert result == f"Server received from client: {message}"
            print(f"WebSocket Test Passed: Received '{result}' as expected.")
        else:
            assert connected is False, "WebSocket connected when it should have been ignored"

    except Exception as e:
        if should_be_reachable:
            pytest.fail(f"WebSocket test failed: {e}")
    finally:
        if ws:
            ws.close()
        if backend:
            print(f"Stopping and removing backend...")
            stop_backend(backend)
