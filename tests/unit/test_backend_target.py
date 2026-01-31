import pytest
from unittest.mock import MagicMock
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.pre_processors.virtual_host_processor import (
    host_generator,
    process_virtual_hosts,
    _parse_host_entry,
)
from nginx_proxy.ProxyConfigData import ProxyConfigData


class TestBackendTarget:
    def test_backend_target_init_defaults(self):
        bt = BackendTarget(id="123")
        assert bt.id == "123"
        assert bt.scheme == "http"
        assert bt.env == {}
        assert bt.labels == {}
        assert bt.network_settings == {}
        assert bt.ports == {}

    def test_backend_target_from_container(self):
        container = MagicMock()
        container.id = "abc123456789"
        container.attrs = {
            "Name": "/test-container",
            "Config": {"Env": ["VIRTUAL_HOST=example.com", "FOO=bar"], "Labels": {"com.example.label": "value"}},
            "NetworkSettings": {
                "Networks": {"net1": {"NetworkID": "net1-id", "IPAddress": "172.18.0.2"}},
                "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}]},
            },
        }

        bt = BackendTarget.from_container(container)

        assert bt.id == "abc123456789"
        assert bt.name == "test-container"
        assert bt.env["VIRTUAL_HOST"] == "example.com"
        assert bt.env["FOO"] == "bar"
        assert bt.labels["com.example.label"] == "value"
        assert bt.network_settings["net1"]["NetworkID"] == "net1-id"
        assert bt.network_settings["net1"]["IPAddress"] == "172.18.0.2"
        assert "80/tcp" in bt.ports


class TestBackendTargetFromService:
    def test_from_service(self):
        service = MagicMock()
        service.id = "service123"
        service.attrs = {
            "Spec": {
                "Name": "my-web-service",
                "Labels": {"com.example.foo": "bar"},
                "TaskTemplate": {
                    "ContainerSpec": {
                        "Env": ["VIRTUAL_HOST=web.service.local", "DB_HOST=db.local"]
                    }
                }
            },
            "Endpoint": {
                "Ports": [
                    {"Protocol": "tcp", "TargetPort": 80, "PublishedPort": 8080}
                ],
                "VirtualIPs": [
                    {"NetworkID": "net1", "Addr": "10.0.0.5/24"},
                    {"NetworkID": "net2", "Addr": "192.168.1.5/24"}
                ]
            }
        }

        bt = BackendTarget.from_service(service)

        assert bt.id == "service123"
        assert bt.name == "my-web-service"
        assert bt.type == "service"
        assert bt.env["VIRTUAL_HOST"] == "web.service.local"
        assert bt.labels["com.example.foo"] == "bar"
        assert "net1" in bt.network_settings
        assert bt.network_settings["net1"]["IPAddress"] == "10.0.0.5"
        assert "80/tcp" in bt.ports


