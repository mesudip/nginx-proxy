"""
Error Resilience Tests for nginx-proxy

This test suite verifies that nginx-proxy handles various error conditions gracefully
without crashing. It covers ACME errors, connection failures, Cloudflare API errors,
and unexpected runtime errors.
"""

import pytest
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from requests.exceptions import ConnectionError, Timeout, SSLError
from certapi import CertApiException
from certapi.http.types import IssuedCert, CertificateResponse

from nginx_proxy.WebServer import WebServer
from nginx_proxy.Host import Host
from nginx_proxy.NginxProxyApp import NginxProxyAppConfig
from nginx_proxy.post_processors.ssl_certificate_processor import SslCertificateProcessor
from nginx.NginxConf import HttpBlock

from tests.helpers.docker_test_client import DockerTestClient


def get_test_config() -> NginxProxyAppConfig:
    """Create a test configuration for WebServer."""
    return NginxProxyAppConfig(
        dummy_nginx=True,
        ssl_dir="./.run_data",
        conf_dir="./run_data",
        client_max_body_size="1m",
        challenge_dir="./.run_data/acme-challenges/",
        default_server=True,
        vhosts_template_dir="./vhosts_template",
        cert_renew_threshold_days=10,
        certapi_url="",
        wellknown_path="/.well-known/acme-challenge/",
    )


@pytest.fixture()
def docker_client():
    return DockerTestClient()


@pytest.fixture()
def webserver_for_error_tests(docker_client: DockerTestClient):
    """Create a webserver instance for error testing without starting background threads"""
    docker_client.networks.create("frontend")

    with patch("certapi.manager.acme_cert_manager.AcmeCertManager.setup") as mock_acme_setup:
        mock_acme_setup.return_value = None
        config = get_test_config()
        webserver = WebServer(docker_client, config, nginx_update_throtle_sec=0.1)
        yield webserver

        docker_client.close()
        webserver.cleanup()


# ============================================================================
# Combined Error Resilience Tests
# ============================================================================


@pytest.mark.parametrize(
    "error, hostname",
    [
        # ACME Error Codes
        (
            CertApiException(
                "Rate limit exceeded",
                detail={"type": "urn:ietf:params:acme:error:rateLimited"},
                step="ACME Certificate Request",
            ),
            "test-rate-limit.example.com",
        ),
        (
            CertApiException(
                "Authorization failed",
                detail={"type": "urn:ietf:params:acme:error:unauthorized"},
                step="ACME Authorization",
            ),
            "test-auth-fail.example.com",
        ),
        (
            CertApiException(
                "Invalid domain name",
                detail={"type": "urn:ietf:params:acme:error:rejectedIdentifier"},
                step="ACME Domain Validation",
            ),
            "invalid..domain.com",
        ),
        (
            CertApiException(
                "Challenge validation failed",
                detail={"type": "urn:ietf:params:acme:error:incorrectResponse"},
                step="ACME Challenge Validation",
            ),
            "test-challenge-fail.example.com",
        ),
        # ACME Server Errors
        (
            CertApiException("Server error 500", detail={"status": 500}, step="ACME Server Communication"),
            "test-server-error-500.example.com",
        ),
        (
            CertApiException("Server error 502", detail={"status": 502}, step="ACME Server Communication"),
            "test-server-error-502.example.com",
        ),
        (
            CertApiException("Server error 503", detail={"status": 503}, step="ACME Server Communication"),
            "test-server-error-503.example.com",
        ),
        # ACME Connection Errors
        (ConnectionError("Connection refused"), "test-conn-refused.example.com"),
        (Timeout("Request timed out"), "test-timeout.example.com"),
        (SSLError("SSL handshake failed"), "test-ssl-fail.example.com"),
        (ConnectionError("Failed to resolve hostname"), "test-dns-fail.example.com"),
        # Cloudflare API Errors
        (
            CertApiException(
                "Authentication failed",
                detail={"code": 10000, "message": "Invalid API token"},
                step="Cloudflare Authentication",
            ),
            "test-cf-auth.example.com",
        ),
        (
            CertApiException(
                "Rate limit exceeded",
                detail={"code": 10000, "message": "Too many requests"},
                step="Cloudflare API Request",
            ),
            "test-cf-ratelimit.example.com",
        ),
        (
            CertApiException(
                "Zone not found",
                detail={"code": 1001, "message": "Zone could not be found"},
                step="Cloudflare Zone Lookup",
            ),
            "test-cf-nozone.example.com",
        ),
        (
            CertApiException(
                "DNS record already exists",
                detail={"code": 81057, "message": "The record already exists"},
                step="Cloudflare DNS Record Creation",
            ),
            "test-cf-conflict.example.com",
        ),
        # Cloudflare Server Errors
        (
            CertApiException(
                "Cloudflare server error 500", detail={"status": 500}, step="Cloudflare API Communication"
            ),
            "test-cf-error-500.example.com",
        ),
        (
            CertApiException(
                "Cloudflare server error 502", detail={"status": 502}, step="Cloudflare API Communication"
            ),
            "test-cf-error-502.example.com",
        ),
        (
            CertApiException(
                "Cloudflare server error 503", detail={"status": 503}, step="Cloudflare API Communication"
            ),
            "test-cf-error-503.example.com",
        ),
        # Cloudflare Connection Errors
        (ConnectionError("Connection to Cloudflare API refused"), "test-cf-conn-refused.example.com"),
        (Timeout("Cloudflare API request timed out"), "test-cf-timeout.example.com"),
        (ConnectionError("Failed to resolve api.cloudflare.com"), "test-cf-dns-fail.example.com"),
        # Runtime Errors
        (ZeroDivisionError("Division by zero in library"), "test-div-zero.example.com"),
        (AttributeError("Accessing non-existing attribute in library"), "test-attr-error.example.com"),
        (IndexError("Index out of bounds in library"), "test-index-error.example.com"),
        (ValueError("Invalid value in library processing"), "test-value-error.example.com"),
        (TypeError("Type mismatch in library call"), "test-type-error.example.com"),
    ],
)
def test_error_resilience(webserver_for_error_tests, error, hostname):
    """Test that various errors during certificate issuance don't crash the application and fallback to self-signed"""
    webserver = webserver_for_error_tests

    with patch.object(webserver.ssl_processor.cert_manager, "obtain", side_effect=error):
        hosts = [Host(hostname=hostname, port=443, scheme={"https"})]

        webserver.ssl_processor.process_ssl_certificates(hosts)

        # Should fallback to self-signed
        assert hosts[0].ssl_file == f"{hostname}.selfsigned"


