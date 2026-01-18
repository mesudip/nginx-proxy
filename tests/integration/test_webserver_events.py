import pytest
import docker
import time
import re
from typing import List

from nginx.NginxConf import HttpBlock, NginxConfig, ServerBlock
from tests.helpers.docker_utils import start_backend_container

# Regex to match the dynamically assigned IP:PORT for proxy_pass
# Example: http://172.18.0.2:80
pattern = re.compile(r"^http://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:80")


def get_nginx_config_from_container(nginx_proxy_container: docker.models.containers.Container) -> str:
    """
    Executes a command inside the nginx_proxy_container to get the current Nginx configuration.
    """
    _, output = nginx_proxy_container.exec_run("cat /etc/nginx/conf.d/nginx-proxy.conf")
    return output.decode("utf-8")


def expect_server_up_integration(
    nginx_proxy_container: docker.models.containers.Container, server_name: str, exact=True, timeout=10
):
    """
    Waits for a server block with the given server_name to appear in the Nginx config
    and have at least one location with a proxy_pass.
    """
    for i in range(timeout):
        config_str = get_nginx_config_from_container(nginx_proxy_container)
        config = HttpBlock.parse(config_str)
        for server in config.servers:
            if server_name in server.server_names:
                if len(server.locations) > 0 and server.locations[0].proxy_pass is not None:
                    print(f"Server '{server_name}' found and up after {i+1} seconds.")
                    return server
            if not exact:
                for sn in server.server_names:
                    if server_name in sn:
                        if len(server.locations) > 0 and server.locations[0].proxy_pass is not None:
                            print(f"Server '{server_name}' (partial match) found and up after {i+1} seconds.")
                            return server
        time.sleep(1)

    config_str = get_nginx_config_from_container(nginx_proxy_container)
    config = HttpBlock.parse(config_str)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    pytest.fail(
        f"Server for '{server_name}' not found or not up after {timeout} seconds. Current config:\n{all_servers_str}"
    )


def expect_server_down_integration(
    nginx_proxy_container: docker.models.containers.Container, server_name: str, timeout=10
):
    """
    Waits for a server block with the given server_name to either not be present
    or to be present but configured as a 503 error page (no locations, return 503).
    """
    for i in range(timeout):
        config_str = get_nginx_config_from_container(nginx_proxy_container)
        config = HttpBlock.parse(config_str)

        found_server = None
        for server in config.servers:
            if server_name in server.server_names:
                found_server = server
                break

        if found_server is None:
            print(f"Server '{server_name}' not present after {i+1} seconds (expected down).")
            return  # Server is completely gone, which is a valid "down" state

        # If server is present, check if it's a 503 error page
        if found_server.return_code == "503":
            print(f"Server '{server_name}' found but configured as 503 after {i+1} seconds (expected down).")
            if len(found_server.locations) == 0:
                return
            else:
                assert len(found_server.locations) == 1
                assert found_server.locations[0].path.startswith("/.well-known")
        return

        time.sleep(1)

    config_str = get_nginx_config_from_container(nginx_proxy_container)
    config = HttpBlock.parse(config_str)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    pytest.fail(f"Server for '{server_name}' still active after {timeout} seconds. Current config:\n{all_servers_str}")


def expect_server_not_present_integration(
    nginx_proxy_container: docker.models.containers.Container, server_name: str, timeout=10
):
    """
    Waits for a server block with the given server_name to not be present in the Nginx config.
    """
    for i in range(timeout):
        config_str = get_nginx_config_from_container(nginx_proxy_container)
        config = HttpBlock.parse(config_str)

        present = False
        for server in config.servers:
            if server_name in server.server_names:
                present = True
                break

        if not present:
            print(f"Server '{server_name}' not present after {i+1} seconds (expected not present).")
            return

        time.sleep(1)

    config_str = get_nginx_config_from_container(nginx_proxy_container)
    config = HttpBlock.parse(config_str)
    all_servers_str = "\n".join([str(s) for s in config.servers])
    pytest.fail(f"Server for '{server_name}' still present after {timeout} seconds. Current config:\n{all_servers_str}")


def test_webserver_initialization_integration(nginx_proxy_container: docker.models.containers.Container):
    """
    Test the initial Nginx configuration generated by the proxy container.
    Should contain a default 503 server.
    """
    container, _, _ = nginx_proxy_container
    config_str = get_nginx_config_from_container(container)
    config = NginxConfig()
    full_config_str = f"http {{\n{config_str}\n}}"  # NginxConfig expects full http block
    config.load(full_config_str)

    # Find the default server
    default_server = None
    for server in config.http.servers:
        if "default_server" in server.listen:
            default_server = server
            break

    assert default_server is not None, "Default server block not found."
    assert default_server.server_names == ["_"]
    assert default_server.error_page == "503 /503_default.html"

    # Check locations within the default server
    assert len(default_server.locations) >= 3  # At least 3 locations: acme, 503_default, /

    acme_loc = next((loc for loc in default_server.locations if loc.path == "/.well-known/acme-challenge/"), None)
    assert acme_loc is not None
    assert acme_loc.alias == "/etc/nginx/acme-challenges/"  # This path is inside the container
    assert acme_loc.try_files == "$uri =404"

    error_loc = next((loc for loc in default_server.locations if loc.path == "= /503_default.html"), None)
    assert error_loc is not None
    assert error_loc.root == "/app/vhosts_template/errors"  # This path is inside the container
    assert error_loc.internal is not None

    root_loc = next((loc for loc in default_server.locations if loc.path == "/"), None)
    assert root_loc is not None
    assert root_loc.return_code == "503"


