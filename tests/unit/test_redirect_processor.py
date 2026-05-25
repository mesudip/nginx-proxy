from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.pre_processors.redirect_processor import process_redirection


def test_proxy_full_redirect_rejects_invalid_target_hostname():
    vhost_map = {"target.example.com": {80: Host("target.example.com", 80)}}
    backend = BackendTarget(id="redirect-id", name="redirect-test")

    process_redirection(
        backend,
        {"PROXY_FULL_REDIRECT": "source.example.com -> bad_target.example.com"},
        vhost_map,
    )

    assert "source.example.com" not in vhost_map
    assert "bad_target.example.com" not in vhost_map


def test_proxy_full_redirect_skips_invalid_source_hostname():
    vhost_map = {"target.example.com": {80: Host("target.example.com", 80)}}
    backend = BackendTarget(id="redirect-id", name="redirect-test")

    process_redirection(
        backend,
        {"PROXY_FULL_REDIRECT": "bad_source.example.com,valid-source.example.com -> target.example.com"},
        vhost_map,
    )

    assert "bad_source.example.com" not in vhost_map
    assert "valid-source.example.com" in vhost_map