# ============================================================================
# Resilience Verification Tests
# ============================================================================


@pytest.mark.parametrize(
    "error, hostname",
    [
        # Sample ACME Errors
        (CertApiException("ACME error", step="Test"), "test-continue-acme.example.com"),
        (
            CertApiException(
                "Rate limit exceeded",
                detail={"type": "urn:ietf:params:acme:error:rateLimited"},
                step="ACME Certificate Request",
            ),
            "test-continue-rate-limit.example.com",
        ),
        # Sample Runtime Errors
        (ZeroDivisionError("Division by zero in library"), "test-continue-div-zero.example.com"),
        (AttributeError("Accessing non-existing attribute in library"), "test-continue-attr-error.example.com"),
        (IndexError("Index out of bounds in library"), "test-continue-index-error.example.com"),
    ],
)
def test_webserver_continues_after_errors(docker_client: DockerTestClient, error, hostname):
    """Test that webserver continues running after various errors"""
    docker_client.networks.create("frontend")

    with patch("certapi.manager.acme_cert_manager.AcmeCertManager.setup") as mock_acme_setup:
        mock_acme_setup.return_value = None
        config = get_test_config()
        webserver = WebServer(docker_client, config, nginx_update_throtle_sec=0.1)

        # Simulate error
        with patch.object(webserver.ssl_processor.cert_manager, "obtain", side_effect=error):
            hosts = [Host(hostname=hostname, port=443, scheme={"https"})]
            webserver.ssl_processor.process_ssl_certificates(hosts)

        # Webserver should still be functional - add a container
        container = docker_client.containers.run(
            "nginx:alpine",
            name="test_resilience_container",
            environment={"VIRTUAL_HOST": "test-resilience.example.com"},
            network="frontend",
        )
        time.sleep(0.2)

        # Verify nginx config is still valid
        config = HttpBlock.parse(webserver.nginx.current_config)
        assert len(config.servers) >= 1

        # Cleanup
        container.remove(force=True)
        docker_client.close()
        webserver.cleanup()


