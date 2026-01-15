import re
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

# @pytest.fixture(scope="session")
@pytest.fixture()
def nginx(webserver:WebServer):
    return webserver.nginx
    
# @pytest.fixture(scope="session")
@pytest.fixture()
def docker_client():
    return DockerTestClient()

# @pytest.fixture(scope="session")
@pytest.fixture()
def webserver(docker_client:DockerTestClient):
    yield from create_webserver(docker_client)

def create_webserver(docker_client: DockerTestClient):
    # Initialize DockerTestClient
    docker_client.networks.create("frontend")  # Default network
    os.environ["LETSENCRYPT_API"] = "https://acme-staging-v02.api.letsencrypt.org/directory"

    # Patch WebServer's loadconfig to use dummy nginx and specific paths
    with patch('nginx_proxy.WebServer.WebServer.loadconfig') as mock_loadconfig, \
         patch('certapi.manager.acme_cert_manager.AcmeCertManager.setup') as mock_acme_setup:
        mock_acme_setup.return_value = None # Make setup do nothing
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
        webserver = WebServer(docker_client,nginx_update_throtle_sec=0.1)

        # Start DockerEventListener in a background thread to process events
        listener = DockerEventListener(webserver, docker_client)
        listener_thread = threading.Thread(target=listener.run, daemon=True)
        listener_thread.start()
        # Yield components for testing
        yield webserver
        
        # Stop the listener by closing the docker client (sends sentinel to event queue)
        docker_client.close()
        # Wait for the thread to finish
        listener_thread.join(timeout=2)
        
        # Stop the SSL refresh thread
        webserver.cleanup()
        webserver.ssl_processor.certificate_expiry_thread.join(timeout=2)

pattern = re.compile(r'^http://172\.18\.\d{1,3}\.\d{1,3}:80')


def expect_server_up(nginx: DummyNginx, server_name: str,exact=True):
    expect_server(nginx, server_name,exact)
    server = expect_server(nginx, server_name,exact)
    assert len(server.locations) > 0, f"Server for {server_name} should have locations."
    assert server.locations[0].proxy_pass is not None, f"Server for {server_name} should have a proxy_pass."
    return server
def expect_server(nginx: DummyNginx, server_name: str,exact=True):
    config = HttpBlock.parse(nginx.current_config)
    for server in config.servers:
        if server_name in server.server_names:
            return server
        if not exact:
            for sn in server.server_names:
                if server_name in sn:
                    return server
    config = HttpBlock.parse(nginx.current_config)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    assert False, f"Server for {server_name} not found. All servers:\n{all_servers_str}"

def expect_server_not_present(nginx: DummyNginx, server_name: str):
    config = HttpBlock.parse(nginx.current_config)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    for server in config.servers:
        assert server_name not in server.server_names, f"Server for {server_name} should not be present. All servers:\n{all_servers_str}"

def expect_server_down(nginx: DummyNginx, server_name: str):
    server = expect_server(nginx, server_name)
    config = HttpBlock.parse(nginx.current_config)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    assert len(server.locations) == 0, f"Server for {server_name} should have no locations. All servers:\n{all_servers_str}"
    assert server.return_code == "503", f"Server for {server_name} should return 503. All servers:\n{all_servers_str}"

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
    assert loc0.alias == "./.run_data/acme-challenges/"
    assert loc0.try_files == "$uri =404"

    loc1 = server.locations[1]
    assert loc1.path == "= /503_default.html"
    assert loc1.root == "./vhosts_template/errors"
    assert loc1.internal is not None

    loc2 = server.locations[2]
    assert loc2.path == "/"
    assert loc2.return_code == "503"

def test_webserver_add_container(docker_client:DockerTestClient,nginx:DummyNginx):
    container_name = "test_container"
    env={
        "VIRTUAL_HOST": "example.com"
    }
    docker_client.containers.run( "nginx:alpine",name=container_name, 
                                 environment=env,
                                 network="frontend")
    time.sleep(0.2)  # Small delay for async event processing

    example_server = expect_server_up(nginx, "example.com")

    assert example_server.server_names == ["example.com"]
    assert "443" in example_server.listen

    assert len(example_server.locations) == 1
    location = example_server.locations[0]
    assert location.path == "/"
    assert bool(pattern.fullmatch(location.proxy_pass)) == True

