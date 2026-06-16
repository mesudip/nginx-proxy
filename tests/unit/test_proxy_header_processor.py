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
