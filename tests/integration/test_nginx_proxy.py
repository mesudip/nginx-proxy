import docker
import docker.models
import docker.models.containers
import pytest
import requests
import websocket
import time
from ..helpers import start_backend_container # Import helper

@pytest.fixture(scope="module")
def nginx_proxy_url(nginx_proxy_container, docker_host_ip):
    _, port_80, _ = nginx_proxy_container
    return f"http://{docker_host_ip}:{port_80}"

@pytest.mark.parametrize("virtual_host, expected_path, request_path", [
    ("http.example.com", "/", "/"),
    ("http.example.com/app", "/app", "/app"),
    ("http.example.com/api -> /backend_api", "/backend_api", "/api"),
    ("http.example.com; client_max_body_size 2m", "/", "/"),
])
def test_http_routing(nginx_proxy_url, docker_client, test_network, virtual_host, expected_path, request_path):
    """
    Test HTTP routing for various VIRTUAL_HOST configurations.
    """
    env = {"VIRTUAL_HOST": virtual_host}
    backend:docker.models.containers.Container  = None
    try:
        backend  = start_backend_container(docker_client, test_network, env)
        
        host_header = virtual_host.split(' ')[0].split(';')[0].split('->')[0].strip()
        if '/' in host_header:
            host_header = host_header.split('/')[0]

        url = f"{nginx_proxy_url}{request_path}"
        headers = {"Host": host_header}

        print(f"\nTesting HTTP: VIRTUAL_HOST='{virtual_host}', URL='{url}', Host='{host_header}'")
        
        response = requests.get(url, headers=headers, timeout=5)
        
        assert response.status_code == 200
        assert response.text == f"Hello from {expected_path}"
        print(f"HTTP Test Passed: Received '{response.text}' as expected.")
    finally:
        if backend:
            print(f"Stopping and removing backend container {backend.name}...")
            backend.remove(force=True)

@pytest.mark.parametrize("virtual_host, request_path", [
    ("ws.example.com", "/"),
    ("ws.example.com/chat", "/chat"),
    ("ws.example.com/ws_path -> /backend_ws", "/ws_path"),
])
def test_websocket_routing(nginx_proxy_container, docker_client, test_network, docker_host_ip, virtual_host, request_path):
    """
    Test WebSocket routing for various VIRTUAL_HOST configurations.
    """
    _, port_80, _ = nginx_proxy_container # Get the dynamically assigned HTTP port
    env = {"VIRTUAL_HOST": virtual_host}
    backend = None
    ws = None
    try:
        backend = start_backend_container(docker_client, test_network, env)

        host_header = virtual_host.split(' ')[0].split(';')[0].split('->')[0].strip()
        if '/' in host_header:
            host_header = host_header.split('/')[0]

        ws_url = f"ws://{docker_host_ip}:{port_80}{request_path}" # Use the dynamically assigned port and host IP
        
        print(f"\nTesting WebSocket: VIRTUAL_HOST='{virtual_host}', WS_URL='{ws_url}', Host='{host_header}'")

        ws = websocket.create_connection(ws_url, header={"Host": host_header}, timeout=5)
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