class TestVirtualHostProcessorWithBackendTarget:
    def test_host_generator_with_backend_target(self):
        # Create a backend target manually (simulating what DockerEventListener might do for Swarm)
        bt = BackendTarget(
            id="swarm-service-id",
            name="my-service",
            env={"VIRTUAL_HOST": "service.local"},
            network_settings={"my-net": {"NetworkID": "net-id-1", "IPAddress": "10.0.0.5"}},
        )

        known_networks = {"net-id-1"}

        # We need to mock the fact that the backend is on a reachable network
        # The logic in virtual_host_processor checks backend networks against known_networks
        # But wait, add_network logic happens inside host_generator (which we are testing)?
        # Yes, host_generator calls backend.add_network() based on its network_settings.

        # Original code assumed container.attrs["NetworkSettings"]["Networks"]
        # My updated code uses backend.network_settings

        generator = host_generator(bt, known_networks=known_networks)

        results = list(generator)
        assert len(results) == 1

        host, location, backend_data, extras = results[0]

        assert host.hostname == "service.local"
        assert backend_data.id == "swarm-service-id"
        assert backend_data.address == "10.0.0.5"  # Should be picked up from network settings

    def test_process_virtual_hosts_integration(self):
        bt = BackendTarget(
            id="integration-id",
            name="integration-test",
            env={"VIRTUAL_HOST": "int.test", "VIRTUAL_PORT": "8080"},
            network_settings={"int-net": {"NetworkID": "int-net-id", "IPAddress": "10.0.0.99"}},
        )
        known_networks = {"int-net-id"}

        config_data = process_virtual_hosts(bt, known_networks)

        assert isinstance(config_data, ProxyConfigData)
        hosts = list(config_data.host_list())
        assert len(hosts) == 1
        assert hosts[0].hostname == "int.test"


    def test_duplicate_injected_directives_are_deduplicated(self):
        known_networks = {"shared-net-id"}

        backends = [
            BackendTarget(
                id="dup-one",
                name="dup-one",
                env={"VIRTUAL_HOST": "dup.example.com; client_max_body_size 200M;"},
                network_settings={"shared": {"NetworkID": "shared-net-id", "IPAddress": "10.0.0.11"}},
            ),
            BackendTarget(
                id="dup-two",
                name="dup-two",
                env={"VIRTUAL_HOST": "dup.example.com; client_max_body_size 200M;proxy_read_timeout 100;"},
                network_settings={"shared": {"NetworkID": "shared-net-id", "IPAddress": "10.0.0.12"}},
            ),
            BackendTarget(
                id="dup-three",
                name="dup-three",
                env={"VIRTUAL_HOST": "dup.example.com; client_max_body_size 400M"},
                network_settings={"shared": {"NetworkID": "shared-net-id", "IPAddress": "10.0.0.12"}},
            ),
        ]

        aggregated = ProxyConfigData()
        for backend in backends:
            config = process_virtual_hosts(backend, known_networks)
            for host in config.host_list():
                aggregated.add_host(host)

        host = aggregated.getHost("dup.example.com")
        injections = host.locations["/"].extras.get("injected", [])
        assert injections.count("client_max_body_size 200M") == 1


    def test_process_virtual_hosts_no_virtual_host(self):
        bt = BackendTarget(
            id="no-host-id",
            name="no-host-test",
            env={},
            network_settings={"int-net": {"NetworkID": "int-net-id", "IPAddress": "10.0.0.99"}},
        )
        known_networks = {"int-net-id"}

        config_data = process_virtual_hosts(bt, known_networks)
        assert len(list(config_data.host_list())) == 0

    def test_process_virtual_hosts_unreachable_network(self):
        bt = BackendTarget(
            id="unreachable-id",
            name="unreachable-test",
            env={"VIRTUAL_HOST": "unreachable.test"},
            network_settings={"other-net": {"NetworkID": "other-net-id", "IPAddress": "10.0.0.98"}},
        )
        known_networks = {"my-net-id"}

        config_data = process_virtual_hosts(bt, known_networks)
        assert len(list(config_data.host_list())) == 0

    def test_parse_host_entry_simple(self):
        h, loc, c, extras = _parse_host_entry("example.com")
        assert h.hostname == "example.com"
        assert loc == "/"
        assert c.scheme == "http"
        assert len(extras) == 0

    def test_parse_host_entry_with_port_and_path(self):
        h, loc, c, extras = _parse_host_entry("https://example.com:8443/foo -> http://backend:8080/bar")
        assert h.hostname == "example.com"
        assert h.port == 8443
        assert "https" in h.scheme
        assert loc == "/foo"
        assert c.scheme == "http"
        assert c.address == "backend"
        assert c.port == 8080
        assert c.path == "/bar"

    def test_parse_host_entry_with_extras(self):
        h, loc, c, extras = _parse_host_entry("example.com; client_max_body_size 100M; proxy_read_timeout 120")
        assert h.hostname == "example.com"
        assert extras["client_max_body_size"] == "100M"
        assert extras["proxy_read_timeout"] == "120"
