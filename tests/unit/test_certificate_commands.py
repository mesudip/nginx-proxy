import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from certapi.errors import CertApiException
from certapi.http.types import CertificateResponse, IssuedCert, FailedDomains
from nginx.NginxChallengeSolver import NginxChallengeSolver
from nginx_proxy.certapi_config import load_certapi_config

ROOT = Path(__file__).resolve().parents[2]


def command(name, domains, monkeypatch):
    monkeypatch.setattr(sys, "argv", [name, *domains])
    return runpy.run_path(str(ROOT / name))["main"]()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SSL_DIR", str(tmp_path / "ssl"))
    monkeypatch.setenv("NGINX_CONF_DIR", str(tmp_path / "nginx"))
    monkeypatch.setenv("CHALLENGE_DIR", str(tmp_path / "challenges"))
    monkeypatch.setenv("CERTAPI_URL", "http://certapi:8080/")
    monkeypatch.setenv("CERT_RENEW_THRESHOLD_DAYS", "45")
    return tmp_path


def test_verify_certapi_checks_requested_domains_without_local_files(monkeypatch, env):
    client = Mock()
    client.verify_domains.return_value = ["dev.gov.tools"]
    monkeypatch.setattr("certapi.client.cert_manager_client.CertManagerClient", Mock(return_value=client))
    nginx = Mock()
    monkeypatch.setattr("nginx.Nginx.Nginx", nginx)
    assert command("verify", ["dev.gov.tools"], monkeypatch) == 0
    client.verify_domains.assert_called_once_with(["dev.gov.tools"])
    client.obtain.assert_not_called()
    nginx.assert_not_called()
    assert not (env / "challenges").exists()


@pytest.mark.parametrize("result", [[], ["good.example"]])
def test_verify_partial_or_total_failure_exits_nonzero(monkeypatch, env, result, capsys):
    monkeypatch.setattr("requests.get", Mock(return_value=Mock(is_redirect=False, status_code=404)))
    client = Mock()
    client.verify_domains.return_value = result
    monkeypatch.setattr("certapi.client.cert_manager_client.CertManagerClient", Mock(return_value=client))
    assert command("verify", ["good.example", "bad.example"], monkeypatch) == 1
    assert "bad.example" in capsys.readouterr().err


@pytest.mark.parametrize(
    "probe,reason",
    [
        (requests.ConnectionError("Failed to resolve 'bad.example' (Name or service not known)"), "DNS lookup failed"),
        (requests.ConnectionError("[Errno 111] Connection refused"), "connection refused on port 80"),
        (requests.Timeout("read timed out"), "no answer on port 80 within 3 seconds"),
        (
            Mock(is_redirect=True, status_code=301, headers={"Location": "https://bad.example/"}),
            "port 80 redirects the challenge path (301 to https://bad.example/)",
        ),
        (Mock(is_redirect=False, status_code=404), "reachable from this proxy (HTTP 404)"),
    ],
)
def test_verify_certapi_failure_explains_the_cause(monkeypatch, env, capsys, probe, reason):
    get = Mock(side_effect=probe) if isinstance(probe, Exception) else Mock(return_value=probe)
    monkeypatch.setattr("requests.get", get)
    client = Mock()
    client.verify_domains.return_value = ["good.example"]
    monkeypatch.setattr("certapi.client.cert_manager_client.CertManagerClient", Mock(return_value=client))
    assert command("verify", ["good.example", "bad.example"], monkeypatch) == 1
    assert f"[Error] [bad.example] {reason}" in capsys.readouterr().err
    get.assert_called_once()
    assert get.call_args.args[0].startswith("http://bad.example/.well-known/acme-challenge/")


def test_verify_reports_invalid_hostnames_without_probing(monkeypatch, env, capsys):
    monkeypatch.delenv("CERTAPI_URL")
    get = Mock()
    monkeypatch.setattr("requests.get", get)
    nginx = Mock()
    nginx.verify_domain.return_value = []
    monkeypatch.setattr("nginx.Nginx.Nginx", Mock(return_value=nginx))
    assert command("verify", ["*.example.com"], monkeypatch) == 1
    assert "[Error] [*.example.com] not a valid hostname" in capsys.readouterr().err
    get.assert_not_called()


def test_verify_remote_errors_exit_nonzero(monkeypatch, env, capsys):
    client = Mock()
    client.verify_domains.side_effect = CertApiException("upgrade the server")
    monkeypatch.setattr("certapi.client.cert_manager_client.CertManagerClient", Mock(return_value=client))
    assert command("verify", ["example.com"], monkeypatch) == 1
    assert "upgrade the server" in capsys.readouterr().err


def test_verify_uses_local_checker_when_certapi_unset(monkeypatch, env):
    monkeypatch.delenv("CERTAPI_URL")
    nginx = Mock()
    nginx.verify_domain.return_value = ["example.com"]
    monkeypatch.setattr("nginx.Nginx.Nginx", Mock(return_value=nginx))
    assert command("verify", ["example.com"], monkeypatch) == 0
    nginx.verify_domain.assert_called_once_with(["example.com"])


