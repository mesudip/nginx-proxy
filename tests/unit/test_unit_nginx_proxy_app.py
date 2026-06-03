import os
import pytest
from unittest.mock import patch, MagicMock

from jinja2 import Template

from nginx_proxy.DockerEventListener import RescanAndReload
from nginx_proxy.NginxProxyApp import NginxProxyApp, _detect_nginx_resolvers


@patch("docker.from_env")
@patch("docker.DockerClient")
def test_load_config_from_env(mock_docker_client, mock_from_env):
    with patch.dict(
        os.environ,
        {
            "CERT_RENEW_THRESHOLD_DAYS": "60",
            "DUMMY_NGINX": "true",
            "SSL_DIR": "/custom/ssl",
            "NGINX_CONF_DIR": "/custom/nginx",
            "CLIENT_MAX_BODY_SIZE": "10m",
            "DEFAULT_HOST": "false",
            "ENABLE_IPV6": "true",
            "DOCKER_SWARM": "strict",
            "SWARM_DOCKER_HOST": "tcp://swarm:2375",
            "DEFAULT_SSL_DOMAINS": "*.xyz.com, *.example.com",
        },
    ):
        with patch("sys.exit"):
            app = NginxProxyApp()
            config = app.config
            assert config["cert_renew_threshold_days"] == 60
            assert config["dummy_nginx"] is True
            assert config["ssl_dir"] == "/custom/ssl"
            assert config["conf_dir"] == "/custom/nginx"
            assert config["client_max_body_size"] == "10m"
            assert config["default_server"] is False
            assert config["enable_ipv6"] is True
            assert config["docker_swarm"] == "strict"
            assert config["swarm_docker_host"] == "tcp://swarm:2375"
            assert config["static_site_root"] == "/static"
            assert config["default_ssl_domains"] == ["*.xyz.com", "*.example.com"]


def test_detect_nginx_resolvers_reads_resolv_conf(tmp_path):
    resolv_conf = tmp_path / "resolv.conf"
    resolv_conf.write_text(
        """
# comment
search example.com
nameserver 127.0.0.11
nameserver 10.0.0.2
""".lstrip()
    )

    with patch.dict(os.environ, {}, clear=True):
        assert _detect_nginx_resolvers(str(resolv_conf)) == ["127.0.0.11", "10.0.0.2"]


def test_detect_nginx_resolvers_prefers_env_override(tmp_path):
    resolv_conf = tmp_path / "resolv.conf"
    resolv_conf.write_text("nameserver 127.0.0.11\n")

    with patch.dict(os.environ, {"NGINX_RESOLVER": "10.0.0.2, 10.0.0.3"}):
        assert _detect_nginx_resolvers(str(resolv_conf)) == ["10.0.0.2", "10.0.0.3"]


def test_certapi_url_rejects_unsupported_scheme():
    with patch.dict(os.environ, {"CERTAPI_URL": "ftp://certapi.example.com"}):
        with pytest.raises(SystemExit):
            NginxProxyApp()


@pytest.mark.parametrize(
    "certapi_url,expected_scheme,expected_port",
    [
        ("http://certapi.example.com", "http", 80),
        ("https://certapi.example.com", "https", 443),
        ("http://certapi.example.com:8080", "http", 8080),
        ("https://certapi.example.com:8443", "https", 8443),
    ],
)
@patch("nginx_proxy.NginxProxyApp.render_nginx_conf")
@patch("os.path.exists", return_value=False)
@patch("docker.from_env")
def test_certapi_url_accepts_http_and_https(
    mock_from_env,
    mock_exists,
    mock_render,
    certapi_url,
    expected_scheme,
    expected_port,
):
    with patch.dict(os.environ, {"CERTAPI_URL": certapi_url}):
        app = NginxProxyApp()

    assert app.config["certapi"]["scheme"] == expected_scheme
    assert app.config["certapi"]["port"] == expected_port
    assert app.config["certapi"]["endpoint"] == f"certapi.example.com:{expected_port}"


