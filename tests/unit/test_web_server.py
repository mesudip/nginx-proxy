import pytest
from unittest.mock import MagicMock, patch, mock_open

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
        "docker_swarm": "ignore"
    }


@pytest.fixture
def web_server(mock_config):
    with patch('builtins.open', mock_open(read_data="template_content")), \
         patch('nginx_proxy.WebServer.DummyNginx'), \
         patch('nginx_proxy.post_processors.SslCertificateProcessor'):
        server = WebServer(MagicMock(), mock_config, swarm_client=MagicMock())
        return server


def test_init(web_server):
    assert web_server.client is not None
    assert web_server.swarm_client is not None


def test_learn_yourself_in_container(web_server):
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data=".../docker/abcde...")), \
         patch('os.getenv', return_value="my-container-id"):
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
    backend.network_settings = {
        "net1": {
            "NetworkID": "net1-id",
            "IPAddress": "172.18.0.2"
        }
    }

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

    with patch.object(web_server.throttler, 'throttle') as mock_run:
        web_server.remove_backend("container1")
        mock_run.assert_called_once()
        config_data.remove_backend.assert_called_with("container1")


def test_rescan_and_reload(web_server):
    with patch.object(web_server, '_do_reload') as mock_reload, \
         patch.object(web_server, 'rescan_all_container'):
        web_server.rescan_and_reload(force=True)
        mock_reload.assert_called_once()
