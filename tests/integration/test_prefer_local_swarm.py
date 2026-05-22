import time
import uuid

import pytest

from nginx.NginxConf import HttpBlock
from tests.helpers.docker_utils import start_backend, stop_backend
from tests.helpers.integration_helpers import get_nginx_config_from_container


@pytest.fixture(scope="session")
def swarm_mode():
    return "prefer-local"


def test_prefer_local_uses_local_swarm_task_primary_and_service_vip_backup(
    nginx_proxy_container,
    docker_client,
    test_network,
):
    virtual_host = f"prefer-local-{uuid.uuid4().hex[:6]}.example.com"
    env = {"VIRTUAL_HOST": f"{virtual_host} -> :8080"}
    backend = start_backend(docker_client, test_network, env, backend_type="service", sleep=False)

    try:
        upstream = None
        config_str = ""
        for _ in range(40):
            config_str = get_nginx_config_from_container(nginx_proxy_container[0])
            config = HttpBlock.parse(config_str)
            upstream = next((u for u in config.upstreams if virtual_host in u.parameters), None)
            if upstream:
                server_directives = upstream.get_directives("server")
                server_values = [" ".join(directive.values) for directive in server_directives]
                has_primary = any("backup" not in values for values in server_values)
                has_backup = any("backup" in values for values in server_values)
                if len(server_directives) == 2 and has_primary and has_backup:
                    break
            time.sleep(1)

        assert upstream is not None, f"Upstream block for {virtual_host} not found. Config:\n{config_str}"
        server_directives = upstream.get_directives("server")
        server_values = [" ".join(directive.values) for directive in server_directives]
        assert len(server_directives) == 2, f"Expected local task plus service VIP. Config:\n{config_str}"
        assert any("backup" not in values for values in server_values), f"Expected local primary. Config:\n{config_str}"
        assert any(
            "backup" in values for values in server_values
        ), f"Expected service VIP backup. Config:\n{config_str}"
        assert "# container:" in config_str
        assert "# service:" in config_str
    finally:
        stop_backend(backend)
