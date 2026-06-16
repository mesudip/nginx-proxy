import time

from nginx.NginxConf import HttpBlock

from tests.helpers.docker_test_client import DockerTestClient
from tests.unit.test_webserver_events import create_webserver


def _render_location(container_name: str, hostname: str, virtual_host: str):
    docker_client = DockerTestClient()
    webserver_gen = create_webserver(docker_client)
    webserver = next(webserver_gen)

    try:
        docker_client.containers.run(
            "nginx:alpine",
            name=container_name,
            environment={"VIRTUAL_HOST": virtual_host},
            network="frontend",
        )
        time.sleep(0.2)

        config = HttpBlock.parse(webserver.nginx.current_config)
        server = next(s for s in config.servers if hostname in s.server_names)
        return next(l for l in server.locations if l.path == "/")
    finally:
        try:
            next(webserver_gen)
        except StopIteration:
            pass


def test_http_location_without_proxy_set_header_keeps_global_config():
    location = _render_location(
        container_name="plain_http_container",
        hostname="plain-http.example.com",
        virtual_host="plain-http.example.com",
    )

    assert location.proxy_set_headers == []


def test_websocket_only_location_reinserts_required_proxy_headers():
    location = _render_location(
        container_name="ws_only_container",
        hostname="ws-only.example.com",
        virtual_host="ws://ws-only.example.com -> :8080",
    )
    headers = dict(location.proxy_set_headers)

    assert headers["Host"] == "$http_host"
    assert headers["Connection"] == "$connection_upgrade"
    assert headers["Upgrade"] == "$http_upgrade"
    assert headers["X-Real-IP"] == "$remote_addr"
    assert headers["X-Forwarded-For"] == "$proxy_add_x_forwarded_for"
    assert headers["X-Forwarded-Proto"] == "$proxy_x_forwarded_proto"
    assert headers["X-Forwarded-Ssl"] == "$proxy_x_forwarded_ssl"
    assert headers["X-Forwarded-Port"] == "$proxy_x_forwarded_port"
    assert headers["Proxy"] == '""'


def test_location_with_injected_host_override_skips_default_host():
    location = _render_location(
        container_name="custom_host_container",
        hostname="custom-host.example.com",
        virtual_host="custom-host.example.com; proxy_set_header Host custom.example",
    )
    headers = location.proxy_set_headers
    header_values = {(header, value) for header, value in headers}

    assert ("Host", "custom.example") in header_values
    assert ("Host", "$http_host") not in header_values
    assert ("X-Real-IP", "$remote_addr") in header_values
    assert ("X-Forwarded-For", "$proxy_add_x_forwarded_for") in header_values
    assert ("Proxy", '""') in header_values


def test_location_with_unrelated_proxy_set_header_reinserts_defaults():
    location = _render_location(
        container_name="custom_header_container",
        hostname="custom-header.example.com",
        virtual_host="custom-header.example.com; proxy_set_header X-Foo bar",
    )
    headers = dict(location.proxy_set_headers)

    assert headers["X-Foo"] == "bar"
    assert headers["Host"] == "$http_host"
    assert headers["X-Real-IP"] == "$remote_addr"
    assert headers["X-Forwarded-For"] == "$proxy_add_x_forwarded_for"
    assert headers["X-Forwarded-Proto"] == "$proxy_x_forwarded_proto"
    assert headers["X-Forwarded-Ssl"] == "$proxy_x_forwarded_ssl"
    assert headers["X-Forwarded-Port"] == "$proxy_x_forwarded_port"
    assert headers["Proxy"] == '""'


def test_location_with_lowercase_host_override_is_treated_case_insensitively():
    location = _render_location(
        container_name="lowercase_host_container",
        hostname="lowercase-host.example.com",
        virtual_host="lowercase-host.example.com; proxy_set_header host custom.example",
    )
    headers = location.proxy_set_headers

    assert any(header.lower() == "host" and value == "custom.example" for header, value in headers)
    assert not any(header == "Host" and value == "$http_host" for header, value in headers)
