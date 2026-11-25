"""PROXY_BASIC_AUTH through the real virtual host processing.

test_basic_auth_processor.py builds the vhost map by hand. These tests let
process_virtual_hosts decide which port each host is stored on, so a change
there (such as https-only hosts moving from port 80 to 443) cannot silently
drop the credentials again.
"""

import pytest

from nginx_proxy import pre_processors
from nginx_proxy.BackendTarget import BackendTarget

CREDENTIALS = {"admin": "secret-password"}


def _protected_hosts(env):
    backend = BackendTarget(
        id="id",
        name="backend",
        env=env,
        network_settings={"frontend": {"NetworkID": "net1", "IPAddress": "10.0.0.5"}},
    )
    hosts = pre_processors.process_virtual_hosts(backend, {"net1"})
    assert len(hosts), "virtual host was not registered"
    pre_processors.process_basic_auth(backend, env, hosts.config_map)
    return hosts.host_list()


@pytest.mark.parametrize(
    "env",
    [
        pytest.param(
            {
                "VIRTUAL_HOST": "https://docs.example.com -> :8080",
                "PROXY_BASIC_AUTH": "docs.example.com -> admin:secret-password",
            },
            id="https-host-named-entry",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "https://docs.example.com -> :8080",
                "PROXY_BASIC_AUTH": "https://docs.example.com -> admin:secret-password",
            },
            id="https-host-https-entry",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "https://docs.example.com -> :8080",
                "PROXY_BASIC_AUTH": "http://docs.example.com -> admin:secret-password",
            },
            id="https-host-http-entry",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "https://docs.example.com -> :8080",
                "PROXY_BASIC_AUTH-1": "docs.example.com -> admin:secret-password",
            },
            id="https-host-suffixed-key",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "docs.example.com -> :8080",
                "LETSENCRYPT_HOST": "docs.example.com",
                "PROXY_BASIC_AUTH": "docs.example.com -> admin:secret-password",
            },
            id="letsencrypt-host-override",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "https://docs.example.com -> :8080",
                "PROXY_BASIC_AUTH": "admin:secret-password",
            },
            id="https-host-bare-credentials",
        ),
        pytest.param(
            {
                "VIRTUAL_HOST": "docs.example.com -> :8080",
                "PROXY_BASIC_AUTH": "docs.example.com -> admin:secret-password",
            },
            id="plain-http-host",
        ),
    ],
)
def test_every_served_host_gets_the_credentials(env):
    hosts = _protected_hosts(env)

    for host in hosts:
        assert host.extras.get("security") == CREDENTIALS, f"{host.hostname}:{host.port} has no basic auth"