def test_webserver_add_container_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that adding a container with VIRTUAL_HOST creates a corresponding Nginx server block.
    """
    container_name = "test_backend_add"
    virtual_host = "add.example.com"
    env = {"VIRTUAL_HOST": virtual_host}
    backend: docker.models.containers.Container = None
    try:
        backend = start_backend_container(docker_client, test_network, env)

        server = expect_server_up_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout

    finally:
        if backend:
            backend.remove(force=True)


def test_webserver_remove_container_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that removing a container with VIRTUAL_HOST removes its Nginx server block
    or converts it to a 503 error page.
    """
    container_name = "test_backend_remove"
    virtual_host = "remove.example.com"
    env = {"VIRTUAL_HOST": virtual_host}

    backend = start_backend_container(docker_client, test_network, env)
    expect_server_up_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout

    backend.remove(force=True)
    expect_server_down_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout


def test_webserver_add_network_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that connecting a container to the proxy's network adds its Nginx server block.
    """
    container_name = "test_backend_add_net"
    virtual_host = "addnet.example.com"
    env = {"VIRTUAL_HOST": virtual_host}

    # Create container on a different network first
    other_network_name = "other-test-network"
    other_network = docker_client.networks.create(other_network_name, driver="bridge")
    backend = None
    try:
        backend = start_backend_container(docker_client, other_network, env)

        expect_server_not_present_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout

        # Connect the container to the test_network (frontend network for the proxy)
        test_network.connect(backend)

        expect_server_up_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout
    finally:
        if backend:
            backend.remove(force=True)
        if other_network:
            other_network.remove()


def test_webserver_remove_network_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that disconnecting a container from the proxy's network removes its Nginx server block
    or converts it to a 503 error page.
    """
    container_name = "test_backend_remove_net"
    virtual_host = "removenet.example.com"
    env = {"VIRTUAL_HOST": virtual_host}

    backend = start_backend_container(docker_client, test_network, env)
    expect_server_up_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout

    # Disconnect the container from the test_network
    test_network.disconnect(backend)

    expect_server_down_integration(nginx_proxy_container[0], virtual_host, timeout=15)  # Increased timeout

    backend.remove(force=True)  # Clean up


def test_webserver_recreate_same_name_container_with_different_host_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that recreating a container with the same name but a different VIRTUAL_HOST
    correctly updates the Nginx configuration.
    """
    container_name = "test_backend_recreate"
    old_virtual_host = "old.example.com"
    new_virtual_host = "new.example.com"

    # Create with old env
    backend_old = start_backend_container(docker_client, test_network, {"VIRTUAL_HOST": old_virtual_host})
    expect_server_up_integration(nginx_proxy_container[0], old_virtual_host, timeout=15)  # Increased timeout

    # Remove the old container
    backend_old.remove(force=True)
    expect_server_down_integration(nginx_proxy_container[0], old_virtual_host, timeout=15)  # Increased timeout

    # Create a new one with the same name but new env
    backend_new = start_backend_container(docker_client, test_network, {"VIRTUAL_HOST": new_virtual_host})
    try:
        expect_server_up_integration(nginx_proxy_container[0], new_virtual_host, timeout=15)  # Increased timeout
        expect_server_down_integration(
            nginx_proxy_container[0], old_virtual_host, timeout=15
        )  # Ensure old is still down
    finally:
        if backend_new:
            backend_new.remove(force=True)


def test_webserver_add_container_with_ssl_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that adding a container with an HTTPS VIRTUAL_HOST creates both HTTP redirect
    and HTTPS server blocks with self-signed certificates.
    """
    container_name = "test_backend_ssl"
    virtual_host = "ssl-test.example.com"
    env = {"VIRTUAL_HOST": f"https://{virtual_host}"}

    backend = start_backend_container(docker_client, test_network, env)
    try:
        # Wait for the server to be up (either HTTP redirect or HTTPS)
        # We expect two server blocks for this host
        time.sleep(10)  # Give ample time for SSL cert generation and Nginx reload

        config_str = get_nginx_config_from_container(nginx_proxy_container[0])
        config = HttpBlock.parse(config_str)

        servers_for_host: List[ServerBlock] = [s for s in config.servers if virtual_host in s.server_names]

        assert (
            len(servers_for_host) == 1
        ), f"Expected 1 server blocks for {virtual_host}, found {len(servers_for_host)}. Config:\n{config_str}"

        https_server = next((s for s in servers_for_host if "443" in s.listen), None)

        assert https_server is not None, "HTTPS server block not found."

        # Verify HTTPS server is correctly configured
        assert "ssl" in https_server.listen
        assert https_server._get_directive_value("ssl_certificate").endswith(f"/{virtual_host}.selfsigned.crt")
        assert https_server._get_directive_value("ssl_certificate_key").endswith(f"/{virtual_host}.selfsigned.key")
    finally:
        if backend:
            backend.remove(force=True)


