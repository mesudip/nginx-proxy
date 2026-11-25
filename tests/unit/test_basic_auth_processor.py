import pytest

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.pre_processors.basic_auth_processor import process_basic_auth

CREDENTIALS = {"admin": "secret-password"}


def _https_only(hostname="docs.example.com"):
    return {hostname: {443: Host(hostname, 443, scheme={"https"})}}


def _http_and_https(hostname="docs.example.com"):
    return {
        hostname: {
            80: Host(hostname, 80),
            443: Host(hostname, 443, scheme={"https"}),
        }
    }


def _security(vhost_map, hostname, port):
    return vhost_map[hostname][port].extras.get("security")


def _run(env_value, vhost_map, env_key="PROXY_BASIC_AUTH"):
    process_basic_auth(BackendTarget(id="id", name="backend"), {env_key: env_value}, vhost_map)


@pytest.mark.parametrize(
    "entry",
    [
        "docs.example.com -> admin:secret-password",
        "https://docs.example.com -> admin:secret-password",
        "docs.example.com:443 -> admin:secret-password",
        "http://docs.example.com -> admin:secret-password",
        "docs.example.com:80 -> admin:secret-password",
    ],
)
def test_auth_applies_to_https_only_vhost(entry):
    vhost_map = _https_only()

    _run(entry, vhost_map)

    assert _security(vhost_map, "docs.example.com", 443) == CREDENTIALS


def test_suffixed_env_key_is_applied():
    vhost_map = _https_only()

    _run("docs.example.com -> admin:secret-password", vhost_map, env_key="PROXY_BASIC_AUTH-1")

    assert _security(vhost_map, "docs.example.com", 443) == CREDENTIALS


def test_host_without_scheme_protects_every_port():
    vhost_map = _http_and_https()

    _run("docs.example.com -> admin:secret-password", vhost_map)

    assert _security(vhost_map, "docs.example.com", 80) == CREDENTIALS
    assert _security(vhost_map, "docs.example.com", 443) == CREDENTIALS


def test_http_scheme_only_protects_port_80():
    vhost_map = _http_and_https()

    _run("http://docs.example.com -> admin:secret-password", vhost_map)

    assert _security(vhost_map, "docs.example.com", 80) == CREDENTIALS
    assert _security(vhost_map, "docs.example.com", 443) is None


def test_https_scheme_only_protects_port_443():
    vhost_map = _http_and_https()

    _run("https://docs.example.com -> admin:secret-password", vhost_map)

    assert _security(vhost_map, "docs.example.com", 80) is None
    assert _security(vhost_map, "docs.example.com", 443) == CREDENTIALS


def test_unmatched_port_is_reported_and_not_applied(capsys):
    vhost_map = _https_only()

    _run("docs.example.com:8443 -> admin:secret-password", vhost_map)

    assert _security(vhost_map, "docs.example.com", 443) is None
    assert "ignored" in capsys.readouterr().out


def test_bare_credentials_apply_to_single_vhost():
    vhost_map = _https_only()

    _run("admin:secret-password", vhost_map)

    assert _security(vhost_map, "docs.example.com", 443) == CREDENTIALS
