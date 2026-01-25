import docker
import docker.models
import docker.models.containers
import pytest
import requests
import websocket
import time
from ..helpers import start_backend  # Import helper


@pytest.mark.parametrize(
    "virtual_host, backend_path, request_path",
    [
        ("http.example.com", "/", "/"),
        ("http.example.com/app", "/app", "/app"),
        ("http.example.com/api -> /backend_api", "/backend_api", "/api"),
        ("http.example.com; client_max_body_size 2m", "/", "/"),
    ],
)
def test_http_routing(nginx_request, docker_client, test_network, virtual_host, backend_path, request_path):
    """
    Test HTTP routing for various VIRTUAL_HOST configurations.
    """
    env = {"VIRTUAL_HOST": "http://" + virtual_host}
    backend: docker.models.containers.Container = None
    try:
        backend = start_backend(docker_client, test_network, env)

        # The nginx_request fixture will handle the Host header automatically
        # We need to construct the full URL that the NginxRequest class will parse for the host.
        # The path will be appended to the base_url_http by NginxRequest.get()
        request_url = f"http://http.example.com{request_path}"

        print(f"\nTesting HTTP: VIRTUAL_HOST='{virtual_host}', URL='{request_url}'")

        time.sleep(5)  # Give Nginx a moment to process

        response = nginx_request.get(request_url, timeout=5)
        assert response.status_code == 200
        assert response.text == f"Hello from {backend_path}"
        print(f"HTTP Test Passed: Received '{response.text}' as expected.")
    finally:
        if backend:
            print(f"Stopping and removing backend container {backend.name}...")
            backend.remove(force=True)


@pytest.mark.parametrize(
    "virtual_host, request_path",
    [
        ("ws.example.com", "/"),
        ("ws.example.com/chat", "/chat"),
        ("ws.example.com/ws_path -> /backend_ws", "/ws_path"),
    ],
)
def test_websocket_routing(nginx_request, docker_client, test_network, virtual_host, request_path):
    """
    Test WebSocket routing for various VIRTUAL_HOST configurations.
    """
    env = {"VIRTUAL_HOST": "ws://" + virtual_host}
    backend = None
    ws = None
    try:
        backend = start_backend(docker_client, test_network, env)

        # The nginx_request fixture will handle the Host header automatically
        # We need to construct the full URL that the NginxRequest class will parse for the host.
        full_url_for_host_parsing = (
            f"ws://{virtual_host.split(' ')[0].split(';')[0].split('->')[0].strip()}{request_path}"
        )

        print(f"\nTesting WebSocket: VIRTUAL_HOST='{virtual_host}', WS_URL='{full_url_for_host_parsing}'")

        time.sleep(5)  # Give Nginx time to reload
        ws = nginx_request.websocket_connect(full_url_for_host_parsing, timeout=5)
        message = "Hello WebSocket!"
        ws.send(message)
        result = ws.recv()

        assert result == f"Server received from client: {message}"
        print(f"WebSocket Test Passed: Received '{result}' as expected.")
    except Exception as e:
        pytest.fail(f"WebSocket test failed: {e}")
    finally:
        if ws:
            ws.close()
        if backend:
            print(f"Stopping and removing backend container {backend.name}...")
            backend.stop()
            backend.remove()