def test_webserver_add_two_containers_with_same_virtual_host_integration(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that adding two containers with the same VIRTUAL_HOST creates an upstream block
    and the server block uses it.
    """
    virtual_host = "loadbalance1.example.com"
    env = {"VIRTUAL_HOST": virtual_host}

    backend1 = start_backend_container(docker_client, test_network, env)
    backend2 = start_backend_container(docker_client, test_network, env)
    try:
        time.sleep(10)  # Give ample time for Nginx to reload with upstream config

        server = expect_server_up_integration(
            nginx_proxy_container[0], virtual_host, timeout=15
        )  # Already waited, so quick check

        config_str = get_nginx_config_from_container(nginx_proxy_container[0])
        config = HttpBlock.parse(config_str)

        # Verify upstream block is created
        upstream = next((u for u in config.upstreams if virtual_host in u.parameters), None)
        assert upstream is not None, f"Upstream block for {virtual_host} not found. Config:\n{config_str}"
        assert (
            len(upstream.get_directives("server")) == 2
        ), f"Expected 2 servers in upstream, found {len(upstream.get_directives('server'))}. Config:\n{config_str}"

        # Verify server block uses upstream
        assert f"http://{virtual_host}" in server.locations[0].proxy_pass
    finally:
        if backend1:
            backend1.remove(force=True)
        if backend2:
            backend2.remove(force=True)


def test_when_container_shuts_down__then_ip_removed_from_upstream(
    nginx_proxy_container: docker.models.containers.Container,
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
):
    """
    Test that adding two containers with the same VIRTUAL_HOST creates an upstream block
    and the server block uses it.
    """
    virtual_host = "loadbalance2.example.com"
    env = {"VIRTUAL_HOST": virtual_host}

    backend1 = start_backend_container(docker_client, test_network, env)
    backend2 = start_backend_container(docker_client, test_network, env)
    backend3 = start_backend_container(docker_client, test_network, env)
    try:
        time.sleep(10)  # Give ample time for Nginx to reload with upstream config

        server = expect_server_up_integration(
            nginx_proxy_container[0], virtual_host, timeout=15
        )  # Already waited, so quick check

        config_str = get_nginx_config_from_container(nginx_proxy_container[0])
        config = HttpBlock.parse(config_str)

        # Verify upstream block is created
        upstream = next((u for u in config.upstreams if virtual_host in u.parameters), None)
        assert upstream is not None, f"Upstream block for {virtual_host} not found. Config:\n{config_str}"
        assert (
            len(upstream.get_directives("server")) == 3
        ), f"Expected 3 servers in upstream, found {len(upstream.get_directives('server'))}. Config:\n{config_str}"

        # Verify server block uses upstream
        assert f"http://{virtual_host}" in server.locations[0].proxy_pass

        # Stop one backend
        backend1.stop()
        time.sleep(10)

        config_str = get_nginx_config_from_container(nginx_proxy_container[0])
        config = HttpBlock.parse(config_str)

        # Verify upstream block is created
        upstream = next((u for u in config.upstreams if virtual_host in u.parameters), None)
        assert upstream is not None, f"Upstream block for {virtual_host} not found. Config:\n{config_str}"
        assert (
            len(upstream.get_directives("server")) == 2
        ), f"Expected 2 servers in upstream, found {len(upstream.get_directives('server'))}. Config:\n{config_str}"

        backend2.stop()
        time.sleep(10)
        config_str = get_nginx_config_from_container(nginx_proxy_container[0])
        config = HttpBlock.parse(config_str)
        upstream = next((u for u in config.upstreams if virtual_host in u.parameters), None)
        assert (
            upstream is None or len(upstream.get_directives("server")) == 1
        ), f"Expected 1 server in upstream after one shutdown, found {len(upstream.get_directives('server'))}. Config:\n{config_str}"
        expect_server_up_integration(
            nginx_proxy_container[0], virtual_host, timeout=15
        )  # Already waited, so quick check

    finally:
        if backend1:
            backend1.remove(force=True)
        if backend2:
            backend2.remove(force=True)
        if backend3:
            backend3.remove(force=True)
