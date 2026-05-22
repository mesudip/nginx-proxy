import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta, timezone

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.ProxyConfigData import ProxyConfigData
from nginx_proxy.WebServer import WebServer


@pytest.fixture
def mock_config(tmpdir):
    return {
        "dummy_nginx": True,
        "conf_dir": str(tmpdir.mkdir("nginx")),
        "challenge_dir": str(tmpdir.mkdir("challenges")),
        "vhosts_template_dir": "vhosts_template",
        "ssl_dir": str(tmpdir.mkdir("ssl")),
        "cert_renew_threshold_days": 30,
        "docker_swarm": "ignore",
    }


@pytest.fixture
def web_server(mock_config):
    with (
        patch("builtins.open", mock_open(read_data="template_content")),
        patch("nginx_proxy.WebServer.DummyNginx"),
        patch("nginx_proxy.post_processors.SslCertificateProcessor"),
    ):
        server = WebServer(MagicMock(), mock_config, swarm_client=MagicMock())
        return server


def test_init(web_server):
    assert web_server.client is not None
    assert web_server.swarm_client is not None


def test_init_bypasses_startup_grace_on_initial_rescan(mock_config):
    with (
        patch("builtins.open", mock_open(read_data="template_content")),
        patch("nginx_proxy.WebServer.DummyNginx"),
        patch("nginx_proxy.post_processors.SslCertificateProcessor"),
        patch.object(WebServer, "rescan_and_reload") as mock_rescan,
    ):
        WebServer(MagicMock(), mock_config, swarm_client=MagicMock())

    mock_rescan.assert_called_once_with(force=True, bypass_start_grace=True)


def test_learn_yourself_in_container(web_server):
    with (
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=".../docker/abcde...")),
        patch("os.getenv", return_value="my-container-id"),
    ):
        container_mock = MagicMock()
        container_mock.attrs = {"NetworkSettings": {"Networks": {"net1": {"NetworkID": "net1-id"}}}}
        container_mock.id = "my-container-id"
        web_server.client.containers.get.return_value = container_mock

        network_mock = MagicMock()
        network_mock.id = "net1-id"
        network_mock.name = "net1"
        web_server.client.networks.get.return_value = network_mock

        web_server.learn_yourself()
        assert "net1-id" in web_server.networks
        assert web_server.networks["net1-id"] == "net1"
        assert web_server.networks["net1"] == "net1-id"


def test_register_backend(web_server):
    backend = MagicMock()
    backend.id = "container1"
    backend.name = "container_name"
    backend.type = "container"
    backend.env = {"VIRTUAL_HOST": "example.com"}
    # Mock backend to simulate network attachment
    backend.network_settings = {"net1": {"NetworkID": "net1-id", "IPAddress": "172.18.0.2"}}

    # Mock web_server.networks to include the network the backend is on
    web_server.networks = {"net1": "net1-id", "net1-id": "net1"}

    # Mock config_data to verify add_host is called
    web_server.config_data = MagicMock()

    # register_backend doesn't trigger reload/throttle on its own
    ret = web_server.register_backend(backend)

    # Check if add_host was called (register_backend calls it for valid hosts)
    # Since we are not mocking pre_processors, we assume MagicMock backend is enough for process_virtual_hosts to return something
    # if VIRTUAL_HOST is present.
    # Check if it returned True implies it found hosts
    assert ret is True
    web_server.config_data.add_host.assert_called()


def test_remove_backend(web_server):
    config_data = MagicMock()
    config_data.remove_backend.return_value = (MagicMock(name="deleted_backend"), "deleted.domain")
    web_server.config_data = config_data

    with patch.object(web_server.throttler, "throttle") as mock_run:
        web_server.remove_backend("container1")
        mock_run.assert_called_once()
        config_data.remove_backend.assert_called_with("container1")


def _backend_target(backend_id, hostname, address, port=80, backend_type="container"):
    return BackendTarget(
        id=backend_id,
        name=backend_id,
        env={"VIRTUAL_HOST": hostname},
        network_settings={"frontend": {"NetworkID": "frontend-id", "IPAddress": address}},
        ports={f"{port}/tcp": None},
        backend_type=backend_type,
    )


def test_update_backend_ignores_existing_container_backend(web_server):
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = ProxyConfigData()
    existing = _backend_target("container1", "old.example.com", "172.18.0.2")
    updated = _backend_target("container1", "new.example.com", "172.18.0.3")
    web_server.register_backend(existing)

    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        changed = web_server.update_backend(updated)

    assert changed is False
    assert web_server.config_data.getHost("old.example.com") is not None
    assert web_server.config_data.getHost("new.example.com") is None
    mock_throttle.assert_not_called()


def test_update_backend_replaces_existing_service_backend(web_server):
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = ProxyConfigData()
    existing = _backend_target("service1", "old.example.com", "10.0.0.2", backend_type="service")
    updated = _backend_target("service1", "new.example.com", "10.0.0.3", port=8080, backend_type="service")
    updated.env["VIRTUAL_PORT"] = "8080"
    web_server.register_backend(existing)

    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        changed = web_server.update_backend(updated)

    assert changed is True
    assert web_server.config_data.getHost("old.example.com").isempty()
    new_host = web_server.config_data.getHost("new.example.com")
    assert new_host is not None
    backend = new_host.locations["/"].backends[0]
    assert backend.address == "10.0.0.3"
    assert backend.port == "8080"
    mock_throttle.assert_called_once()


