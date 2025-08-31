import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from typing import List

from nginx.NginxConf import NginxConfig
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

def assert_config_part_in_nginx_config(expected_part: str, actual_config: str, message_prefix: str = "Expected config part not found"):
    """
    Helper function to assert if an expected Nginx config part is present in the actual config.
    If not, it fails the test with a detailed, readable message.
    """
    if expected_part.strip() not in actual_config.strip():
        pytest.fail(
            f"{message_prefix} in current Nginx config.\n\n"
            f"--- Expected Config Part ---\n{expected_part.strip()}\n\n"
            f"--- Current Nginx Config ---\n{actual_config.strip()}"
        )



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
        webserver = WebServer(docker_client=docker_client)

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
    expected_initial_config = """
server{
        listen 80 default_server;
        server_name _ ;
        location /.well-known/acme-challenge/ {
            alias /tmp/acme-challenges/;
            try_files $uri =404;
        }
        error_page 503 /503_default.html;

        location = /503_default.html {
            root /tmp/vhosts_template/errors;
            internal;
        }

        location / {
            return 503;
        }
}
"""
    actual_config = nginx.current_config
    assert_config_part_in_nginx_config(expected_initial_config, actual_config, "Expected initial config part not found")

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
    time.sleep(5)  # Small delay for async event processing

    config = NginxConfig()
    config.load(nginx.current_config)
    
    expected_config_part = """
server{
        server_name example.com;

        include ./run_data/error.conf;
        listen 443 ; 
        location / {
            proxy_pass http://172.18.0.2:8000;  # container: {container.id[:12]}
        }
}
"""
    
    assert_config_part_in_nginx_config(expected_config_part, nginx.current_config, "Expected container config part not found")

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
    time.sleep(5)  # Allow event processing

    # Verify addition
    expected_config_part = "server_name example.com;"
    assert expected_config_part in dummy_nginx.current_config

    # Remove container, triggering destroy event
    container.remove()
    time.sleep(5)  # Allow event processing

    # Verify removal
    assert expected_config_part not in dummy_nginx.current_config
    expected_default_config = """
server{
        listen 80 default_server;
        server_name _ ;
        location /.well-known/acme-challenge/ {
            alias /tmp/acme-challenges/;
            try_files $uri =404;
        }
        error_page 503 /503_default.html;

        location = /503_default.html {
            root /tmp/vhosts_template/errors;
            internal;
        }

        location / {
            return 503;
        }
}
"""
    actual_config = dummy_nginx.current_config
    assert_config_part_in_nginx_config(expected_default_config, actual_config, "Expected default config part not found after container removal")

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
    time.sleep(5)  # Allow event processing

    # Add new network
    net = docker_client.networks.create("new_network")
    net.connect(container.id, ipv4_address=address_new_network)
    time.sleep(5)  # Allow connect event processing

    expected_config_part = f"""
server {{
    listen 80;
    server_name example.com;

    location / {{
        proxy_pass http://{address_new_network}:8000;
    }}
}}
"""
    actual_config = dummy_nginx.current_config
    assert_config_part_in_nginx_config(expected_config_part, actual_config, "Expected config part after adding network not found")

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
    time.sleep(5)  # Allow event processing

    # Disconnect from another_network
    net_another.disconnect(container.id)
    time.sleep(5)  # Allow disconnect event processing

    expected_config_part = f"""
server {{
    listen 80;
    server_name example.com;

    location / {{
        proxy_pass http://{address_bridge}:8000;
    }}
}}
"""
    actual_config = dummy_nginx.current_config
    assert_config_part_in_nginx_config(expected_config_part, actual_config, "Expected config part after removing network not found")

    # Clean up by removing container
    container.remove()
    time.sleep(5)  # Allow event processing

    expected_default_config = """
server{
        listen 80 default_server;
        server_name _ ;
        location /.well-known/acme-challenge/ {
            alias /tmp/acme-challenges/;
            try_files $uri =404;
        }
        error_page 503 /503_default.html;

        location = /503_default.html {
            root /tmp/vhosts_template/errors;
            internal;
        }

        location / {
            return 503;
        }
}
"""
    actual_config = dummy_nginx.current_config
    assert_config_part_in_nginx_config(expected_default_config, actual_config, "Expected default config part not found after container removal and network changes")

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
    time.sleep(5)  # Allow event processing

    assert "server_name old.example.com;" in dummy_nginx.current_config

    # Remove the old container first to allow creating a new one with the same name
    container.remove()
    time.sleep(5) # Allow event processing for removal

    # Create new container with updated labels, same name
    labels_new = {
        "nginx.port": "8000"
    }
    env_new = {
        "VIRTUAL_HOST": "new.example.com"
    }
    container_new = docker_client.containers.run("nginx:alpine", name=container_name, environment=env_new, labels=labels_new, network=network_names[0])
    time.sleep(5)  # Allow event processing

    assert "server_name old.example.com;" not in dummy_nginx.current_config
    assert "server_name new.example.com;" in dummy_nginx.current_config

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
    time.sleep(5)  # Allow event processing

    expected_config_part_http = """
server {
    listen 80;
    server_name ssl.example.com;
    return 301 https://$host$request_uri;
}
"""
    expected_config_part_https = """
server {
    listen 443 ssl;
    server_name ssl.example.com;

    ssl_certificate /etc/nginx/ssl/ssl.example.com.crt;
    ssl_certificate_key /etc/nginx/ssl/ssl.example.com.key;

    location / {
        proxy_pass http://172.17.0.3:443;
    }
}
"""
    current_config = dummy_nginx.current_config
    assert_config_part_in_nginx_config(expected_config_part_http, current_config, "Expected HTTP config part for SSL container not found")
    assert_config_part_in_nginx_config(expected_config_part_https, current_config, "Expected HTTPS config part for SSL container not found")