def test_webserver_remove_container(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    env = {
        "VIRTUAL_HOST": "example.com"
    }

    # Add container
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(0.2)

    # Verify addition
    expect_server_up(nginx, "example.com")

    # Remove container
    container.remove(force=True)
    time.sleep(1) # Increased sleep duration

    # Verify removal
    expect_server_down(nginx, "example.com")

def test_webserver_add_network(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    env = {
        "VIRTUAL_HOST": "example.com",
    }

    # Create container on a different network
    docker_client.networks.create("other_network")
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="other_network")
    time.sleep(0.2)

    # Verify that the server is not in the config
    expect_server_not_present(nginx, "example.com")

    # Add the container to the frontend network
    frontend_network = docker_client.networks.get("frontend")
    frontend_network.connect(container.id)
    time.sleep(0.2)

    # Verify that the server is now in the config
    expect_server_up(nginx, "example.com")

def test_webserver_remove_network(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    env = {
        "VIRTUAL_HOST": "example.com",
    }

    # Create container on the frontend network
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(3)

    # Verify that the server is in the config
    expect_server_up(nginx, "example.com")

    # Remove the container from the frontend network
    frontend_network = docker_client.networks.get("frontend")
    frontend_network.disconnect(container.id)
    time.sleep(3) # Increased sleep duration

    # Verify that the server is no longer in the config
    expect_server_down(nginx, "example.com")

def test_webserver_recreate_same_name_container_with_different_host(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    env_old = {
        "VIRTUAL_HOST": "old.example.com"
    }
    # Create with old env
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env_old, network="frontend")
    time.sleep(0.2)
    expect_server_up(nginx, "old.example.com")

    # Remove the old container and create a new one with the same name but new env
    container.remove(force=True)
    time.sleep(1) # Increased sleep duration
    env_new = {
        "VIRTUAL_HOST": "new.example.com"
    }
    docker_client.containers.run("nginx:alpine", name=container_name, environment=env_new, network="frontend")
    time.sleep(0.2)

    # Verify new server exists and old one is gone
    expect_server_up(nginx, "new.example.com")
    expect_server_down(nginx, "old.example.com")

def test_webserver_add_container_with_ssl(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "ssl_container"
    env = {
        "VIRTUAL_HOST": "https://ssl.example.com"
    }

    # Create container with SSL env
    docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(0.2)

    # Verify that two server blocks are created for SSL
    config = HttpBlock.parse(nginx.current_config)
    servers = [s for s in config.servers if "ssl.example.com" in s.server_names]
    assert len(servers) == 2

    # Verify HTTP server redirects to HTTPS
    http_server = next((s for s in servers if "80" in s.listen), None)
    assert http_server is not None
    assert http_server.locations[0].return_code == "301 https://$host$request_uri"

    # Verify HTTPS server is correctly configured
    https_server = next((s for s in servers if "443" in s.listen), None)
    assert https_server is not None
    assert "ssl" in https_server.listen
    assert https_server.ssl_certificate.endswith("/ssl.example.com.selfsigned.crt")
    assert https_server.ssl_certificate_key.endswith("/ssl.example.com.selfsigned.key")
    assert bool(pattern.fullmatch(https_server.locations[0].proxy_pass))


def test_webserver_add_two_containers_with_same_virtual_host(docker_client: DockerTestClient, nginx: DummyNginx):
    env = {
        "VIRTUAL_HOST": "example.com"
    }
    docker_client.containers.run("nginx:alpine", name="test_container_1", environment=env, network="frontend")
    docker_client.containers.run("nginx:alpine", name="test_container_2", environment=env, network="frontend")
    time.sleep(0.2)

    config = HttpBlock.parse(nginx.current_config)
    
    # Verify upstream block is created
    assert len(config.upstreams) == 1
    upstream = config.upstreams[0]
    assert  "example.com" in upstream.parameters
    assert len(upstream.get_directives('server')) == 2

    # Verify server block uses upstream
    server = expect_server_up(nginx, "example.com")
    assert 'http://example.com' in server.locations[0].proxy_pass
