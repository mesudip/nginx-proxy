import pytest
import docker
import time
import requests
import websocket
import os
import re
from urllib.parse import urlparse

@pytest.fixture(scope="session")
def docker_host_ip():
    docker_host = os.environ.get("DOCKER_HOST")
    
    # Regex to match both tcp://hostname:port, unix://socket, or ip:port
    regex = r'^(?:(tcp|unix)://)?([a-zA-Z0-9.-]+)(?::\d+)?$'
    
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
    client : docker.DockerClient = docker.from_env()
    yield client
    client.close()

@pytest.fixture(scope="session")
def test_network(docker_client):
    network_name = "test-frontend"
    try:
        network = docker_client.networks.get(network_name)
    except docker.errors.NotFound:
        network = docker_client.networks.create(network_name, driver="bridge")
    yield network
    print(f"Waiting a moment before removing network {network_name}...")
    time.sleep(2) # Give Docker time to clean up endpoints
    try:
        network.remove()
        print(f"Network {network_name} removed successfully.")
    except docker.errors.APIError as e:
        print(f"Error removing network {network_name}: {e}")
        # Optionally, try to disconnect and remove containers if the error persists
        # This might involve listing containers on the network and forcing their removal

@pytest.fixture(scope="session")
def nginx_proxy_container(docker_client, test_network):
    image_name = "mesudip/nginx-proxy:test"
    container_name = "nginx-proxy-test-container"

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
            ports={'80/tcp': None, '443/tcp': None}, # Let Docker assign random ports
            volumes={'/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'ro'}},
            network=test_network.name,
            name=container_name,
            restart_policy={"Name": "no"}
        )
        
        # Get the dynamically assigned ports
        container.reload() # Reload container info to get updated port mappings
        port_80 = container.ports['80/tcp'][0]['HostPort']
        port_443 = container.ports['443/tcp'][0]['HostPort']
        
        print(f"nginx-proxy running on host ports: HTTP={port_80}, HTTPS={port_443}")

        # Wait for nginx-proxy to be ready
        ready = False
        for i in range(120): # wait up to 120 seconds (2 minutes)
            try:
                # Use localhost for health check as it's from within the test runner's perspective
                response = requests.get(f"http://localhost:{port_80}", headers={"Host": "nonexistent.example.com"}, timeout=1)
                if response.status_code == 503: # Default 503 response means nginx is up
                    print(f"nginx-proxy is ready after {i+1} seconds.")
                    ready = True
                    break
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(1)
        
        if not ready:
            print("\nnginx-proxy did not become ready in time. Container logs:")
            if container:
                print(container.logs().decode('utf-8'))
            raise RuntimeError("nginx-proxy did not become ready in time.")
        
        yield container, port_80, port_443 # Yield container and ports
    finally:
        if container:
            print("Stopping and removing nginx-proxy-test-container...")
            container.stop()
            container.remove()
