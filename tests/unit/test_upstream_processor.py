import pytest
from jinja2 import Template

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.post_processors.upstream_processor import UpstreamProcessor


def _backend(id, address, backend_type, port=80, labels=None, env=None):
    return BackendTarget(
        id=id,
        address=address,
        port=port,
        path="",
        name=id,
        env=env or {},
        labels=labels or {},
        backend_type=backend_type,
    )


def test_service_vip_is_backup_when_local_backend_exists():
    host = Host("example.com", 80)
    local_backend = _backend(
        "container1", "172.18.0.2", "container", labels={"com.docker.swarm.service.id": "service1"}
    )
    service_backend = _backend("service1", "10.0.0.5", "service")
    host.add_container("/", local_backend)
    host.add_container("/", service_backend)

    upstreams = UpstreamProcessor().process([host], prefer_local=True)

    assert len(upstreams) == 1
    assert local_backend.backup is False
    assert service_backend.backup is True


def test_service_vip_is_not_backup_for_unmatched_local_swarm_task():
    host = Host("example.com", 80)
    local_backend = _backend(
        "container1", "172.18.0.2", "container", labels={"com.docker.swarm.service.id": "service1"}
    )
    service_backend_with_local_task = _backend("service1", "10.0.0.5", "service")
    service_backend_without_local_task = _backend("service2", "10.0.0.6", "service")
    host.add_container("/", local_backend)
    host.add_container("/", service_backend_with_local_task)
    host.add_container("/", service_backend_without_local_task)

    upstreams = UpstreamProcessor().process([host], prefer_local=True)

    assert len(upstreams) == 1
    assert service_backend_with_local_task.backup is True
    assert service_backend_without_local_task.backup is False


def test_service_vip_backup_uses_local_port_when_service_only_defaulted_to_80():
    host = Host("example.com", 80)
    local_backend = _backend(
        "container1",
        "172.18.0.2",
        "container",
        port=8080,
        labels={"com.docker.swarm.service.id": "service1"},
    )
    service_backend = _backend("service1", "10.0.0.5", "service", port=80)
    host.add_container("/", local_backend)
    host.add_container("/", service_backend)

    upstreams = UpstreamProcessor().process([host], prefer_local=True)

    assert len(upstreams) == 1
    assert local_backend.port == 8080
    assert service_backend.port == 8080
    assert service_backend.backup is True


def test_service_vip_keeps_inferred_port_after_local_backend_is_removed():
    host = Host("example.com", 80)
    local_backend = _backend(
        "container1",
        "172.18.0.2",
        "container",
        port=8080,
        labels={"com.docker.swarm.service.id": "service1"},
    )
    service_backend = _backend("service1", "10.0.0.5", "service", port=80)
    host.add_container("/", local_backend)
    host.add_container("/", service_backend)

    UpstreamProcessor().process([host], prefer_local=True)
    host.locations["/"].remove(local_backend)
    UpstreamProcessor().process([host], prefer_local=True)

    assert host.locations["/"].upstream is False
    assert service_backend.port == 8080
    assert service_backend.backup is False


def test_service_vip_backup_keeps_port_when_local_ports_disagree():
    host = Host("example.com", 80)
    service_backend = _backend("service1", "10.0.0.5", "service", port=80)
    host.add_container(
        "/",
        _backend(
            "container1",
            "172.18.0.2",
            "container",
            port=8080,
            labels={"com.docker.swarm.service.id": "service1"},
        ),
    )
    host.add_container(
        "/",
        _backend(
            "container2",
            "172.18.0.3",
            "container",
            port=9090,
            labels={"com.docker.swarm.service.id": "service1"},
        ),
    )
    host.add_container("/", service_backend)

    UpstreamProcessor().process([host], prefer_local=True)

    assert service_backend.port == 80
    assert service_backend.backup is True


def test_service_vip_is_not_backup_outside_prefer_local():
    host = Host("example.com", 80)
    local_backend = _backend("container1", "172.18.0.2", "container")
    service_backend = _backend("service1", "10.0.0.5", "service")
    host.add_container("/", local_backend)
    host.add_container("/", service_backend)

    UpstreamProcessor().process([host])

    assert local_backend.backup is False
    assert service_backend.backup is False


def test_service_vip_only_backend_is_direct_in_prefer_local():
    host = Host("example.com", 80)
    service_backend = _backend("service1", "10.0.0.5", "service")
    host.add_container("/", service_backend)

    upstreams = UpstreamProcessor().process([host], prefer_local=True)

    assert upstreams == []
    assert host.locations["/"].upstream is False
    assert service_backend.backup is False


