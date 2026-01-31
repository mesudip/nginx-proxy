import docker
import pytest
import requests
from typing import List
import time

from nginx.NginxConf import HttpBlock, NginxConfig, ServerBlock
from tests.helpers.docker_utils import start_backend, stop_backend

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