def test_update_backend_removes_existing_service_when_updated_config_is_invalid(web_server):
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = ProxyConfigData()
    existing = _backend_target("service1", "old.example.com", "10.0.0.2", backend_type="service")
    invalid = BackendTarget(
        id="service1",
        name="service1",
        env={},
        network_settings={"frontend": {"NetworkID": "frontend-id", "IPAddress": "10.0.0.3"}},
        backend_type="service",
    )
    web_server.register_backend(existing)

    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        changed = web_server.update_backend(invalid)

    assert changed is True
    assert web_server.config_data.getHost("old.example.com").isempty()
    assert not web_server.config_data.has_backend("service1")
    mock_throttle.assert_called_once()


def test_rescan_and_reload(web_server):
    with patch.object(web_server, "_do_reload") as mock_reload, patch.object(web_server, "rescan_all_container"):
        web_server.rescan_and_reload(force=True)
        mock_reload.assert_called_once()


def test_reload_force_runs_immediately(web_server):
    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        web_server.reload(force=True)
        mock_throttle.assert_called_once()
        assert mock_throttle.call_args.kwargs["immediate"] is True


def test_should_register_container_now_skips_unhealthy_healthcheck(web_server):
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Config": {"Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}},
        "State": {"Status": "running", "Health": {"Status": "starting"}},
    }

    assert web_server._should_register_container_now(container) is False


def test_should_register_container_now_honors_startup_grace(web_server):
    web_server.config["backend_start_grace_seconds"] = 10
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Config": {},
        "State": {
            "Status": "running",
            "StartedAt": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
        },
    }

    assert web_server._should_register_container_now(container) is False


def test_should_register_container_now_can_bypass_startup_grace(web_server):
    web_server.config["backend_start_grace_seconds"] = 10
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Config": {},
        "State": {
            "Status": "running",
            "StartedAt": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
        },
    }

    assert web_server._should_register_container_now(container, bypass_start_grace=True) is True


def test_connect_skips_unhealthy_healthchecked_container(web_server):
    web_server.networks = {"network1": "frontend", "frontend": "network1"}
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Config": {"Healthcheck": {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}, "Labels": {}},
        "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
    }
    web_server.client.containers.get.return_value = container

    with patch.object(web_server, "update_backend") as mock_update_backend:
        web_server.connect("network1", "container1", "local")

    mock_update_backend.assert_not_called()


def _running_container_with_labels(labels):
    container = MagicMock()
    container.status = "running"
    container.attrs = {
        "Config": {"Env": ["VIRTUAL_HOST=example.com"], "Labels": labels},
        "State": {"Status": "running"},
        "Name": "/backend",
        "NetworkSettings": {
            "Networks": {"frontend": {"NetworkID": "network1", "IPAddress": "172.18.0.2"}},
            "Ports": {"80/tcp": None},
        },
    }
    return container


def _starting_healthcheck_container_with_labels(labels):
    container = _running_container_with_labels(labels)
    container.attrs["Config"]["Healthcheck"] = {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}
    container.attrs["State"]["Health"] = {"Status": "starting"}
    return container


def test_rescan_skips_swarm_task_container_in_enable(web_server):
    web_server.config["docker_swarm"] = "enable"
    web_server.client.containers.list.return_value = [
        _running_container_with_labels({"com.docker.swarm.service.id": "service1"})
    ]
    web_server.swarm_client.info.return_value = {"Swarm": {"LocalNodeState": "inactive"}}

    with patch.object(web_server, "register_backend") as mock_register:
        web_server.rescan_all_container(bypass_start_grace=True)

    mock_register.assert_not_called()


def test_rescan_includes_swarm_task_container_in_prefer_local(web_server):
    web_server.config["docker_swarm"] = "prefer-local"
    web_server.client.containers.list.return_value = [
        _running_container_with_labels({"com.docker.swarm.service.id": "service1"})
    ]
    web_server.swarm_client.info.return_value = {"Swarm": {"LocalNodeState": "inactive"}}

    with patch.object(web_server, "register_backend") as mock_register:
        web_server.rescan_all_container(bypass_start_grace=True)

    mock_register.assert_called_once()


def test_rescan_prefer_local_keeps_health_gate_for_swarm_task(web_server):
    web_server.config["docker_swarm"] = "prefer-local"
    web_server.client.containers.list.return_value = [
        _starting_healthcheck_container_with_labels({"com.docker.swarm.service.id": "service1"})
    ]
    web_server.swarm_client.info.return_value = {"Swarm": {"LocalNodeState": "inactive"}}

    with patch.object(web_server, "register_backend") as mock_register:
        web_server.rescan_all_container(bypass_start_grace=True)

    mock_register.assert_not_called()