def test_backup_service_vip_is_rendered_in_upstream():
    host = Host("example.com", 80)
    host.add_container(
        "/", _backend("container1", "172.18.0.2", "container", labels={"com.docker.swarm.service.id": "service1"})
    )
    host.add_container("/", _backend("service1", "10.0.0.5", "service"))
    upstreams = UpstreamProcessor().process([host], prefer_local=True)

    with open("vhosts_template/default.conf.jinja2") as template_file:
        rendered = Template(template_file.read()).render(
            virtual_servers=[],
            upstreams=upstreams,
            config={"client_max_body_size": "1m", "default_server": False},
        )

    assert "server  172.18.0.2:80;" in rendered
    assert "server  10.0.0.5:80 backup;" in rendered
    assert "# container: container1" in rendered
    assert "# service: service1" in rendered


def test_upstream_id_uses_hyphen_separator():
    host = Host("example.com", 80)
    host.add_container("/", _backend("container1", "172.18.0.2", "container"))
    host.add_container("/", _backend("container2", "172.18.0.3", "container"))

    upstreams = UpstreamProcessor().process([host])

    assert len(upstreams) == 1
    upstream_id = upstreams[0]["id"]
    assert upstream_id.startswith("example.com-")
    assert "example.com_" not in upstream_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("false", None),
        (" FALSE ", None),
        ("true", "ip_hash"),
        (" TRUE ", "ip_hash"),
        ("ip_hash", "ip_hash"),
        ("hash $cookie_sessionid consistent", "hash $cookie_sessionid consistent"),
    ],
)
def test_sticky_session_value(value, expected):
    env = {} if value is None else {"NGINX_STICKY_SESSION": value}

    assert UpstreamProcessor._sticky_value([_backend("one", "172.18.0.2", "container", env=env)]) == expected


@pytest.mark.parametrize(
    ("value", "expected_directive"),
    [
        ("true", "ip_hash;"),
        ("ip_hash", "ip_hash;"),
        ("hash $cookie_sessionid consistent", "hash $cookie_sessionid consistent;"),
        ("false", None),
    ],
)
def test_sticky_session_is_rendered_in_upstream(value, expected_directive):
    host = Host("sticky.example.com", 80)
    env = {"NGINX_STICKY_SESSION": value}
    host.add_container("/", _backend("one", "172.18.0.2", "container", env=env))
    host.add_container("/", _backend("two", "172.18.0.3", "container", env=env))

    upstreams = UpstreamProcessor().process([host])

    with open("vhosts_template/default.conf.jinja2") as template_file:
        rendered = Template(template_file.read()).render(
            virtual_servers=[],
            upstreams=upstreams,
            config={"client_max_body_size": "1m", "default_server": False},
        )

    if expected_directive is None:
        assert "ip_hash;" not in rendered
        assert "hash $cookie_sessionid consistent;" not in rendered
    else:
        assert expected_directive in rendered


def test_prefer_local_sticky_session_uses_local_primaries_without_vip_backup():
    host = Host("sticky.example.com", 80)
    env = {"NGINX_STICKY_SESSION": "true"}
    labels = {"com.docker.swarm.service.id": "service1"}
    host.add_container("/", _backend("task1", "172.18.0.2", "container", labels=labels, env=env))
    host.add_container("/", _backend("task2", "172.18.0.3", "container", labels=labels, env=env))
    host.add_container("/", _backend("service1", "10.0.0.5", "service", env=env))

    upstream = UpstreamProcessor().process([host], prefer_local=True)[0]

    assert upstream["sticky"] == "ip_hash"
    assert [backend.id for backend in upstream["containers"]] == ["task1", "task2"]
    assert all(not backend.backup for backend in upstream["containers"])


def test_prefer_local_single_primary_keeps_vip_backup_without_sticky_session():
    host = Host("sticky.example.com", 80)
    env = {"NGINX_STICKY_SESSION": "true"}
    labels = {"com.docker.swarm.service.id": "service1"}
    host.add_container("/", _backend("task1", "172.18.0.2", "container", labels=labels, env=env))
    host.add_container("/", _backend("service1", "10.0.0.5", "service", env=env))

    upstream = UpstreamProcessor().process([host], prefer_local=True)[0]

    assert upstream["sticky"] is None
    assert [backend.id for backend in upstream["containers"]] == ["task1", "service1"]
    assert upstream["containers"][1].backup is True
