import time
import uuid

import pytest

from nginx.NginxConf import HttpBlock
from tests.helpers.docker_utils import start_backend, stop_backend
from tests.helpers.integration_helpers import get_nginx_config_from_container

COOKIE_HASH = "hash $cookie_sessionid consistent"


def _wait_for_upstream(nginx_proxy_container, virtual_host, server_count, sticky_directive, timeout=60):
    deadline = time.monotonic() + timeout
    config_str = ""
    while time.monotonic() < deadline:
        config_str = get_nginx_config_from_container(nginx_proxy_container)
        config = HttpBlock.parse(config_str)
        upstream = next((item for item in config.upstreams if virtual_host in item.parameters), None)
        if upstream is not None and len(upstream.get_directives("server")) == server_count:
            has_ip_hash = bool(upstream.get_directives("ip_hash"))
            hash_directives = upstream.get_directives("hash")
            has_cookie_hash = any(
                " ".join(directive.values) == "$cookie_sessionid consistent" for directive in hash_directives
            )
            if sticky_directive == "ip_hash" and has_ip_hash:
                return upstream, config_str
            if sticky_directive == COOKIE_HASH and has_cookie_hash:
                return upstream, config_str
            if sticky_directive is None and not has_ip_hash and not hash_directives:
                return upstream, config_str
        time.sleep(1)

    pytest.fail(
        f"Expected {server_count} servers with sticky={sticky_directive!r} "
        f"for {virtual_host}. Config:\n{config_str}"
    )


def _request_identity(nginx_request, url, *, header="X-Test-Backend-ID", cookies=None, timeout=30):
    deadline = time.monotonic() + timeout
    last_response = None
    while time.monotonic() < deadline:
        try:
            last_response = nginx_request.get(url, cookies=cookies, timeout=2)
            identity = last_response.headers.get(header)
            if last_response.status_code == 200 and identity:
                return identity
        except Exception:
            pass
        time.sleep(1)
    status = None if last_response is None else last_response.status_code
    pytest.fail(f"Backend identity was not returned by {url}; last status={status}")


def _sticky_env(virtual_host, sticky_value, backend_id=None):
    env = {
        "VIRTUAL_HOST": f"{virtual_host} -> :8080",
        "NGINX_STICKY_SESSION": sticky_value,
    }
    if backend_id is not None:
        env["TEST_BACKEND_ID"] = backend_id
    return env


@pytest.mark.swarm_mode("enable")
@pytest.mark.parametrize(
    ("sticky_value", "expected_directive"),
    [
        ("false", None),
        ("true", "ip_hash"),
        (COOKIE_HASH, COOKIE_HASH),
    ],
)
def test_sticky_sessions_route_real_requests_between_standalone_backends(
    nginx_proxy_container,
    nginx_request,
    docker_client,
    test_network,
    sticky_value,
    expected_directive,
):
    virtual_host = f"sticky-containers-{uuid.uuid4().hex[:8]}.example.com"
    url = f"http://{virtual_host}/identity"
    backends = []
    try:
        for backend_id in ("backend-a", "backend-b"):
            backends.append(
                start_backend(
                    docker_client,
                    test_network,
                    _sticky_env(virtual_host, sticky_value, backend_id),
                    sleep=False,
                )
            )

        _wait_for_upstream(nginx_proxy_container[0], virtual_host, 2, expected_directive)
        fixed_cookies = {"sessionid": "fixed-session"} if sticky_value == COOKIE_HASH else None
        identities = {_request_identity(nginx_request, url, cookies=fixed_cookies) for _ in range(16)}

        if sticky_value == "false":
            assert identities == {"backend-a", "backend-b"}
        else:
            assert len(identities) == 1

        if sticky_value == COOKIE_HASH:
            mappings = {}
            for index in range(32):
                cookie = {"sessionid": f"session-{index}"}
                first = _request_identity(nginx_request, url, cookies=cookie)
                second = _request_identity(nginx_request, url, cookies=cookie)
                assert second == first
                mappings[index] = first
            assert set(mappings.values()) == {"backend-a", "backend-b"}
    finally:
        for backend in backends:
            stop_backend(backend)


