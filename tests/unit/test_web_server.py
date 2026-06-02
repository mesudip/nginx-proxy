import pytest
import os
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta, timezone

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.DockerEventListener import Reload
from nginx_proxy.Host import Host
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


def _static_site_config(hostname="example.com", path="/static/example.com/current"):
    config_data = ProxyConfigData()
    host = Host(hostname, 443, scheme={"https"})
    host.add_container(
        "/",
        BackendTarget(
            id=f"static-site:{hostname}",
            name=hostname,
            path=path,
            backend_type="static_site",
        ),
    )
    config_data.add_host(host)
    return config_data


def _secured_config(hostname, password):
    config_data = ProxyConfigData()
    host = Host(hostname, 80)
    host.add_container(
        "/",
        BackendTarget(id=f"backend-{hostname}", address="172.18.0.2", port=80, path="", backend_type="container"),
    )
    host.update_extras_content("security", {"user": password})
    config_data.add_host(host)
    return config_data


def test_register_static_sites_merges_root_with_existing_path_proxy(web_server):
    hostname = "mixed.example.com"
    existing_host = Host(hostname, 443, scheme={"https"})
    existing_host.add_container(
        "/api",
        BackendTarget(id="api", address="172.18.0.2", port=8080, path="", backend_type="container"),
    )
    web_server.config_data = ProxyConfigData()
    web_server.config_data.add_host(existing_host)

    with patch("nginx_proxy.WebServer.pre_processors.process_static_sites") as process_static_sites:
        process_static_sites.return_value = _static_site_config(hostname)
        web_server._register_static_sites()

    host = web_server.config_data.getHost(hostname, 443)
    assert "/api" in host.locations
    assert "/" in host.locations
    assert host.locations["/"].backends[0].type == "static_site"


def test_register_static_sites_adds_default_ssl_domains(web_server):
    web_server.config["default_ssl_domains"] = ["*.xyz.com"]

    with patch("nginx_proxy.WebServer.pre_processors.process_static_sites") as process_static_sites:
        process_static_sites.return_value = ProxyConfigData()
        web_server._register_static_sites()

    host = web_server.config_data.getHost("*.xyz.com", 443)
    assert host is not None
    assert host.secured is True
    assert "default_server" not in host.extras
    assert host.locations["/"].backends[0].type == "static_site"
    assert host.locations["/"].backends[0].path == "vhosts_template/errors"


def test_register_static_sites_keeps_scanned_static_site_over_default_ssl_domain(web_server):
    web_server.config["default_ssl_domains"] = ["*.xyz.com"]

    with patch("nginx_proxy.WebServer.pre_processors.process_static_sites") as process_static_sites:
        process_static_sites.return_value = _static_site_config("*.xyz.com", "/static/*.xyz.com/current")
        web_server._register_static_sites()

    host = web_server.config_data.getHost("*.xyz.com", 443)
    assert len(host.locations["/"].backends) == 1
    assert host.locations["/"].backends[0].path == "/static/*.xyz.com/current"


def test_register_static_sites_warns_when_existing_root_overrides_static(web_server, capsys):
    hostname = "root-proxy.example.com"
    existing_host = Host(hostname, 443, scheme={"https"})
    existing_host.add_container(
        "/",
        BackendTarget(id="root", address="172.18.0.2", port=8080, path="", backend_type="container"),
    )
    web_server.config_data = ProxyConfigData()
    web_server.config_data.add_host(existing_host)

    with patch("nginx_proxy.WebServer.pre_processors.process_static_sites") as process_static_sites:
        process_static_sites.return_value = _static_site_config(hostname)
        web_server._register_static_sites()

    host = web_server.config_data.getHost(hostname, 443)
    assert len(host.locations["/"].backends) == 1
    assert host.locations["/"].backends[0].type == "container"
    assert "WARNING: Static site root skipped" in capsys.readouterr().err


