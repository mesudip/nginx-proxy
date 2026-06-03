import pytest
from unittest.mock import MagicMock, patch
import threading
import time

import docker

from nginx_proxy.DockerEventListener import ContainerEvent, DockerEventListener
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
    with (
        patch.object(listener, "start_dispatcher"),
        patch.object(listener, "stop_dispatcher"),
        patch("threading.Thread") as mock_thread,
    ):
        listener.run()
        assert mock_thread.call_count == 2


def test_run_with_same_client(web_server, docker_client):
    listener = DockerEventListener(web_server, docker_client, docker_client)
    with (
        patch.object(listener, "start_dispatcher"),
        patch.object(listener, "stop_dispatcher"),
        patch.object(listener, "_listen") as mock_listen,
    ):
        listener.run()
        mock_listen.assert_called_once_with(docker_client)


def test_listen_for_container_events(web_server, docker_client):
    web_server.config = {"docker_swarm": "ignore"}
    listener = DockerEventListener(web_server, docker_client, docker_client)

    docker_client.events.return_value = iter(
        [
            {"Type": "container", "Action": "start", "id": "container1"},
            {"Type": "container", "Action": "stop", "id": "container2"},
        ]
    )

    with patch.object(listener, "enqueue") as mock_enqueue:
        t = threading.Thread(target=listener._listen, args=(docker_client,))
        t.daemon = True
        t.start()
        time.sleep(0.1)
        assert mock_enqueue.call_count == 2
        assert all(isinstance(call.args[0], ContainerEvent) for call in mock_enqueue.call_args_list)
        docker_client.events.return_value = iter([])
        t.join(timeout=0.1)


def test_listen_start_log_includes_swarm_mode(web_server, docker_client):
    web_server.config = {"docker_swarm": "prefer-local"}
    docker_client.api.base_url = "http+docker://localhost"
    docker_client.events.return_value = iter([])
    listener = DockerEventListener(web_server, docker_client, docker_client)

    with patch("builtins.print") as mock_print:
        listener._listen(docker_client)

    mock_print.assert_any_call(
        "Starting Docker event listener loop for client http+docker://localhost with DOCKER_SWARM=prefer-local"
    )


def test_dispatcher_survives_keyboard_interrupt_from_command(web_server, docker_client):
    listener = DockerEventListener(web_server, docker_client, docker_client)
    processed_after_interrupt = threading.Event()

    def interrupting_command():
        raise KeyboardInterrupt("test interrupt")

    listener.start_dispatcher()
    try:
        listener.enqueue(interrupting_command)
        listener.enqueue(processed_after_interrupt.set)

        assert processed_after_interrupt.wait(timeout=2)
        assert listener.is_dispatcher_running()
    finally:
        listener.stop_dispatcher()