@pytest.mark.parametrize(
    "error, hostname",
    [
        # Sample ACME Errors
        (CertApiException("ACME failure", step="Test"), "test-fallback-acme.example.com"),
        (
            CertApiException(
                "Authorization failed",
                detail={"type": "urn:ietf:params:acme:error:unauthorized"},
                step="ACME Authorization",
            ),
            "test-fallback-auth-fail.example.com",
        ),
        # Sample Runtime Errors
        (ZeroDivisionError("Division by zero in library"), "test-fallback-div-zero.example.com"),
        (AttributeError("Accessing non-existing attribute in library"), "test-fallback-attr-error.example.com"),
        (IndexError("Index out of bounds in library"), "test-fallback-index-error.example.com"),
    ],
)
def test_fallback_to_selfsigned_on_failure(webserver_for_error_tests, error, hostname):
    """Test that system falls back to self-signed certificates on various failures"""
    webserver = webserver_for_error_tests

    with patch.object(webserver.ssl_processor.cert_manager, "obtain", side_effect=error):
        hosts = [Host(hostname=hostname, port=443, scheme={"https"})]

        webserver.ssl_processor.process_ssl_certificates(hosts)

        # Should have self-signed certificate
        assert hosts[0].ssl_file == f"{hostname}.selfsigned"


def test_failed_initial_request_delegates_fallback_to_renewal_manager(webserver_for_error_tests):
    """Initial issuance retry behavior is owned by certapi's renewal manager."""
    webserver = webserver_for_error_tests
    processor = webserver.ssl_processor

    hosts = [Host(hostname="test-fallback-delegated.example.com", port=443, scheme={"https"})]
    with patch.object(processor.cert_manager, "obtain") as mock_obtain, patch.object(
        processor.renewal_manager, "update_watch_domains"
    ) as mock_update_watch_domains:
        processor.process_ssl_certificates(hosts)

    mock_obtain.assert_not_called()
    mock_update_watch_domains.assert_any_call(["test-fallback-delegated.example.com"])


def test_multiple_simultaneous_cert_errors(webserver_for_error_tests):
    """Test handling of multiple certificate failures simultaneously"""
    webserver = webserver_for_error_tests

    error = CertApiException("ACME failure", step="Test")

    with patch.object(webserver.ssl_processor.cert_manager, "obtain", side_effect=error):
        hosts = [
            Host(hostname="test-multi-1.example.com", port=443, scheme={"https"}),
            Host(hostname="test-multi-2.example.com", port=443, scheme={"https"}),
            Host(hostname="test-multi-3.example.com", port=443, scheme={"https"}),
        ]

        # Should not crash with multiple failures
        webserver.ssl_processor.process_ssl_certificates(hosts)

        # All should have self-signed certificates
        for host in hosts:
            assert host.ssl_file.endswith(".selfsigned")


def test_wildcard_cert_error_handling(webserver_for_error_tests):
    """Test that wildcard certificate errors are handled gracefully"""
    webserver = webserver_for_error_tests

    error = CertApiException("Wildcard cert failure", step="Test")

    with patch.object(webserver.ssl_processor.renewal_manager, "update_watch_domains") as mock_update_watch_domains:
        hosts = [Host(hostname="*.wildcard-test.example.com", port=443, scheme={"https"})]

        # Should not crash
        webserver.ssl_processor.process_ssl_certificates(hosts)

        # Should fallback to self-signed
        assert hosts[0].ssl_file == "*.wildcard-test.example.com.selfsigned"
        mock_update_watch_domains.assert_any_call(["*.wildcard-test.example.com"])


