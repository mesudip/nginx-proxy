import os

from jinja2 import Template

from nginx.NginxConf import NginxConfig
from nginx_proxy.post_processors.upstream_processor import UpstreamProcessor
from nginx_proxy.pre_processors.static_site_processor import process_static_sites


def test_process_static_sites_discovers_valid_domain_directories(tmp_path, capsys):
    static_root = tmp_path / "static"
    current = static_root / "example.com" / "current"
    current.mkdir(parents=True)
    (static_root / "bad_domain" / "current").mkdir(parents=True)
    (static_root / "missing-current.example.com").mkdir()

    config_data = process_static_sites(str(static_root))

    host = config_data.getHost("example.com", 443)
    assert host is not None
    assert host.secured is True
    backend = host.locations["/"].backends[0]
    assert backend.type == "static_site"
    assert backend.path == str(current)

    output = capsys.readouterr().out
    assert "[static-site] Hosting example.com" in output
    assert "Ignoring invalid domain directory: bad_domain" in output
    assert "Ignoring missing-current.example.com" in output


def test_process_static_sites_skips_missing_root(tmp_path, capsys):
    config_data = process_static_sites(str(tmp_path / "missing"))

    assert len(config_data) == 0
    assert "Root does not exist" in capsys.readouterr().out


def test_process_static_sites_rejects_unsafe_root_path(tmp_path, capsys):
    static_root = tmp_path / "static root"
    (static_root / "example.com" / "current").mkdir(parents=True)

    config_data = process_static_sites(str(static_root))

    assert len(config_data) == 0
    captured = capsys.readouterr()
    assert "ERROR: STATIC_SITE_ROOT contains spaces or unsupported characters" in captured.err
    assert "Hosting example.com" not in captured.out


def test_process_static_sites_rejects_certificate_hostname_over_64_chars(tmp_path, capsys):
    static_root = tmp_path / "static"
    long_domain = f"{'a' * 32}.{'b' * 32}.com"
    (static_root / long_domain / "current").mkdir(parents=True)

    config_data = process_static_sites(str(static_root))

    assert len(config_data) == 0
    output = capsys.readouterr().out
    assert f"Ignoring invalid domain directory: {long_domain}: certificate hostnames must be 64 characters or fewer" in output
    assert "Hosting" not in output


def test_process_static_sites_skips_all_static_sites_when_root_scan_fails(tmp_path, monkeypatch, capsys):
    static_root = tmp_path / "static"
    (static_root / "example.com" / "current").mkdir(parents=True)

    def fail_scandir(_root):
        raise PermissionError("permission denied")

    monkeypatch.setattr("nginx_proxy.pre_processors.static_site_processor.os.scandir", fail_scandir)

    config_data = process_static_sites(str(static_root))

    assert len(config_data) == 0
    captured = capsys.readouterr()
    assert "WARNING: Could not scan root" in captured.err
    assert "permission denied" in captured.err
    assert "Hosting example.com" not in captured.out


def test_process_static_sites_skips_entry_when_entry_inspection_fails(tmp_path, monkeypatch, capsys):
    static_root = tmp_path / "static"
    static_root.mkdir()

    class BrokenEntry:
        name = "example.com"
        path = str(static_root / "example.com")

        def is_dir(self, follow_symlinks=True):
            raise PermissionError("entry permission denied")

    class ScandirResult:
        def __enter__(self):
            return [BrokenEntry()]

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("nginx_proxy.pre_processors.static_site_processor.os.scandir", lambda _root: ScandirResult())

    config_data = process_static_sites(str(static_root))

    assert len(config_data) == 0
    captured = capsys.readouterr()
    assert "WARNING: Could not inspect" in captured.err
    assert "entry permission denied" in captured.err


def test_process_static_sites_skips_domain_when_current_path_inspection_fails(tmp_path, monkeypatch, capsys):
    static_root = tmp_path / "static"
    current = static_root / "example.com" / "current"
    current.mkdir(parents=True)

    real_isdir = os.path.isdir

    def fake_isdir(path):
        if path == str(current):
            raise PermissionError("current permission denied")
        return real_isdir(path)

    monkeypatch.setattr("nginx_proxy.pre_processors.static_site_processor.os.path.isdir", fake_isdir)

    config_data = process_static_sites(str(static_root))

    assert len(config_data) == 0
    captured = capsys.readouterr()
    assert "WARNING: Could not inspect" in captured.err
    assert "current permission denied" in captured.err


def test_static_site_location_renders_root_and_try_files(tmp_path):
    static_root = tmp_path / "static"
    current = static_root / "example.com" / "current"
    current.mkdir(parents=True)

    host = process_static_sites(str(static_root)).getHost("example.com", 443)
    for location in host.locations.values():
        location.container = list(location.backends)[0]
    UpstreamProcessor().process([host])
    host.ssl_file = "example.com"

    with open("vhosts_template/default.conf.jinja2") as template_file:
        rendered = Template(template_file.read()).render(
            virtual_servers=[host],
            upstreams=[],
            config={
                "client_max_body_size": "1m",
                "default_server": False,
                "enable_ipv6": False,
                "rendered_error_conf_path": "/etc/nginx/error.conf",
                "ssl_certs_dir": "/etc/ssl/certs",
                "ssl_key_dir": "/etc/ssl/private",
                "wellknown_path": "/.well-known/acme-challenge/",
                "certapi": None,
                "challenge_dir": "/etc/nginx/challenges/",
            },
        )

    config = NginxConfig()
    config.load(f"http {{\n{rendered}\n}}")
    server = config.http.servers[0]
    location = next(loc for loc in server.locations if loc.path == "/")

    assert location.root == str(current)
    assert location.try_files == "$uri $uri/ /index.html"
    assert location.proxy_pass is None
