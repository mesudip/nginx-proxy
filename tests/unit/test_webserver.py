import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from typing import List

from nginx.NginxConf import HttpBlock, NginxConfig
from nginx_proxy.WebServer import WebServer
from nginx.DummyNginx import DummyNginx
from nginx.NginxChallengeSolver import NginxChallengeSolver
from nginx_proxy.Container import Container
from nginx_proxy.Host import Host
from nginx_proxy.Location import Location
from nginx_proxy.ProxyConfigData import ProxyConfigData
from nginx_proxy.SSL import SSL
import os

from tests.helpers.docker_test_client import DockerTestClient, MockContainer, MockNetwork
from nginx_proxy.DockerEventListener import DockerEventListener

@pytest.fixture(scope="session")
def nginx(webserver:WebServer):
    return webserver.nginx
@pytest.fixture(scope="session")
def docker_client():
    return DockerTestClient()

@pytest.fixture(scope="session")
def webserver(docker_client:DockerTestClient):
    yield create_webserver(docker_client)
def create_webserver(docker_client: DockerTestClient):
    # Initialize DockerTestClient
    docker_client.networks.create("frontend")  # Default network
    os.environ["LETSENCRYPT_API"] = "https://acme-staging-v02.api.letsencrypt.org/directory"

    # Patch WebServer's loadconfig to use dummy nginx and specific paths
    with patch('nginx_proxy.WebServer.WebServer.loadconfig') as mock_loadconfig:
        mock_loadconfig.return_value = {
            "dummy_nginx": True,
            "ssl_dir": "./.run_data",
            "conf_dir": "./run_data",
            "client_max_body_size": "1m",
            "challenge_dir": "./.run_data/acme-challenges/",
            "default_server": True,
            "vhosts_template_dir" : "./vhosts_template",
        }
        # Initialize WebServer
        webserver = WebServer(docker_client,10)

        # Start DockerEventListener in a background thread to process events
        listener = DockerEventListener(webserver, docker_client)
        listener_thread = threading.Thread(target=listener.run, daemon=True)
        listener_thread.start()
        # Yield components for testing
        return webserver


@pytest.fixture(scope="function")
def webserver_instance(webserver: WebServer, nginx: DummyNginx, docker_client: DockerTestClient):
    """Provides webserver, dummy_nginx, and mock_docker_client for tests that need them."""
    yield webserver, nginx, docker_client
    # Cleanup: remove all containers and networks created during the test
    for container in docker_client.containers.list():
        container.remove()
    for network in docker_client.networks.list():
        if network.name != "frontend": # Don't remove the default frontend network
            network.remove()
    # Ensure the webserver's internal state is reset if necessary, though the listener should handle this.
    # For a dummy nginx, clearing its config might be appropriate.
    nginx.clear_config()



def test_webserver_initialization(webserver:WebServer,nginx:DummyNginx):
    assert isinstance(webserver.ssl_processor.ssl.challenge_store, NginxChallengeSolver)
    # Check initial default server block
    config = NginxConfig()
    full_config_str = f"http {{\n{nginx.current_config}\n}}"
    config.load(full_config_str)

    assert len(config.http.servers) == 1
    server = config.http.servers[0]
    assert server.listen == "80 default_server"
    assert server.server_names == ["_"]
    assert server.error_page == "503 /503_default.html"

    assert len(server.locations) == 3
    loc0 = server.locations[0]
    assert loc0.path == "/.well-known/acme-challenge/"
    assert loc0.alias == "/tmp/acme-challenges/"
    assert loc0.try_files == "$uri =404"

    loc1 = server.locations[1]
    assert loc1.path == "= /503_default.html"
    assert loc1.root == "/tmp/vhosts_template/errors"
    assert loc1.internal == True

    loc2 = server.locations[2]
    assert loc2.path == "/"
    assert loc2.return_code == "503"

def test_webserver_add_container(webserver:WebServer,docker_client:DockerTestClient,nginx:DummyNginx):

    container_name = "test_container"
    labels = {
        "nginx.port": "8000" # Keep nginx.port for proxy_pass target
    }
    env={
        "VIRTUAL_HOST": "example.com"
    }
    network_names =["frontend"]
    container=docker_client.containers.run( "nginx:alpine",name=container_name, environment=env, labels=labels, network="frontend")

    # Create container, triggering create and start events
    print("Test container started waiting for 5 secs")
    # Allow listener time to process events
    time.sleep(0.1)  # Small delay for async event processing

    config = HttpBlock.parse(nginx.current_config)

    # Find the server block for example.com
    
    example_server = config.servers[0]
    for server in config.servers:
        if "example.com" in server.server_names:
            example_server = server
            break
    assert example_server is not None, "Server for example.com not found"

    assert example_server.server_names == ["example.com"]
    assert example_server.includes == ["./run_data/error.conf"]
    assert example_server.listen == "443"

    assert len(example_server.locations) == 1
    location = example_server.locations[0]
    assert location.path == "/"
    # Extract the IP address from the container's network settings
    container_ip = container.get_ip_address("frontend")
    assert location.proxy_pass == f"http://{container_ip}:8000"

