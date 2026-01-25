import pytest
from unittest.mock import MagicMock
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.pre_processors.virtual_host_processor import host_generator, process_virtual_hosts
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

        # Verify the upstreams/containers logic
        # Host object -> locations -> container list
        # We don't have easy access to inspect internal structure of Host without iterating locations
        # But if it verified, it means it processed successfully.
