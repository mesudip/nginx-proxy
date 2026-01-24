"""
Unit tests for validation error handling in nginx-proxy.

This test suite verifies that nginx-proxy properly validates environment variables
like VIRTUAL_HOST and STATIC_VIRTUAL_HOST, and displays clear error messages instead
of stack traces when invalid configurations are detected.
"""

import pytest
import sys
from io import StringIO
from tests.helpers.docker_test_client import DockerTestClient
from nginx_proxy.pre_processors.virtual_host_processor import process_virtual_hosts


@pytest.fixture()
def docker_client():
    client = DockerTestClient()
    client.networks.create("frontend")
    yield client
    client.close()


def test_invalid_static_virtual_host_missing_destination(docker_client, capsys):
    """Test that STATIC_VIRTUAL_HOST without destination part shows proper error instead of stack trace"""
    # Create a container with invalid STATIC_VIRTUAL_HOST (missing destination after ->)
    container = docker_client.containers.create(
        "nginx:alpine",
        name="test_invalid_static_vhost",
        environment={"STATIC_VIRTUAL_HOST": "https://xyz.com:80/"},
        network="frontend",
    )
    container.start()
    
    # Get known networks
    known_networks = {docker_client.networks.get("frontend").id}
    
    # Process virtual hosts - should not raise TypeError
    from nginx_proxy.Container import Container
    env_map = Container.get_env_map(container)
    hosts = process_virtual_hosts(container, env_map, known_networks)
    
    # Check that no TypeError was raised (the bug)
    # The processing should handle this gracefully
    captured = capsys.readouterr()
    
    # Should NOT contain "TypeError" or stack trace
    assert "TypeError" not in captured.out
    assert "Traceback" not in captured.out
    assert "can only concatenate str" not in captured.out
    
    # Should contain proper error message
    assert "Invalid Configuration" in captured.out
    assert "STATIC_VIRTUAL_HOST must specify a destination address after '->'" in captured.out
    assert "https://xyz.com:80/" in captured.out
    
    # Should not add any hosts
    assert len(hosts) == 0
    

def test_invalid_static_virtual_host_empty_destination(docker_client, capsys):
    """Test that STATIC_VIRTUAL_HOST with empty destination shows proper error"""
    container = docker_client.containers.create(
        "nginx:alpine",
        name="test_invalid_static_vhost_empty",
        environment={"STATIC_VIRTUAL_HOST": "https://example.com ->"},
        network="frontend",
    )
    container.start()
    
    known_networks = {docker_client.networks.get("frontend").id}
    
    from nginx_proxy.Container import Container
    env_map = Container.get_env_map(container)
    hosts = process_virtual_hosts(container, env_map, known_networks)
    
    captured = capsys.readouterr()
    
    # Should NOT contain "TypeError" or stack trace
    assert "TypeError" not in captured.out
    assert "Traceback" not in captured.out
    
    # Should contain proper error message
    assert "Invalid Configuration" in captured.out
    assert "STATIC_VIRTUAL_HOST must specify a destination address after '->'" in captured.out


def test_valid_static_virtual_host(docker_client, capsys):
    """Test that valid STATIC_VIRTUAL_HOST works correctly"""
    container = docker_client.containers.create(
        "nginx:alpine",
        name="test_valid_static_vhost",
        environment={"STATIC_VIRTUAL_HOST": "https://xyz.com:80/ -> http://backend:8080"},
        network="frontend",
    )
    container.start()
    
    known_networks = {docker_client.networks.get("frontend").id}
    
    from nginx_proxy.Container import Container
    env_map = Container.get_env_map(container)
    hosts = process_virtual_hosts(container, env_map, known_networks)
    
    captured = capsys.readouterr()
    
    # Should process successfully
    assert "Valid configuration" in captured.out
    assert len(hosts) > 0
    
    # Should not have any error messages
    assert "Invalid Configuration" not in captured.out
    assert "TypeError" not in captured.out


def test_invalid_virtual_host_empty_hostname(docker_client, capsys):
    """Test that VIRTUAL_HOST with missing hostname is handled gracefully"""
    container = docker_client.containers.create(
        "nginx:alpine",
        name="test_invalid_vhost_empty",
        environment={"VIRTUAL_HOST": ""},
        network="frontend",
    )
    container.start()
    
    known_networks = {docker_client.networks.get("frontend").id}
    
    from nginx_proxy.Container import Container
    env_map = Container.get_env_map(container)
    
    # Should handle gracefully
    hosts = process_virtual_hosts(container, env_map, known_networks)
    
    captured = capsys.readouterr()
    
    # Should NOT contain "TypeError" or stack trace
    assert "TypeError" not in captured.out
    assert "Traceback" not in captured.out


def test_valid_virtual_host(docker_client, capsys):
    """Test that valid VIRTUAL_HOST works correctly"""
    container = docker_client.containers.create(
        "nginx:alpine",
        name="test_valid_vhost",
        environment={"VIRTUAL_HOST": "example.com"},
        network="frontend",
    )
    container.start()
    
    known_networks = {docker_client.networks.get("frontend").id}
    
    from nginx_proxy.Container import Container
    env_map = Container.get_env_map(container)
    hosts = process_virtual_hosts(container, env_map, known_networks)
    
    captured = capsys.readouterr()
    
    # Should process successfully
    assert "Valid configuration" in captured.out
    assert len(hosts) > 0
    
    # Should not have any error messages
    assert "Invalid Configuration" not in captured.out
    assert "TypeError" not in captured.out