def test_webserver_remove_container(webserver_instance):
    webserver, dummy_nginx, docker_client = webserver_instance

    container_name = "test_container"
    labels = {
        "nginx.port": "8000"
    }
    env = {
        "VIRTUAL_HOST": "example.com"
    }
    network_names =["frontend"]

    # Add container using docker_client.containers.run
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, labels=labels, network=network_names[0])
    time.sleep(0.1)  # Allow event processing

    # Verify addition
    expected_config_part = "server_name example.com;"
    assert expected_config_part in dummy_nginx.current_config

    # Remove container, triggering destroy event
    container.remove()
    time.sleep(0.1)  # Allow event processing

    # Verify removal
    config = NginxConfig()
    full_config_str = f"http {{\n{dummy_nginx.current_config}\n}}"
    config.load(full_config_str)

    # Assert that the example.com server is no longer present
    example_server_found = False
    for server in config.http.servers:
        if "example.com" in server.server_names:
            example_server_found = True
            break
    assert not example_server_found, "Server for example.com should have been removed"

    # Assert that the default server block is still present and correctly configured
    assert len(config.http.servers) == 1
    default_server = config.http.servers[0]
    assert default_server.listen == "80 default_server"
    assert default_server.server_names == ["_"]
    assert default_server.error_page == "503 /503_default.html"

    assert len(default_server.locations) == 3
    loc0 = default_server.locations[0]
    assert loc0.path == "/.well-known/acme-challenge/"
    assert loc0.alias == "/tmp/acme-challenges/"
    assert loc0.try_files == "$uri =404"

    loc1 = default_server.locations[1]
    assert loc1.path == "= /503_default.html"
    assert loc1.root == "/tmp/vhosts_template/errors"
    assert loc1.internal == True

    loc2 = default_server.locations[2]
    assert loc2.path == "/"
    assert loc2.return_code == "503"

def test_webserver_add_network(webserver_instance):
    webserver, dummy_nginx, docker_client = webserver_instance

    container_name = "test_container"
    address_new_network = "172.18.0.3" # This address is assigned by the mock client
    labels = {
        "nginx.port": "8000"
    }
    env = {
        "VIRTUAL_HOST": "example.com"
    }
    network_names_initial =["frontend"]

    # Create container on bridge using docker_client.containers.run
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, labels=labels, network=network_names_initial[0])
    time.sleep(0.1)  # Allow event processing

    # Add new network
    net = docker_client.networks.create("new_network")
    net.connect(container.id, ipv4_address=address_new_network)
    time.sleep(0.1)  # Allow connect event processing

    config = NginxConfig()
    full_config_str = f"http {{\n{dummy_nginx.current_config}\n}}"
    config.load(full_config_str)

    # Find the server block for example.com
    example_server = None
    for server in config.http.servers:
        if "example.com" in server.server_names:
            example_server = server
            break
    assert example_server is not None, "Server for example.com not found after adding network"

    assert example_server.listen == "80"
    assert example_server.server_names == ["example.com"]

    assert len(example_server.locations) == 1
    location = example_server.locations[0]
    assert location.path == "/"
    assert location.proxy_pass == f"http://{address_new_network}:8000"

def test_webserver_remove_network(webserver_instance):
    webserver, dummy_nginx, docker_client = webserver_instance

    container_name = "test_container"
    address_bridge = "172.17.0.2" # This address is assigned by the mock client
    address_new_network = "172.18.0.3" # This address is assigned by the mock client
    labels = {
        "nginx.port": "8000"
    }
    env = {
        "VIRTUAL_HOST": "example.com"
    }
    network_names_initial = ["bridge", "another_network"]

    # Create container with multiple networks using docker_client.containers.run
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, labels=labels, network=network_names_initial[0])
    # Connect to the second network manually
    net_another = docker_client.networks.create("another_network")
    net_another.connect(container.id, ipv4_address=address_new_network)
    time.sleep(0.1)  # Allow event processing

    # Disconnect from another_network
    net_another.disconnect(container.id)
    time.sleep(0.1)  # Allow disconnect event processing

    config = NginxConfig()
    full_config_str = f"http {{\n{dummy_nginx.current_config}\n}}"
    config.load(full_config_str)

    # Find the server block for example.com
    example_server = None
    for server in config.http.servers:
        if "example.com" in server.server_names:
            example_server = server
            break
    assert example_server is not None, "Server for example.com not found after removing network"

    assert example_server.listen == "80"
    assert example_server.server_names == ["example.com"]

    assert len(example_server.locations) == 1
    location = example_server.locations[0]
    assert location.path == "/"
    assert location.proxy_pass == f"http://{address_bridge}:8000"

    # Clean up by removing container
    container.remove()
    time.sleep(0.1)  # Allow event processing

    # Verify that only the default server remains
    full_config_str = f"http {{\n{dummy_nginx.current_config}\n}}"
    config.load(full_config_str) # Reload config after container removal
    assert len(config.http.servers) == 1
    default_server = config.http.servers[0]
    assert default_server.listen == "80 default_server"
    assert default_server.server_names == ["_"]
    assert default_server.error_page == "503 /503_default.html"

    assert len(default_server.locations) == 3
    loc0 = default_server.locations[0]
    assert loc0.path == "/.well-known/acme-challenge/"
    assert loc0.alias == "/tmp/acme-challenges/"
    assert loc0.try_files == "$uri =404"

    loc1 = default_server.locations[1]
    assert loc1.path == "= /503_default.html"
    assert loc1.root == "/tmp/vhosts_template/errors"
    assert loc1.internal == True

    loc2 = default_server.locations[2]
    assert loc2.path == "/"
    assert loc2.return_code == "503"

