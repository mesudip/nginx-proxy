from pathlib import Path

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.post_processors.proxy_header_processor import ProxyHeaderProcessor


def _backend(name: str):
    return BackendTarget(
        id=name,
        address="172.18.0.2",
        port=80,
        path="",
        name=name,
        env={},
        labels={},
        backend_type="container",
    )


def test_plain_http_location_does_not_reinsert_global_config():
    host = Host("example.com", 80)
    backend = _backend("plain")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend

    ProxyHeaderProcessor().process([host])

    assert location.reinsert_global_config is False
    assert location.reinserted_proxy_set_headers == []


def test_proxy_set_header_injection_sets_reinsert_flag_and_preserves_other_defaults():
    host = Host("example.com", 80)
    backend = _backend("custom")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras({"injected": ["proxy_set_header Host custom.example"]})

    ProxyHeaderProcessor().process([host])

    assert location.reinsert_global_config is True
    assert ("Host", "$http_host") not in location.reinserted_proxy_set_headers
    assert ("X-Real-IP", "$remote_addr") in location.reinserted_proxy_set_headers
    assert ("Proxy", '""') in location.reinserted_proxy_set_headers


def test_proxy_set_header_injection_accepts_non_list_iterables():
    host = Host("example.com", 80)
    backend = _backend("custom-iterable")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras({"injected": ("proxy_set_header Host custom.example",)})

    ProxyHeaderProcessor().process([host])

    assert location.reinsert_global_config is True
    assert ("Host", "$http_host") not in location.reinserted_proxy_set_headers
    assert ("X-Real-IP", "$remote_addr") in location.reinserted_proxy_set_headers


def test_websocket_only_location_sets_reinsert_flag():
    host = Host("example.com", 80)
    backend = _backend("ws-only")
    host.add_container("/", backend, websocket=True, http=False)
    location = host.locations["/"]
    location.container = backend

    ProxyHeaderProcessor().process([host])

    assert location.reinsert_global_config is True
    assert ("Host", "$http_host") in location.reinserted_proxy_set_headers
    assert ("Connection", "$connection_upgrade") in location.reinserted_proxy_set_headers
    assert ("Upgrade", "$http_upgrade") in location.reinserted_proxy_set_headers


def test_add_header_injection_reinserts_global_add_headers():
    """An injected add_header discards every inherited add_header, HSTS included."""
    host = Host("example.com", 443)
    backend = _backend("custom-add-header")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras({"injected": ["add_header X-Frame-Options SAMEORIGIN"]})

    ProxyHeaderProcessor().process([host])

    assert location.reinsert_global_config is True
    assert ("Strict-Transport-Security", '"max-age=31536000" always') in location.reinserted_add_headers
    # add_header and proxy_set_header inherit independently.
    assert location.reinserted_proxy_set_headers == []


def test_add_header_override_is_not_reinserted():
    host = Host("example.com", 443)
    backend = _backend("hsts-override")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras({"injected": ["add_header Strict-Transport-Security max-age=0"]})

    ProxyHeaderProcessor().process([host])

    assert location.reinserted_add_headers == []


def test_vhost_config_finds_add_header_after_another_directive_on_the_same_line():
    host = Host("example.com", 443)
    backend = _backend("same-line-add-header")
    backend.type = "static_site"
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras(
        {"vhost_config": 'expires 1h; add_header X-Example "value;with-semicolon" always;'}
    )

    ProxyHeaderProcessor().process([host])

    assert ("Strict-Transport-Security", '"max-age=31536000" always') in location.reinserted_add_headers


def test_vhost_config_finds_hsts_override_after_another_header_on_the_same_line():
    host = Host("example.com", 443)
    backend = _backend("same-line-hsts-override")
    backend.type = "static_site"
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras(
        {
            "vhost_config": (
                "add_header X-Frame-Options SAMEORIGIN; "
                'add_header Strict-Transport-Security "max-age=0" always;'
            )
        }
    )

    ProxyHeaderProcessor().process([host])

    assert location.reinserted_add_headers == []


def test_proxy_set_header_injection_leaves_add_headers_inherited():
    host = Host("example.com", 443)
    backend = _backend("proxy-only")
    host.add_container("/", backend)
    location = host.locations["/"]
    location.container = backend
    location.update_extras({"injected": ["proxy_set_header X-Foo bar"]})

    ProxyHeaderProcessor().process([host])

    assert location.reinserted_add_headers == []
    assert ("Host", "$http_host") in location.reinserted_proxy_set_headers


def test_http_level_add_header_matches_processor_defaults():
    """The reinserted values must stay identical to the http-level ones in the template."""
    template = (Path(__file__).parents[2] / "vhosts_template" / "default.conf.jinja2").read_text()
    rendered = {f"add_header {name} {value};" for name, value in ProxyHeaderProcessor._DEFAULT_ADD_HEADERS}

    http_level = {line.strip() for line in template.splitlines() if line.startswith("add_header ")}

    assert http_level == rendered
