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
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.Location import Location
from nginx_proxy.ProxyConfigData import ProxyConfigData
from nginx_proxy.NginxProxyApp import NginxProxyAppConfig
import os

from tests.helpers.docker_test_client import DockerTestClient, MockContainer, MockNetwork
from nginx_proxy.DockerEventListener import DockerEventListener


def get_test_config(enable_ipv6: bool = False) -> NginxProxyAppConfig:
    """Create a test configuration for WebServer."""
    return NginxProxyAppConfig(
        dummy_nginx=True,
        ssl_dir="./.run_data",
        conf_dir="./run_data",
        client_max_body_size="1m",
        challenge_dir="./.run_data/acme-challenges/",
        default_server=True,
        vhosts_template_dir="./vhosts_template",
        cert_renew_threshold_days=10,
        certapi_url="",
        wellknown_path="/.well-known/acme-challenge/",
        enable_ipv6=enable_ipv6,
    )


# @pytest.fixture(scope="session")
@pytest.fixture()
def nginx(webserver: WebServer):
    nginx.webserver = webserver
    return webserver.nginx


# @pytest.fixture(scope="session")
@pytest.fixture()
def docker_client():
    return DockerTestClient()


# @pytest.fixture(scope="session")
@pytest.fixture()
def webserver(docker_client: DockerTestClient):
    yield from create_webserver(docker_client)


def create_webserver(docker_client: DockerTestClient, enable_ipv6: bool = False):
    # Initialize DockerTestClient
    docker_client.networks.create("frontend")  # Default network
    os.environ["LETSENCRYPT_API"] = "https://acme-staging-v02.api.letsencrypt.org/directory"

    with patch("certapi.manager.acme_cert_manager.AcmeCertManager.setup") as mock_acme_setup:
        mock_acme_setup.return_value = None  # Make setup do nothing
        # Initialize WebServer with test config
        config = get_test_config(enable_ipv6)
        webserver = WebServer(docker_client, config, nginx_update_throtle_sec=0.1)

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
        webserver.ssl_processor.ssl.certificate_expiry_thread.join(timeout=2)


pattern = re.compile(r"^http://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:80")


def expect_server_up(nginx: DummyNginx, server_name: str, exact=True):
    server = expect_server(nginx, server_name, exact)
    assert len(server.locations) > 0, f"Server for {server_name} should have locations."
    proxy_loc = next((l for l in server.locations if l.proxy_pass is not None), None)
    assert proxy_loc is not None, f"Server for {server_name} should have a location with proxy_pass."
    assert proxy_loc.proxy_pass.startswith("http")
    return server


def expect_server(nginx: DummyNginx, server_name: str, exact=True):
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
        assert (
            server_name not in server.server_names
        ), f"Server for {server_name} should not be present. All servers:\n{all_servers_str}"


def expect_server_down(nginx: DummyNginx, server_name: str):
    server = expect_server(nginx, server_name)
    config = HttpBlock.parse(nginx.current_config)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    # Filter out acme challenge location
    locations = [l for l in server.locations if l.path != "/.well-known/acme-challenge/"]
    assert (
        len(locations) == 0
    ), f"Server for {server_name} should have no locations (except acme). All servers:\n{all_servers_str}"
    assert server.return_code == "503", f"Server for {server_name} should return 503. All servers:\n{all_servers_str}"


def test_webserver_initialization(webserver: WebServer, nginx: DummyNginx):
    assert isinstance(webserver.ssl_processor.ssl.challenge_store, NginxChallengeSolver)
    # Check initial default server block
    config = NginxConfig()
    full_config_str = f"http {{\n{nginx.current_config}\n}}"
    config.load(full_config_str)

    assert len(config.http.servers) == 1
    server = config.http.servers[0]
    assert server.listen == "80 default_server"