def test_webserver_update_container_labels(webserver_instance):
    webserver, dummy_nginx, docker_client = webserver_instance

    container_name = "test_container"
    labels_old = {
        "nginx.port": "8000"
    }
    env_old = {
        "VIRTUAL_HOST": "old.example.com"
    }
    network_names =["frontend"]

    # Create with old labels
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env_old, labels=labels_old, network=network_names[0])
    time.sleep(0.1)  # Allow event processing

    config = NginxConfig()
    config.load(dummy_nginx.current_config)

    # Verify old server name exists
    old_server_found = False
    for server in config.http.servers:
        if "old.example.com" in server.server_names:
            old_server_found = True
            break
    assert old_server_found, "Server for old.example.com not found initially"

    # Remove the old container first to allow creating a new one with the same name
    container.remove()
    time.sleep(0.1) # Allow event processing for removal

    # Create new container with updated labels, same name
    labels_new = {
        "nginx.port": "8000"
    }
    env_new = {
        "VIRTUAL_HOST": "new.example.com"
    }
    container_new = docker_client.containers.run("nginx:alpine", name=container_name, environment=env_new, labels=labels_new, network=network_names[0])
    time.sleep(0.1)  # Allow event processing

    config.load(dummy_nginx.current_config) # Reload config after update

    # Verify old server name is gone
    old_server_found_after_update = False
    for server in config.http.servers:
        if "old.example.com" in server.server_names:
            old_server_found_after_update = True
            break
    assert not old_server_found_after_update, "Server for old.example.com should have been removed after update"

    # Verify new server name exists
    new_server_found = False
    for server in config.http.servers:
        if "new.example.com" in server.server_names:
            new_server_found = True
            break
    assert new_server_found, "Server for new.example.com not found after update"

def test_webserver_add_container_with_ssl(webserver_instance):
    webserver, dummy_nginx, docker_client = webserver_instance

    container_name = "ssl_container"
    labels = {
        "nginx.port": "443",
        "nginx.ssl": "true"
    }
    env = {
        "VIRTUAL_HOST": "ssl.example.com"
    }
    network_names =["frontend"]

    # Create container with SSL labels using docker_client.containers.run
    docker_client.containers.run("nginx:alpine", name=container_name, environment=env, labels=labels, network=network_names[0])
    time.sleep(0.1)  # Allow event processing

    config = NginxConfig()
    config.load(dummy_nginx.current_config)

    # Find the HTTP server block for ssl.example.com
    http_server = None
    for server in config.http.servers:
        if "ssl.example.com" in server.server_names and server.listen == "80":
            http_server = server
            break
    assert http_server is not None, "HTTP server for ssl.example.com not found"
    assert http_server.server_names == ["ssl.example.com"]
    assert http_server.return_code == "301 https://$host$request_uri"

    # Find the HTTPS server block for ssl.example.com
    https_server = None
    for server in config.http.servers:
        if "ssl.example.com" in server.server_names and server.listen == "443 ssl":
            https_server = server
            break
    assert https_server is not None, "HTTPS server for ssl.example.com not found"
    assert https_server.server_names == ["ssl.example.com"]
    assert https_server.ssl_certificate == "/etc/nginx/ssl/ssl.example.com.crt"
    assert https_server.ssl_certificate_key == "/etc/nginx/ssl/ssl.example.com.key"

    assert len(https_server.locations) == 1
    location = https_server.locations[0]
    assert location.path == "/"
    container_ip = docker_client.get_container_by_name(container_name).get_ip_address("frontend")
    assert location.proxy_pass == f"http://{container_ip}:443"
