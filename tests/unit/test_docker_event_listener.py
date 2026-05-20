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


def test_process_container_start_with_healthcheck_waits_for_healthy(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.name = "health-container"
    container.attrs = {
        "Config": {"Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}},
        "State": {"Status": "running", "Health": {"Status": "starting"}},
    }
    docker_client.containers.get.return_value = container

    with patch("builtins.print") as mock_print:
        listener._process_container_event("start", {"Actor": {"ID": "container1", "Attributes": {}}})

    web_server.update_backend.assert_not_called()
    mock_print.assert_called_once_with(
        "Container waiting   ",
        "Id:container1",
        "    health-container",
        "for healthy",
        sep="\t",
    )


def test_process_container_healthy_event_adds_backend(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.attrs = {
        "Config": {"Env": [], "Labels": {}, "Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}},
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Name": "/healthy-container",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_container_health_event(
        "health_status: healthy",
        {"Actor": {"ID": "container1", "Attributes": {}}},
    )

    web_server.update_backend.assert_called_once()


def test_process_container_unhealthy_event_removes_backend(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)

    listener._process_container_health_event(
        "health_status: unhealthy",
        {"Actor": {"ID": "container1", "Attributes": {}}},
    )

    web_server.remove_backend.assert_called_once_with("container1")
    web_server.update_backend.assert_not_called()


def test_process_container_start_with_grace_period_defers_activation(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 0.2}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.attrs = {
        "Config": {"Env": [], "Labels": {}},
        "State": {"Status": "running"},
        "Name": "/grace-container",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    with patch("builtins.print") as mock_print:
        listener._process_container_event("start", {"Actor": {"ID": "container1", "Attributes": {}}})
    web_server.update_backend.assert_not_called()
    mock_print.assert_not_called()

    time.sleep(0.3)

    web_server.update_backend.assert_called_once()


def test_process_container_die_cancels_pending_activation(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 1}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.name = "dying-container"
    container.attrs = {
        "Config": {"Env": [], "Labels": {}},
        "State": {"Status": "running"},
        "Name": "/dying-container",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_container_event("start", {"Actor": {"ID": "container1", "Attributes": {}}})
    with patch("builtins.print") as mock_print:
        listener._process_container_event("die", {"Actor": {"ID": "container1", "Attributes": {"name": "dying-container"}}})

    time.sleep(0.1)

    web_server.update_backend.assert_not_called()
    web_server.remove_backend.assert_called_once_with("container1")
    mock_print.assert_called_once_with(
        "Container crashed   ",
        "Id:container1",
        "    dying-container",
        sep="\t",
    )


def test_process_healthchecked_container_die_logs_crash_while_waiting(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 1}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.name = "health-dying-container"
    container.attrs = {
        "Config": {"Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}},
        "State": {"Status": "running", "Health": {"Status": "starting"}},
        "Name": "/health-dying-container",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_container_event("start", {"Actor": {"ID": "container1", "Attributes": {}}})
    with patch("builtins.print") as mock_print:
        listener._process_container_event(
            "die",
            {"Actor": {"ID": "container1", "Attributes": {"name": "health-dying-container"}}},
        )

    web_server.update_backend.assert_not_called()
    web_server.remove_backend.assert_called_once_with("container1")
    mock_print.assert_called_once_with(
        "Container crashed   ",
        "Id:container1",
        "    health-dying-container",
        sep="\t",
    )


def test_network_connect_is_ignored_for_container_still_starting(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 1}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.status = "created"
    container.attrs = {
        "Config": {"Env": [], "Labels": {}},
        "State": {"Status": "created"},
        "Name": "/starting-container",
        "NetworkSettings": {"Networks": {"frontend": {}}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_network_event(
        "connect",
        {"Actor": {"ID": "network1", "Attributes": {"container": "container1"}}, "scope": "local"},
    )

    web_server.connect.assert_not_called()


def test_network_connect_is_ignored_for_unhealthy_container(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    listener._started_containers.add("container1")
    container = MagicMock()
    container.id = "container1"
    container.status = "running"
    container.attrs = {
        "Config": {"Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}, "Labels": {}},
        "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
        "Name": "/unhealthy-container",
        "NetworkSettings": {"Networks": {"frontend": {}}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_network_event(
        "connect",
        {"Actor": {"ID": "network1", "Attributes": {"container": "container1"}}, "scope": "local"},
    )

    web_server.connect.assert_not_called()