def test_register_backend_root_warns_and_overrides_existing_static_site(web_server, capsys):
    hostname = "container-root.example.com"
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = _static_site_config(hostname)
    backend = _backend_target("container1", f"https://{hostname} -> :8080", "172.18.0.2", port=8080)

    registered = web_server.register_backend(backend)

    host = web_server.config_data.getHost(hostname, 443)
    assert registered is True
    assert len(host.locations["/"].backends) == 1
    assert host.locations["/"].backends[0].type == "container"
    assert "WARNING: Container route overrides static site root" in capsys.readouterr().err


def test_remove_backend_restores_overridden_static_site_root(web_server):
    hostname = "container-root.example.com"
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = _static_site_config(hostname)
    backend = _backend_target("container1", f"https://{hostname} -> :8080", "172.18.0.2", port=8080)
    web_server.register_backend(backend)

    with (
        patch("nginx_proxy.WebServer.pre_processors.process_static_sites") as process_static_sites,
        patch.object(web_server.throttler, "throttle") as mock_throttle,
    ):
        process_static_sites.return_value = _static_site_config(hostname)
        web_server.remove_backend("container1")

    host = web_server.config_data.getHost(hostname, 443)
    assert len(host.locations["/"].backends) == 1
    assert host.locations["/"].backends[0].type == "static_site"
    mock_throttle.assert_called_once()


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


def test_update_backend_replaces_existing_container_backend_when_requested(web_server):
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = ProxyConfigData()
    existing = _backend_target("container1", "old.example.com", "172.18.0.2")
    updated = _backend_target("container1", "new.example.com", "172.18.0.3")
    web_server.register_backend(existing)

    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        changed = web_server.update_backend(updated, replace_existing=True)

    assert changed is True
    assert web_server.config_data.getHost("old.example.com").isempty()
    assert web_server.config_data.getHost("new.example.com") is not None
    mock_throttle.assert_called_once()


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


def test_update_backend_ignores_candidate_when_nginx_validation_fails(web_server):
    web_server.networks = {"frontend": "frontend-id", "frontend-id": "frontend"}
    web_server.config_data = ProxyConfigData()
    existing = _backend_target("service1", "old.example.com", "10.0.0.2", backend_type="service")
    updated = _backend_target("service1", "new.example.com", "10.0.0.3", backend_type="service")
    web_server.register_backend(existing)
    web_server.nginx.validate_config.return_value = (False, "duplicate directive")

    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        changed = web_server.update_backend(updated)

    assert changed is False
    assert web_server.config_data.getHost("old.example.com") is not None
    assert web_server.config_data.getHost("new.example.com") is None
    assert web_server.config_data.has_backend("service1")
    mock_throttle.assert_not_called()


def test_validation_does_not_overwrite_live_basic_auth_file(web_server):
    hostname = "auth.example.com"
    auth_file = web_server.basic_auth_processor.generate_htpasswd_file(hostname, "_", {"user": "old-password"})
    with open(auth_file) as file:
        original_auth = file.read()
    web_server.nginx.validate_config.return_value = (False, "invalid config")

    valid = web_server._validate_config_data(_secured_config(hostname, "new-password"))

    assert valid is False
    with open(auth_file) as file:
        assert file.read() == original_auth


def test_validation_does_not_mutate_default_server_config(web_server):
    web_server.config["default_server"] = True
    candidate = ProxyConfigData()
    host = Host("candidate-default.example.com", 80)
    host.add_container(
        "/",
        BackendTarget(id="backend-default", address="172.18.0.2", port=80, path="", backend_type="container"),
    )
    host.update_extras_content("default_server", "default_server")
    candidate.add_host(host)
    web_server.nginx.validate_config.return_value = (False, "invalid config")

    valid = web_server._validate_config_data(candidate)

    assert valid is False
    assert web_server.config["default_server"] is True