@pytest.mark.swarm_mode("strict")
@pytest.mark.parametrize(
    ("sticky_value", "expected_directive"),
    [("true", "ip_hash"), (COOKIE_HASH, COOKIE_HASH)],
)
def test_sticky_sessions_work_across_swarm_services(
    nginx_proxy_container,
    nginx_request,
    docker_client,
    test_network,
    sticky_value,
    expected_directive,
):
    virtual_host = f"sticky-services-{uuid.uuid4().hex[:8]}.example.com"
    url = f"http://{virtual_host}/identity"
    services = []
    try:
        for backend_id in ("service-a", "service-b"):
            services.append(
                start_backend(
                    docker_client,
                    test_network,
                    _sticky_env(virtual_host, sticky_value, backend_id),
                    backend_type="service",
                    sleep=False,
                )
            )

        _wait_for_upstream(nginx_proxy_container[0], virtual_host, 2, expected_directive)
        cookies = {"sessionid": "fixed-service-session"} if sticky_value == COOKIE_HASH else None
        identities = {_request_identity(nginx_request, url, cookies=cookies) for _ in range(16)}

        assert len(identities) == 1
        assert identities <= {"service-a", "service-b"}
    finally:
        for service in services:
            stop_backend(service)


@pytest.mark.swarm_mode("prefer-local")
@pytest.mark.parametrize(
    ("sticky_value", "expected_directive"),
    [("true", "ip_hash"), (COOKIE_HASH, COOKIE_HASH)],
)
def test_prefer_local_sticky_sessions_scale_between_local_tasks_and_vip_backup(
    nginx_proxy_container,
    nginx_request,
    docker_client,
    test_network,
    sticky_value,
    expected_directive,
):
    virtual_host = f"sticky-prefer-local-{uuid.uuid4().hex[:8]}.example.com"
    url = f"http://{virtual_host}/identity"
    service = start_backend(
        docker_client,
        test_network,
        _sticky_env(virtual_host, sticky_value),
        backend_type="service",
        sleep=False,
        replicas=2,
    )
    try:
        upstream, config_str = _wait_for_upstream(
            nginx_proxy_container[0], virtual_host, 2, expected_directive, timeout=90
        )
        assert all("backup" not in " ".join(item.values) for item in upstream.get_directives("server")), config_str

        cookies = {"sessionid": "fixed-local-session"} if sticky_value == COOKIE_HASH else None
        identities = {
            _request_identity(
                nginx_request,
                url,
                header="X-Test-Backend-Hostname",
                cookies=cookies,
            )
            for _ in range(16)
        }
        assert len(identities) == 1

        service.scale(1)
        single_upstream, config_str = _wait_for_upstream(nginx_proxy_container[0], virtual_host, 2, None, timeout=90)
        server_values = [" ".join(item.values) for item in single_upstream.get_directives("server")]
        assert sum("backup" in value for value in server_values) == 1, config_str
        _request_identity(nginx_request, url, header="X-Test-Backend-Hostname", cookies=cookies)

        service.reload()
        service.scale(2)
        restored, config_str = _wait_for_upstream(
            nginx_proxy_container[0], virtual_host, 2, expected_directive, timeout=90
        )
        assert all("backup" not in " ".join(item.values) for item in restored.get_directives("server")), config_str
    finally:
        stop_backend(service)


@pytest.mark.swarm_mode("enable")
def test_ip_hash_fails_over_when_selected_backend_is_removed(
    nginx_proxy_container,
    nginx_request,
    docker_client,
    test_network,
):
    virtual_host = f"sticky-failover-{uuid.uuid4().hex[:8]}.example.com"
    url = f"http://{virtual_host}/identity"
    backends = {}
    try:
        for backend_id in ("backend-a", "backend-b"):
            backends[backend_id] = start_backend(
                docker_client,
                test_network,
                _sticky_env(virtual_host, "true", backend_id),
                sleep=False,
            )

        _wait_for_upstream(nginx_proxy_container[0], virtual_host, 2, "ip_hash")
        selected = _request_identity(nginx_request, url)
        survivor = ({"backend-a", "backend-b"} - {selected}).pop()

        stop_backend(backends.pop(selected))

        deadline = time.monotonic() + 60
        last_status = None
        last_identity = None
        while time.monotonic() < deadline:
            try:
                response = nginx_request.get(url, timeout=2)
                last_status = response.status_code
                last_identity = response.headers.get("X-Test-Backend-ID")
                if response.status_code == 200 and last_identity == survivor:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            pytest.fail(
                f"Traffic did not fail over from {selected} to {survivor}; "
                f"last status={last_status}, last identity={last_identity}"
            )
    finally:
        for backend in backends.values():
            stop_backend(backend)