def _render_default_server_certapi_location(nginx_resolvers):
    with open("vhosts_template/default.conf.jinja2") as template_file:
        return Template(template_file.read()).render(
            virtual_servers=[],
            upstreams=[],
            config={
                "certapi": {
                    "url": "https://certapi.example.com:8443",
                    "host": "certapi.example.com",
                    "scheme": "https",
                    "port": 8443,
                    "endpoint": "certapi.example.com:8443",
                },
                "nginx_resolvers": nginx_resolvers,
                "client_max_body_size": "1m",
                "default_server": True,
                "enable_ipv6": False,
                "wellknown_path": "/.well-known/acme-challenge/",
                "challenge_dir": "/etc/nginx/challenges/",
                "vhosts_template_dir": "/app/vhosts_template",
            },
        )


def test_certapi_challenge_proxy_uses_runtime_resolver_when_configured():
    rendered = _render_default_server_certapi_location(["127.0.0.11"])

    assert "set $certapi_endpoint certapi.example.com:8443;" in rendered
    assert "proxy_pass https://$certapi_endpoint;" in rendered
    assert "proxy_pass https://certapi.example.com:8443;" not in rendered


def test_certapi_challenge_proxy_uses_literal_url_without_runtime_resolver():
    rendered = _render_default_server_certapi_location([])

    assert "set $certapi_endpoint" not in rendered
    assert "proxy_pass https://certapi.example.com:8443;" in rendered


@patch("docker.from_env")
def test_init_docker_client_default(mock_from_env):
    with patch.dict(os.environ, {"SWARM_DOCKER_HOST": ""}):
        app = NginxProxyApp()
        assert mock_from_env.call_count > 0
        assert app.docker_client is not None
        assert app.docker_client == app.swarm_client


@patch("docker.DockerClient")
@patch("docker.from_env")
def test_init_docker_client_with_swarm_host(mock_from_env, mock_docker_client):
    with patch.dict(os.environ, {"SWARM_DOCKER_HOST": "tcp://swarm:2375"}):
        with patch("sys.exit"):
            NginxProxyApp()
            assert mock_docker_client.called
            assert mock_from_env.called


@patch("nginx_proxy.NginxProxyApp.render_nginx_conf")
@patch("os.path.exists", return_value=False)
@patch("docker.from_env")
def test_prefer_local_validates_swarm_like_enable(mock_from_env, mock_exists, mock_render):
    docker_client = MagicMock()
    docker_client.info.return_value = {"Swarm": {"LocalNodeState": "active", "ControlAvailable": True}}
    mock_from_env.return_value = docker_client

    with patch.dict(os.environ, {"DOCKER_SWARM": "prefer-local", "SWARM_DOCKER_HOST": ""}):
        app = NginxProxyApp()

    assert app.config["docker_swarm"] == "prefer-local"
    docker_client.info.assert_called_once()


@patch("nginx_proxy.NginxProxyApp.render_nginx_conf")
@patch("os.path.exists", return_value=True)
def test_setup_nginx_conf_renders_template(mock_exists, mock_render):
    with patch("docker.from_env"), patch("docker.DockerClient"):
        app = NginxProxyApp()
        app._setup_nginx_conf()
        assert mock_render.called


@patch("nginx_proxy.NginxProxyApp.render_nginx_conf")
@patch("os.path.exists", return_value=False)
def test_setup_nginx_conf_skips_if_no_template(mock_exists, mock_render):
    with patch("docker.from_env"), patch("docker.DockerClient"):
        app = NginxProxyApp()
        app._setup_nginx_conf()
        assert not mock_render.called


def test_reload_rescans_and_forces_reload():
    with patch("docker.from_env"), patch("docker.DockerClient"):
        app = NginxProxyApp()

    app.server = MagicMock()

    app.reload()

    app.server.rescan_and_reload.assert_called_once_with(force=True, bypass_start_grace=True)


def test_reload_enqueues_rescan_when_dispatcher_is_running():
    with patch("docker.from_env"), patch("docker.DockerClient"):
        app = NginxProxyApp()

    app.server = MagicMock()
    app.docker_event_listener = MagicMock()
    app.docker_event_listener.is_dispatcher_running.return_value = True

    app.reload()

    app.server.rescan_and_reload.assert_not_called()
    command = app.docker_event_listener.enqueue.call_args.args[0]
    assert command == RescanAndReload(force=True, bypass_start_grace=True)
