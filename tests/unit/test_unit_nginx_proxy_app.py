import os
import pytest
from unittest.mock import patch, MagicMock

from nginx_proxy.NginxProxyApp import NginxProxyApp


@patch('docker.from_env')
@patch('docker.DockerClient')
def test_load_config_from_env(mock_docker_client, mock_from_env):
    with patch.dict(os.environ, {
        "CERT_RENEW_THRESHOLD_DAYS": "60",
        "DUMMY_NGINX": "true",
        "SSL_DIR": "/custom/ssl",
        "NGINX_CONF_DIR": "/custom/nginx",
        "CLIENT_MAX_BODY_SIZE": "10m",
        "DEFAULT_HOST": "false",
        "ENABLE_IPV6": "true",
        "DOCKER_SWARM": "strict",
        "SWARM_DOCKER_HOST": "tcp://swarm:2375"
    }):
        with patch('sys.exit'):
            app = NginxProxyApp()
            config = app.config
            assert config['cert_renew_threshold_days'] == 60
            assert config['dummy_nginx'] is True
            assert config['ssl_dir'] == '/custom/ssl'
            assert config['conf_dir'] == '/custom/nginx'
            assert config['client_max_body_size'] == '10m'
            assert config['default_server'] is False
            assert config['enable_ipv6'] is True
            assert config['docker_swarm'] == 'strict'
            assert config['swarm_docker_host'] == 'tcp://swarm:2375'


@patch('docker.from_env')
def test_init_docker_client_default(mock_from_env):
    with patch.dict(os.environ, {"SWARM_DOCKER_HOST": ""}):
        app = NginxProxyApp()
        assert mock_from_env.call_count > 0
        assert app.docker_client is not None
        assert app.docker_client == app.swarm_client


@patch('docker.DockerClient')
@patch('docker.from_env')
def test_init_docker_client_with_swarm_host(mock_from_env, mock_docker_client):
    with patch.dict(os.environ, {"SWARM_DOCKER_HOST": "tcp://swarm:2375"}):
        with patch('sys.exit'):
            NginxProxyApp()
            assert mock_docker_client.called
            assert mock_from_env.called


@patch('nginx_proxy.NginxProxyApp.render_nginx_conf')
@patch('os.path.exists', return_value=True)
def test_setup_nginx_conf_renders_template(mock_exists, mock_render):
    with patch('docker.from_env'), patch('docker.DockerClient'):
        app = NginxProxyApp()
        app._setup_nginx_conf()
        assert mock_render.called


@patch('nginx_proxy.NginxProxyApp.render_nginx_conf')
@patch('os.path.exists', return_value=False)
def test_setup_nginx_conf_skips_if_no_template(mock_exists, mock_render):
    with patch('docker.from_env'), patch('docker.DockerClient'):
        app = NginxProxyApp()
        app._setup_nginx_conf()
        assert not mock_render.called