def test_process_service_event_update(web_server: WebServer, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    event = {"Action": "update", "Actor": {"ID": service_id}}

    with patch.object(listener, "_schedule_service_processing") as mock_schedule:
        listener._process_service_event("update", event)

    mock_schedule.assert_called_once_with(service_id, "update", 5, attempt=1)
    swarm_client.services.get.assert_not_called()
    web_server.update_backend.assert_not_called()


def test_process_service_upsert_updates_backend_when_vip_is_reachable(
    web_server: WebServer, docker_client, swarm_client
):
    web_server.networks = {"net1": "frontend", "frontend": "net1"}
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    mock_service = MagicMock()
    mock_service.id = service_id
    mock_service.attrs = {
        "Spec": {
            "Name": "service-name",
            "Labels": {},
            "TaskTemplate": {"ContainerSpec": {"Env": ["VIRTUAL_HOST=service.example.com"]}},
        },
        "Endpoint": {
            "Ports": [{"Protocol": "tcp", "TargetPort": 80}],
            "VirtualIPs": [{"NetworkID": "net1", "Addr": "10.0.0.5/24"}],
        },
    }
    swarm_client.services.get.return_value = mock_service

    listener._process_service_upsert(service_id, "update", attempt=1)

    swarm_client.services.get.assert_called_once_with(service_id)
    web_server.update_backend.assert_called_once()


def test_process_service_upsert_retries_when_service_is_not_found(web_server: WebServer, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    swarm_client.services.get.side_effect = docker.errors.NotFound("missing")

    with patch.object(listener, "_schedule_service_processing") as mock_schedule:
        listener._process_service_upsert(service_id, "create", attempt=1)

    mock_schedule.assert_called_once_with(service_id, "create", 20, attempt=2)
    web_server.update_backend.assert_not_called()


def test_process_service_upsert_retries_when_service_vip_is_not_reachable(
    web_server: WebServer, docker_client, swarm_client
):
    web_server.networks = {"net1": "frontend", "frontend": "net1"}
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    mock_service = MagicMock()
    mock_service.id = service_id
    mock_service.attrs = {
        "Spec": {
            "Name": "service-name",
            "Labels": {},
            "TaskTemplate": {"ContainerSpec": {"Env": ["VIRTUAL_HOST=service.example.com"]}},
        },
        "Endpoint": {
            "Ports": [{"Protocol": "tcp", "TargetPort": 80}],
            "VirtualIPs": [],
        },
    }
    swarm_client.services.get.return_value = mock_service

    with patch.object(listener, "_schedule_service_processing") as mock_schedule:
        listener._process_service_upsert(service_id, "create", attempt=1)

    mock_schedule.assert_called_once_with(service_id, "create", 20, attempt=2)
    web_server.update_backend.assert_not_called()


def test_process_service_event_remove(web_server: WebServer, docker_client, swarm_client):
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    event = {"Action": "remove", "Actor": {"ID": service_id}}

    listener._process_service_event("remove", event)

    web_server.remove_backend.assert_called_once_with(service_id)


def test_swarm_task_container_event_is_ignored_in_enable(web_server, docker_client):
    web_server.config = {"docker_swarm": "enable", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)

    listener._process_container_event(
        "start",
        {
            "Actor": {
                "ID": "container1",
                "Attributes": {"com.docker.swarm.service.id": "service1"},
            }
        },
    )

    web_server.update_backend.assert_not_called()
    docker_client.containers.get.assert_not_called()


def test_swarm_task_container_event_is_processed_in_prefer_local(web_server, docker_client):
    web_server.config = {"docker_swarm": "prefer-local", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.id = "container1"
    container.attrs = {
        "Config": {
            "Env": [],
            "Labels": {"com.docker.swarm.service.id": "service1"},
        },
        "State": {"Status": "running"},
        "Name": "/swarm-task",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_container_event(
        "start",
        {
            "Actor": {
                "ID": "container1",
                "Attributes": {"com.docker.swarm.service.id": "service1"},
            }
        },
    )

    web_server.update_backend.assert_called_once()


def test_swarm_task_container_healthcheck_waits_in_prefer_local(web_server, docker_client):
    web_server.config = {"docker_swarm": "prefer-local", "backend_start_grace_seconds": 0}
    listener = DockerEventListener(web_server, docker_client, docker_client)
    container = MagicMock()
    container.name = "swarm-health-task"
    container.attrs = {
        "Config": {
            "Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]},
            "Labels": {"com.docker.swarm.service.id": "service1"},
        },
        "State": {"Status": "running", "Health": {"Status": "starting"}},
        "Name": "/swarm-health-task",
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    docker_client.containers.get.return_value = container

    listener._process_container_event(
        "start",
        {
            "Actor": {
                "ID": "container1",
                "Attributes": {"com.docker.swarm.service.id": "service1"},
            }
        },
    )

    web_server.update_backend.assert_not_called()
    assert "container1" in listener._waiting_for_healthy


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
    web_server.update_backend.assert_not_called()
    listener.drain_commands()

    web_server.update_backend.assert_called_once()


def test_service_delay_timer_enqueues_upsert_without_calling_webserver(web_server, docker_client, swarm_client):
    web_server.networks = {"net1": "frontend", "frontend": "net1"}
    listener = DockerEventListener(web_server, docker_client, swarm_client)
    service_id = "service1"
    mock_service = MagicMock()
    mock_service.id = service_id
    mock_service.attrs = {
        "Spec": {
            "Name": "service-name",
            "Labels": {},
            "TaskTemplate": {"ContainerSpec": {"Env": ["VIRTUAL_HOST=service.example.com"]}},
        },
        "Endpoint": {
            "Ports": [{"Protocol": "tcp", "TargetPort": 80}],
            "VirtualIPs": [{"NetworkID": "net1", "Addr": "10.0.0.5/24"}],
        },
    }
    swarm_client.services.get.return_value = mock_service

    listener._schedule_service_processing(service_id, "update", 0.01, attempt=1)
    time.sleep(0.05)

    swarm_client.services.get.assert_not_called()
    web_server.update_backend.assert_not_called()

    listener.drain_commands()

    swarm_client.services.get.assert_called_once_with(service_id)
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
        listener._process_container_event(
            "die", {"Actor": {"ID": "container1", "Attributes": {"name": "dying-container"}}}
        )

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
