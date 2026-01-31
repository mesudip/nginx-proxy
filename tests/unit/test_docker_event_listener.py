import pytest
from unittest.mock import MagicMock, patch
import threading
import time

from nginx_proxy.DockerEventListener import DockerEventListener
from nginx_proxy.WebServer import WebServer


@pytest.fixture
def web_server():
    server = MagicMock()
    server.config = {"docker_swarm": "enable"}
    return server


@pytest.fixture
def docker_client():
    return MagicMock()


@pytest.fixture
def swarm_client():
    return MagicMock()


def test_run_with_separate_clients(web_server, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    with patch('threading.Thread') as mock_thread:
        listener.run()
        assert mock_thread.call_count == 2


def test_run_with_same_client(web_server, docker_client):
    listener = DockerEventListener(web_server, docker_client, docker_client)
    with patch.object(listener, '_listen') as mock_listen:
        listener.run()
        mock_listen.assert_called_once_with(docker_client)


def test_listen_for_container_events(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore"}
    listener = DockerEventListener(web_server, docker_client, docker_client)

    docker_client.events.return_value = iter([
        {"Type": "container", "Action": "start", "id": "container1"},
        {"Type": "container", "Action": "stop", "id": "container2"},
    ])

    with patch.object(listener, '_process_container_event') as mock_process:
        t = threading.Thread(target=listener._listen, args=(docker_client,))
        t.daemon = True
        t.start()
        time.sleep(0.1)
        assert mock_process.call_count == 2
        docker_client.events.return_value = iter([])
        t.join(timeout=0.1)


def test_process_service_event_update(web_server:WebServer, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    event = {"Action": "update", "Actor": {"ID": service_id}}
    mock_service = MagicMock()
    swarm_client.services.get.return_value = mock_service

    listener._process_service_event("update", event)

    swarm_client.services.get.assert_called_once_with(service_id)
    web_server.update_backend.assert_called_once()


def test_process_service_event_remove(web_server:WebServer, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    event = {"Action": "remove", "Actor": {"ID": service_id}}

    listener._process_service_event("remove", event)

    web_server.remove_backend.assert_called_once_with(service_id)