def test_webserver_add_container(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    hostname = "add_container.example.com"
    env = {"VIRTUAL_HOST": hostname}
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(0.2)  # Small delay for async event processing

    example_server = expect_server_up(nginx, hostname)

    assert example_server.server_names == [hostname]
    assert "80" in example_server.listen

    assert len(example_server.locations) == 2  # .wellknown and the backend
    location = next(l for l in example_server.locations if l.path == "/")
    container_ip = container.attrs["NetworkSettings"]["Networks"]["frontend"]["IPAddress"]
    assert f"http://{container_ip}:80" in location.proxy_pass


def test_webserver_remove_container(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    hostname = "remove_container.example.com"
    env = {"VIRTUAL_HOST": hostname}

    # Add container
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(0.2)

    # Verify addition
    expect_server_up(nginx, hostname)

    # Remove container
    container.remove(force=True)
    time.sleep(1)  # Increased sleep duration

    # Verify removal
    expect_server_down(nginx, hostname)


def test_webserver_add_network(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    hostname = "add_network.example.com"
    env = {
        "VIRTUAL_HOST": hostname,
    }

    # Create container on a different network
    docker_client.networks.create("other_network")
    container = docker_client.containers.run(
        "nginx:alpine", name=container_name, environment=env, network="other_network"
    )
    time.sleep(0.2)

    # Verify that the server is not in the config
    expect_server_not_present(nginx, hostname)

    # Add the container to the frontend network
    frontend_network = docker_client.networks.get("frontend")
    frontend_network.connect(container.id)
    time.sleep(0.2)

    # Verify that the server is now in the config
    expect_server_up(nginx, hostname)


def test_webserver_remove_network(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    hostname = "remove_network.example.com"
    env = {
        "VIRTUAL_HOST": hostname,
    }

    # Create container on the frontend network
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(3)

    # Verify that the server is in the config
    expect_server_up(nginx, hostname)

    # Remove the container from the frontend network
    frontend_network = docker_client.networks.get("frontend")
    frontend_network.disconnect(container.id)
    time.sleep(3)  # Increased sleep duration

    # Verify that the server is no longer in the config
    expect_server_down(nginx, hostname)


def test_webserver_recreate_same_name_container_with_different_host(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "test_container"
    old_hostname = "old.recreate.example.com"
    new_hostname = "new.recreate.example.com"
    env_old = {"VIRTUAL_HOST": old_hostname}
    # Create with old env
    container = docker_client.containers.run(
        "nginx:alpine", name=container_name, environment=env_old, network="frontend"
    )
    time.sleep(0.2)
    expect_server_up(nginx, old_hostname)

    # Remove the old container and create a new one with the same name but new env
    container.remove(force=True)
    time.sleep(1)  # Increased sleep duration
    env_new = {"VIRTUAL_HOST": new_hostname}
    docker_client.containers.run("nginx:alpine", name=container_name, environment=env_new, network="frontend")
    time.sleep(0.2)

    # Verify new server exists and old one is gone
    expect_server_up(nginx, new_hostname)
    expect_server_down(nginx, old_hostname)


def test_webserver_add_container_with_ssl(docker_client: DockerTestClient, nginx: DummyNginx):
    container_name = "ssl_container"
    hostname = "ssl.example.com"
    env = {"VIRTUAL_HOST": f"https://{hostname}"}

    # Create container with SSL env
    container = docker_client.containers.run("nginx:alpine", name=container_name, environment=env, network="frontend")
    time.sleep(0.2)

    # Verify that two server blocks are created for SSL
    config = HttpBlock.parse(nginx.current_config)
    servers = [s for s in config.servers if hostname in s.server_names]
    assert len(servers) == 1

    # Verify HTTPS server is correctly configured
    https_server = next((s for s in servers if "443" in s.listen), None)
    assert https_server is not None
    assert "ssl" in https_server.listen
    assert https_server.ssl_certificate.endswith(f"/{hostname}.selfsigned.crt")
    assert https_server.ssl_certificate_key.endswith(f"/{hostname}.selfsigned.key")

    location = next(l for l in https_server.locations if l.path == "/")
    container_ip = container.attrs["NetworkSettings"]["Networks"]["frontend"]["IPAddress"]
    assert f"http://{container_ip}:80" in location.proxy_pass


def test_webserver_add_two_containers_with_same_virtual_host(docker_client: DockerTestClient, nginx: DummyNginx):
    hostname = "two_containers.example.com"
    env = {"VIRTUAL_HOST": hostname}
    c1 = docker_client.containers.run("nginx:alpine", name="test_container_1", environment=env, network="frontend")
    c2 = docker_client.containers.run("nginx:alpine", name="test_container_2", environment=env, network="frontend")
    time.sleep(0.2)

    config = HttpBlock.parse(nginx.current_config)

    # Verify upstream block is created
    assert len(config.upstreams) == 1
    upstream = config.upstreams[0]
    assert hostname in upstream.parameters

    # Verify both container IPs are in the upstream
    ip1 = c1.attrs["NetworkSettings"]["Networks"]["frontend"]["IPAddress"]
    ip2 = c2.attrs["NetworkSettings"]["Networks"]["frontend"]["IPAddress"]
    upstream_servers = [d.values[0] for d in upstream.get_directives("server")]
    assert f"{ip1}:80" in upstream_servers
    assert f"{ip2}:80" in upstream_servers

    # Verify server block uses upstream
    server = expect_server_up(nginx, hostname)
    assert f"http://{hostname}" in next(l.proxy_pass for l in server.locations if l.path == "/")


def test_webserver_restart_container_extras_do_not_duplicate_servers(
    docker_client: DockerTestClient, nginx: DummyNginx
):
    """Restart a container with changing VIRTUAL_HOST extras and ensure no duplicate server blocks."""

    container_name = "multi_restart_container"
    # sequence of (hostname, client_max_body_size) tuples
    scenarios = [
        ("first.restart.e.com", "0"),
        ("first.restart.e.com", "5m"),
        ("second.restart.e.com", "0"),
        ("second.restart.e.com", "0"),
    ]

    active_container = None
    try:
        for hostname, client_max_body_size in scenarios:
            # construct VIRTUAL_HOST env value
            env_value = f"{hostname} ; client_max_body_size {client_max_body_size}"
            # start container with current env
            active_container = docker_client.containers.run(
                "nginx:alpine", name=container_name, environment={"VIRTUAL_HOST": env_value}, network="frontend"
            )
            time.sleep(0.4)

            # Ensure host appears exactly once and check client_max_body_size directive
            config = HttpBlock.parse(nginx.current_config)
            servers_for_host = [s for s in config.servers if hostname in s.server_names]
            assert (
                len(servers_for_host) == 1
            ), f"Host {hostname} should have exactly one server block after restart, found {len(servers_for_host)}"
            server_block = servers_for_host[0]
            # find primary location (path '/') and assert client_max_body_size matches expected
            loc = next((l for l in server_block.locations if l.path == "/"), None)
            assert loc is not None, f"Location / not found for host {hostname}"
            # Assert directive equals the client_max_body_size value
            assert (
                loc.client_max_body_size == client_max_body_size
            ), f"Expected client_max_body_size {client_max_body_size} for host {hostname}, got {loc.client_max_body_size}"
            # Remove container to simulate restart for next scenario
            active_container.remove(force=True)
            active_container = None
            time.sleep(0.8)

            # Host should disappear after removal (no lingering duplicates)
            expect_server_down(nginx, hostname)

        # After final scenario, ensure each host is in 'down' state (no active locations)
        expected_hosts = {hostname for hostname, _ in scenarios}
        for h in expected_hosts:
            expect_server_down(nginx, h)

    finally:
        if active_container is not None:
            active_container.remove(force=True)
            time.sleep(0.5)