def test_validation_prepares_missing_selfsigned_certificate_files(web_server, tmpdir):
    hostname = "candidate-ssl.example.com"
    certs_dir = str(tmpdir.mkdir("certs"))
    keys_dir = str(tmpdir.mkdir("private"))
    web_server.config["ssl_certs_dir"] = certs_dir
    web_server.config["ssl_key_dir"] = keys_dir
    web_server.nginx.validate_config.return_value = (True, None)

    candidate = ProxyConfigData()
    host = Host(hostname, 443, scheme={"https"})
    host.add_container(
        "/",
        BackendTarget(id="backend-ssl", address="172.18.0.2", port=80, path="", backend_type="container"),
    )
    candidate.add_host(host)

    def set_ssl_file(hosts, update_watch_domains=True):
        for rendered_host in hosts:
            rendered_host.ssl_file = f"{rendered_host.hostname}.selfsigned"

    web_server.ssl_processor.process_ssl_certificates.side_effect = set_ssl_file

    with patch("nginx_proxy.WebServer.subprocess.run") as mock_run:
        valid = web_server._validate_config_data(candidate)

    assert valid is True
    mock_run.assert_called_once()
    command = mock_run.call_args.args[0]
    assert command[0] == "openssl"
    assert command[command.index("-out") + 1] == os.path.join(certs_dir, f"{hostname}.selfsigned.crt")
    assert command[command.index("-keyout") + 1] == os.path.join(keys_dir, f"{hostname}.selfsigned.key")


def test_rescan_and_reload(web_server):
    with patch.object(web_server, "_do_reload") as mock_reload, patch.object(web_server, "rescan_all_container"):
        web_server.rescan_and_reload(force=True)
        mock_reload.assert_called_once()


def test_reload_force_runs_immediately(web_server):
    with patch.object(web_server.throttler, "throttle") as mock_throttle:
        web_server.reload(force=True)
        mock_throttle.assert_called_once()
        assert mock_throttle.call_args.kwargs["immediate"] is True


def test_enqueue_reload_uses_dispatcher_when_running(web_server):
    dispatcher = MagicMock(return_value=True)
    web_server.set_reload_dispatcher(dispatcher, lambda: False)

    assert web_server.enqueue_reload(force=True) is True

    command = dispatcher.call_args.args[0]
    assert isinstance(command, Reload)
    assert command.force is True


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


def _service(service_id="service1"):
    service = MagicMock()
    service.id = service_id
    service.attrs = {
        "Spec": {
            "Name": service_id,
            "Labels": {},
            "TaskTemplate": {"ContainerSpec": {"Env": ["VIRTUAL_HOST=service.example.com"]}},
        },
        "Endpoint": {
            "Ports": [{"Protocol": "tcp", "TargetPort": 80}],
            "VirtualIPs": [{"NetworkID": "network1", "Addr": "10.0.0.5/24"}],
        },
    }
    return service


def _starting_healthcheck_container_with_labels(labels):
    container = _running_container_with_labels(labels)
    container.attrs["Config"]["Healthcheck"] = {"Test": ["CMD", "curl", "-f", "http://localhost/health"]}
    container.attrs["State"]["Health"] = {"Status": "starting"}
    return container


@pytest.mark.parametrize(
    "swarm_mode, expected_backend_ids",
    [
        ("ignore", ["standalone", "task"]),
        ("exclude", ["standalone"]),
        ("enable", ["standalone", "service1"]),
        ("prefer-local", ["standalone", "task", "service1"]),
        ("strict", ["service1"]),
    ],
)
def test_rescan_all_container_matches_swarm_mode_matrix(web_server, swarm_mode, expected_backend_ids):
    web_server.config["docker_swarm"] = swarm_mode
    standalone = _running_container_with_labels({})
    standalone.id = "standalone"
    task = _running_container_with_labels({"com.docker.swarm.service.id": "service1"})
    task.id = "task"
    web_server.client.containers.list.return_value = [standalone, task]
    web_server.swarm_client.info.return_value = {"Swarm": {"LocalNodeState": "active", "ControlAvailable": True}}
    web_server.swarm_client.services.list.return_value = [_service()]

    with patch.object(web_server, "register_backend") as mock_register:
        web_server.rescan_all_container(bypass_start_grace=True)

    backend_ids = [call.args[0].id for call in mock_register.call_args_list]
    assert backend_ids == expected_backend_ids


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