@pytest.mark.parametrize("remote,force", [(False, False), (False, True), (True, False), (True, True)])
def test_getssl_backend_flags_and_reload(monkeypatch, env, remote, force):
    if not remote:
        monkeypatch.delenv("CERTAPI_URL")
    backend = Mock()
    backend.obtain.return_value = CertificateResponse(existing=[IssuedCert(domains=["*.gov.tools"])])
    factory = Mock(return_value=SimpleNamespace(backend=backend, batch_domains=True))
    monkeypatch.setattr("nginx_proxy.certificate_backend.build_certificate_backend", factory)
    nginx = Mock()
    nginx.reload.return_value = True
    nginx_cls = Mock(return_value=nginx)
    monkeypatch.setattr("nginx.Nginx.Nginx", nginx_cls)
    assert command("getssl", (["--force"] if force else []) + ["dev.gov.tools"], monkeypatch) == 0
    backend.obtain.assert_called_once_with(
        ["dev.gov.tools"],
        key_type="ecdsa",
        batch_domains=True,
        self_verify=not force,
        skip_failing=False,
        renew_threshold_days=45,
    )
    assert nginx_cls.call_args.kwargs["create_challenge_dir"] is not remote
    nginx.reload.assert_called_once_with()
    assert factory.call_args.kwargs["config"]["certapi"] == load_certapi_config()


@pytest.mark.parametrize(
    "result",
    [
        CertificateResponse(),
        CertificateResponse(
            issued=[IssuedCert(domains=["good.example"])],
            failed=[FailedDomains(domains=["bad.example"], message="verification failed")],
        ),
        CertificateResponse(existing=[IssuedCert(domains=["good.example"])]),
        # Older servers can report a failed batch for names another certificate already covers.
        CertificateResponse(
            issued=[IssuedCert(domains=["good.example", "bad.example"])],
            failed=[FailedDomains(domains=["bad.example"], message="verification failed")],
        ),
    ],
)
def test_getssl_empty_partial_and_failed_results_exit_nonzero(monkeypatch, env, result, capsys):
    backend = Mock()
    backend.obtain.return_value = result
    monkeypatch.setattr(
        "nginx_proxy.certificate_backend.build_certificate_backend",
        Mock(return_value=SimpleNamespace(backend=backend, batch_domains=True)),
    )
    nginx = Mock()
    nginx.reload.return_value = True
    monkeypatch.setattr("nginx.Nginx.Nginx", Mock(return_value=nginx))
    assert command("getssl", ["good.example", "bad.example"], monkeypatch) == 1
    error = capsys.readouterr().err
    summary = next(line for line in error.splitlines() if line.startswith("Error: Could not obtain"))
    assert "bad.example" in summary
    assert nginx.reload.call_count == bool(result.issued or result.existing)


def test_getssl_reload_failure_exits_nonzero(monkeypatch, env):
    backend = Mock()
    backend.obtain.return_value = CertificateResponse(existing=[IssuedCert(domains=["example.com"])])
    monkeypatch.setattr(
        "nginx_proxy.certificate_backend.build_certificate_backend",
        Mock(return_value=SimpleNamespace(backend=backend, batch_domains=True)),
    )
    nginx = Mock()
    nginx.reload.return_value = False
    monkeypatch.setattr("nginx.Nginx.Nginx", Mock(return_value=nginx))
    assert command("getssl", ["example.com"], monkeypatch) == 1


def test_local_force_bypasses_reachability_but_default_checks_it(tmp_path):
    nginx = Mock()
    nginx.verify_domain.return_value = True
    solver = NginxChallengeSolver(str(tmp_path), nginx)
    assert solver.supports_domain("example.com") is True
    nginx.verify_domain.assert_not_called()
    assert solver.supports_domain_strict("example.com") is True
    nginx.verify_domain.assert_called_once_with("example.com")
    assert solver.supports_domain("*.example.com") is False


@pytest.mark.parametrize(
    "url",
    [
        "ftp://certapi",
        "http://",
        "http://certapi:bad",
        "http://certapi:0",
        "http://certapi/path",
        "http://certapi?x=1",
        "http://certapi#fragment",
        "http://user@certapi",
        "http://certapi?x=",
        "http://cert api:8080",
        "http://certapi;x:8080",
        "http://cert{api}",
        "http://-certapi",
    ],
)
def test_all_commands_reject_invalid_certapi_urls(monkeypatch, env, url):
    monkeypatch.setenv("CERTAPI_URL", url)
    with pytest.raises(ValueError):
        load_certapi_config()
    assert command("verify", ["example.com"], monkeypatch) == 1
    assert command("getssl", ["example.com"], monkeypatch) == 1


@pytest.mark.parametrize(
    "url,endpoint",
    [
        ("http://certapi:8080/", "certapi:8080"),
        ("https://certapi", "certapi:443"),
        ("http://[::1]:8080/", "[::1]:8080"),
        ("http://certapi:8080?", "certapi:8080"),
        ("http://certapi:8080#", "certapi:8080"),
        ("http://certapi:8080/?#", "certapi:8080"),
        ("http://certapi_certapi:8080", "certapi_certapi:8080"),
        ("http://10.0.0.5:8080", "10.0.0.5:8080"),
    ],
)
def test_certapi_url_normalization(url, endpoint):
    config = load_certapi_config(url)
    assert config["endpoint"] == endpoint
    assert config["url"] == config["url"].rstrip("/?#")  # rendered as-is into proxy_pass


def test_whitespace_certapi_url_selects_local_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTAPI_URL", "   ")
    from nginx_proxy.certificate_backend import build_certificate_backend

    manager = Mock()
    monkeypatch.setattr("nginx_proxy.certificate_backend.AcmeCertManager", Mock(return_value=manager))
    monkeypatch.setattr("nginx_proxy.certificate_backend.AcmeCertIssuer", Mock())
    backend = build_certificate_backend(
        str(tmp_path / "ssl"), SimpleNamespace(challenge_dir=str(tmp_path / "challenges"))
    )
    assert backend.use_certapi_server is False
    assert backend.backend is manager
