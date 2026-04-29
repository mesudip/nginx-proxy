import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nginx_proxy.Host import Host
from nginx_proxy.post_processors.ssl_certificate_processor import SslCertificateProcessor


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_server():
    return SimpleNamespace(
        config={
            "certapi": {
                "url": "https://certapi.example.com",
                "host": "certapi.example.com",
                "scheme": "https",
                "port": 443,
            }
        },
        reload=Mock(),
    )


def _build_processor(monkeypatch, batch_value: str | None, start_ssl_thread=False):
    if batch_value is None:
        monkeypatch.delenv("CERTAPI_BATCH_DOMAINS", raising=False)
    else:
        monkeypatch.setenv("CERTAPI_BATCH_DOMAINS", batch_value)

    nginx = SimpleNamespace(challenge_dir="./.run_data/acme-challenges/")
    backend = Mock()
    backend.obtain.return_value = SimpleNamespace(issued=[], existing=[])
    backend_info = SimpleNamespace(
        backend=backend,
        key_store=Mock(),
        certapi_url="https://certapi.example.com",
        use_certapi_server=True,
        batch_domains=batch_value is None or batch_value != "false",
        cert_manager=None,
        certapi_client=backend,
        challenge_store=None,
    )

    with (
        patch(
            "nginx_proxy.post_processors.ssl_certificate_processor.build_certificate_backend",
            return_value=backend_info,
        ),
        patch("nginx_proxy.post_processors.ssl_certificate_processor.RenewalManager") as renewal_cls,
    ):
        renewal = Mock()
        renewal_cls.return_value = renewal
        processor = SslCertificateProcessor(
            nginx,
            server=_make_server(),
            update_threshold_days=1,
            ssl_dir="./.run_data",
            start_ssl_thread=start_ssl_thread,
        )
        processor._test_renewal_cls_call_args = renewal_cls.call_args
        return processor, backend, renewal


def test_certapi_batch_domains_enabled_by_default(monkeypatch):
    processor, backend, _renewal = _build_processor(monkeypatch, None)

    backend.obtain.assert_not_called()
    assert processor._test_renewal_cls_call_args.kwargs["batch_domains"] is True


def test_certapi_batch_domains_passed_to_renewal_manager(monkeypatch):
    processor, backend, _renewal = _build_processor(monkeypatch, "false")

    assert processor.certapi_batch_domains is False
    backend.obtain.assert_not_called()
    assert processor._test_renewal_cls_call_args.kwargs["batch_domains"] is False
    assert processor._test_renewal_cls_call_args.kwargs["renewal_callback"] == processor.sync_watch_domains


def test_processor_does_not_obtain_directly_and_triggers_renewal_once(monkeypatch):
    processor, backend, renewal = _build_processor(monkeypatch, None)
    processor.key_store.find_key_and_cert_by_domain.return_value = None

    hosts = [Host("api.example.com", 443, {"https"}), Host("www.example.com", 443, {"https"})]
    processor.process_ssl_certificates(hosts)

    renewal.update_watch_domains.assert_called_once_with(["api.example.com", "www.example.com"])
    renewal.trigger_now.assert_not_called()
    backend.obtain.assert_not_called()
    assert hosts[0].ssl_file == "api.example.com.selfsigned"
    assert hosts[1].ssl_file == "www.example.com.selfsigned"


def test_ssl_starts_and_stops_certapi_renewal_manager(monkeypatch):
    processor, _backend, renewal = _build_processor(monkeypatch, None, start_ssl_thread=True)
    renewal.start.assert_called_once_with()

    processor.shutdown()
    renewal.stop.assert_called_once_with()


def test_sync_watch_domains_publishes_secured_hosts_to_renewal_manager(monkeypatch):
    secured = Host("secure.example.com", 443, {"https"})
    wildcard = Host("*.example.com", 443, {"https"})
    plain = Host("plain.example.com", 80, {"http"})
    server = _make_server()
    server.config_data = SimpleNamespace(host_list=lambda: [secured, plain, wildcard])
    processor, _backend, renewal = _build_processor(monkeypatch, None)
    processor.server = server

    processor.sync_watch_domains()

    renewal.update_watch_domains.assert_called_once_with(["*.example.com", "secure.example.com"])
    server.reload.assert_called_once_with(force=True)


def test_getssl_force_passes_self_verify_false_to_remote_backend(monkeypatch, tmp_path):
    backend = Mock()
    backend.obtain.return_value = SimpleNamespace(issued=[], existing=[])
    backend_info = SimpleNamespace(
        backend=backend,
        key_store=Mock(),
        certapi_url="https://certapi.example.com",
        use_certapi_server=True,
        batch_domains=True,
        cert_manager=None,
        certapi_client=backend,
        challenge_store=None,
    )
    nginx = Mock()
    nginx.reload.return_value = True

    monkeypatch.setenv("CERTAPI_URL", "https://certapi.example.com")
    monkeypatch.setenv("SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("NGINX_CONF_DIR", str(tmp_path / "nginx"))
    monkeypatch.setenv("CHALLENGE_DIR", str(tmp_path / "challenges"))
    monkeypatch.setenv("CERT_RENEW_THRESHOLD_DAYS", "10")
    monkeypatch.setattr(sys, "argv", ["getssl", "--force", "api.example.com"])

    with (
        patch("nginx.Nginx.Nginx", return_value=nginx),
        patch("nginx_proxy.certificate_backend.build_certificate_backend", return_value=backend_info),
    ):
        runpy.run_path(str(REPO_ROOT / "getssl"), run_name="__main__")

    backend.obtain.assert_called_once_with(
        ["api.example.com"], key_type="ecdsa", batch_domains=True, self_verify=False
    )


def test_getssl_passes_self_verify_true_to_local_backend_by_default(monkeypatch, tmp_path):
    backend = Mock()
    backend.obtain.return_value = SimpleNamespace(issued=[], existing=[])
    backend_info = SimpleNamespace(
        backend=backend,
        key_store=Mock(),
        certapi_url="",
        use_certapi_server=False,
        batch_domains=True,
        cert_manager=backend,
        certapi_client=None,
        challenge_store=Mock(),
    )
    nginx = Mock()
    nginx.reload.return_value = True

    monkeypatch.delenv("CERTAPI_URL", raising=False)
    monkeypatch.setenv("SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("NGINX_CONF_DIR", str(tmp_path / "nginx"))
    monkeypatch.setenv("CHALLENGE_DIR", str(tmp_path / "challenges"))
    monkeypatch.setenv("CERT_RENEW_THRESHOLD_DAYS", "10")
    monkeypatch.setattr(sys, "argv", ["getssl", "api.example.com"])

    with (
        patch("nginx.Nginx.Nginx", return_value=nginx),
        patch("nginx_proxy.certificate_backend.build_certificate_backend", return_value=backend_info),
    ):
        runpy.run_path(str(REPO_ROOT / "getssl"), run_name="__main__")

    backend.obtain.assert_called_once_with(
        ["api.example.com"], key_type="ecdsa", batch_domains=True, self_verify=True
    )