def test_fresh_wildcard_cert_remains_preferred(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    hosts = [
        Host(hostname="*.example.com", port=443, scheme={"https"}),
        Host(hostname="api.example.com", port=443, scheme={"https"}),
    ]
    fresh_cert = Mock(not_valid_after_utc=datetime.now(timezone.utc) + timedelta(days=30))

    def find_cert(domain):
        if domain == "*.example.com":
            return ("*.example.com", Mock(), [fresh_cert])
        return None

    with patch.object(webserver.ssl_processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert), patch.object(
        webserver.ssl_processor.renewal_manager, "update_watch_domains"
    ) as mock_update_watch_domains:
        webserver.ssl_processor.process_ssl_certificates(hosts)

    assert hosts[0].ssl_file == "*.example.com"
    assert hosts[1].ssl_file == "*.example.com"
    mock_update_watch_domains.assert_called_once_with(["*.example.com", "api.example.com"])


def test_wildcard_near_expiry_is_not_preferred(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    hosts = [
        Host(hostname="*.example.com", port=443, scheme={"https"}),
        Host(hostname="api.example.com", port=443, scheme={"https"}),
    ]
    expiring_cert = Mock(not_valid_after_utc=datetime.now(timezone.utc) + timedelta(days=3))

    def find_cert(domain):
        if domain == "*.example.com":
            return ("*.example.com", Mock(), [expiring_cert])
        return None

    with patch.object(webserver.ssl_processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert), patch.object(
        webserver.ssl_processor.renewal_manager, "update_watch_domains"
    ) as mock_update_watch_domains:
        webserver.ssl_processor.process_ssl_certificates(hosts)

    assert hosts[0].ssl_file == "*.example.com"
    assert hosts[1].ssl_file == "api.example.com.selfsigned"
    mock_update_watch_domains.assert_called_once_with(["*.example.com", "api.example.com"])


def test_existing_cert_is_kept_while_renewal_manager_handles_retry(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    processor = webserver.ssl_processor
    hostname = "renew-existing.example.com"
    expired_cert = Mock(not_valid_after_utc=datetime.now(timezone.utc) - timedelta(days=1))

    def find_cert(domain):
        if domain == hostname:
            return (hostname, Mock(), [expired_cert])
        return None

    with (
        patch.object(processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert),
        patch.object(processor.cert_manager, "obtain", side_effect=CertApiException("ACME renewal failed", step="Test")) as mock_obtain,
    ):
        hosts = [Host(hostname=hostname, port=443, scheme={"https"})]
        webserver.ssl_processor.process_ssl_certificates(hosts)

    assert hosts[0].ssl_file == hostname


def test_initial_failure_for_existing_cert_delegates_retry_to_renewal_manager(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    processor = webserver.ssl_processor
    hostname = "retry-backoff.example.com"
    existing_cert = Mock(not_valid_after_utc=datetime.now(timezone.utc) - timedelta(days=1))

    def find_cert(domain):
        if domain == hostname:
            return (hostname, Mock(), [existing_cert])
        return None

    with (
        patch.object(processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert),
        patch.object(processor.cert_manager, "obtain") as mock_obtain,
        patch.object(processor.renewal_manager, "update_watch_domains") as mock_update_watch_domains,
    ):
        processor.process_ssl_certificates([Host(hostname=hostname, port=443, scheme={"https"})])

    mock_obtain.assert_not_called()
    mock_update_watch_domains.assert_called_once_with([hostname])


def test_failed_wildcard_with_existing_cert_gets_concrete_individual_cert(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    processor = webserver.ssl_processor
    wildcard = "*.example.com"
    hosts = [
        Host(hostname=wildcard, port=443, scheme={"https"}),
        Host(hostname="api.example.com", port=443, scheme={"https"}),
    ]
    wildcard_cert = Mock(not_valid_after_utc=datetime.now(timezone.utc) + timedelta(days=3))

    def find_cert(domain):
        if domain == wildcard:
            return (wildcard, Mock(), [wildcard_cert])
        return None

    with (
        patch.object(processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert),
        patch.object(processor.renewal_manager, "update_watch_domains") as mock_update_watch_domains,
    ):
        webserver.ssl_processor.process_ssl_certificates(hosts)

    assert hosts[0].ssl_file == wildcard
    assert hosts[1].ssl_file == "api.example.com.selfsigned"
    mock_update_watch_domains.assert_called_once_with(["*.example.com", "api.example.com"])


def test_failed_wildcard_expiring_within_48h_triggers_individual_dependent_issuance(webserver_for_error_tests):
    webserver = webserver_for_error_tests
    processor = webserver.ssl_processor
    wildcard = "*.example.com"
    hosts = [
        Host(hostname=wildcard, port=443, scheme={"https"}),
        Host(hostname="api.example.com", port=443, scheme={"https"}),
    ]
    expiring_in_12h = Mock(not_valid_after_utc=datetime.now(timezone.utc) + timedelta(hours=12))

    processor.update_threshold_secs = 3600

    def find_cert(domain):
        if domain == wildcard:
            return (wildcard, Mock(), [expiring_in_12h])
        return None

    with (
        patch.object(processor.key_store, "find_key_and_cert_by_domain", side_effect=find_cert),
        patch.object(processor.renewal_manager, "update_watch_domains") as mock_update_watch_domains,
    ):
        processor.process_ssl_certificates(hosts)

    assert hosts[0].ssl_file == wildcard
    assert hosts[1].ssl_file == wildcard
    mock_update_watch_domains.assert_called_once_with(["*.example.com", "api.example.com"])


def test_ssl_uses_renewal_manager_for_background_startup():
    server = SimpleNamespace(
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
    nginx = SimpleNamespace(challenge_dir="./.run_data/acme-challenges/")
    backend = Mock()
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
            server=server,
            update_threshold_days=1,
            ssl_dir="./.run_data",
            start_ssl_thread=True,
        )

    renewal.start.assert_called_once_with()
    processor.shutdown()
    renewal.stop.assert_called_once_with()
