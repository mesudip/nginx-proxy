"""
Error Resilience Tests for nginx-proxy

This test suite verifies that nginx-proxy handles various error conditions gracefully
without crashing. It covers ACME errors, connection failures, Cloudflare API errors,
and unexpected runtime errors.
"""

import pytest
import time
from unittest.mock import patch
from requests.exceptions import ConnectionError, Timeout, SSLError
from certapi import CertApiException
from certapi.http.types import IssuedCert, CertificateResponse

from nginx_proxy.WebServer import WebServer
from nginx_proxy.Host import Host
from nginx.NginxConf import HttpBlock

from tests.helpers.docker_test_client import DockerTestClient


@pytest.fixture()
def docker_client():
    return DockerTestClient()


@pytest.fixture()
def webserver_for_error_tests(docker_client: DockerTestClient):
    """Create a webserver instance for error testing without starting background threads"""
    docker_client.networks.create("frontend")
    
    with patch("nginx_proxy.WebServer.WebServer.loadconfig") as mock_loadconfig, \
         patch("certapi.manager.acme_cert_manager.AcmeCertManager.setup") as mock_acme_setup:
        mock_acme_setup.return_value = None
        mock_loadconfig.return_value = {
            "dummy_nginx": True,
            "ssl_dir": "./.run_data",
            "conf_dir": "./run_data",
            "client_max_body_size": "1m",
            "challenge_dir": "./.run_data/acme-challenges/",
            "default_server": True,
            "vhosts_template_dir": "./vhosts_template",
        }
        
        webserver = WebServer(docker_client, nginx_update_throtle_sec=0.1)
        yield webserver
        
        docker_client.close()
        webserver.cleanup()
        if webserver.ssl_processor.ssl.certificate_expiry_thread.is_alive():
            webserver.ssl_processor.ssl.certificate_expiry_thread.join(timeout=2)


# ============================================================================
# Combined Error Resilience Tests
# ============================================================================

@pytest.mark.parametrize(
    "error, hostname",
    [
        # ACME Error Codes
        (CertApiException("Rate limit exceeded", detail={"type": "urn:ietf:params:acme:error:rateLimited"}, step="ACME Certificate Request"), "test-rate-limit.example.com"),
        (CertApiException("Authorization failed", detail={"type": "urn:ietf:params:acme:error:unauthorized"}, step="ACME Authorization"), "test-auth-fail.example.com"),
        (CertApiException("Invalid domain name", detail={"type": "urn:ietf:params:acme:error:rejectedIdentifier"}, step="ACME Domain Validation"), "invalid..domain.com"),
        (CertApiException("Challenge validation failed", detail={"type": "urn:ietf:params:acme:error:incorrectResponse"}, step="ACME Challenge Validation"), "test-challenge-fail.example.com"),
        
        # ACME Server Errors
        (CertApiException("Server error 500", detail={"status": 500}, step="ACME Server Communication"), "test-server-error-500.example.com"),
        (CertApiException("Server error 502", detail={"status": 502}, step="ACME Server Communication"), "test-server-error-502.example.com"),
        (CertApiException("Server error 503", detail={"status": 503}, step="ACME Server Communication"), "test-server-error-503.example.com"),
        
        # ACME Connection Errors
        (ConnectionError("Connection refused"), "test-conn-refused.example.com"),
        (Timeout("Request timed out"), "test-timeout.example.com"),
        (SSLError("SSL handshake failed"), "test-ssl-fail.example.com"),
        (ConnectionError("Failed to resolve hostname"), "test-dns-fail.example.com"),
        
        # Cloudflare API Errors
        (CertApiException("Authentication failed", detail={"code": 10000, "message": "Invalid API token"}, step="Cloudflare Authentication"), "test-cf-auth.example.com"),
        (CertApiException("Rate limit exceeded", detail={"code": 10000, "message": "Too many requests"}, step="Cloudflare API Request"), "test-cf-ratelimit.example.com"),
        (CertApiException("Zone not found", detail={"code": 1001, "message": "Zone could not be found"}, step="Cloudflare Zone Lookup"), "test-cf-nozone.example.com"),
        (CertApiException("DNS record already exists", detail={"code": 81057, "message": "The record already exists"}, step="Cloudflare DNS Record Creation"), "test-cf-conflict.example.com"),
        
        # Cloudflare Server Errors
        (CertApiException("Cloudflare server error 500", detail={"status": 500}, step="Cloudflare API Communication"), "test-cf-error-500.example.com"),
        (CertApiException("Cloudflare server error 502", detail={"status": 502}, step="Cloudflare API Communication"), "test-cf-error-502.example.com"),
        (CertApiException("Cloudflare server error 503", detail={"status": 503}, step="Cloudflare API Communication"), "test-cf-error-503.example.com"),
        
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
    ]
)
def test_error_resilience(webserver_for_error_tests, error, hostname):
    """Test that various errors during certificate issuance don't crash the application and fallback to self-signed"""
    webserver = webserver_for_error_tests
    
    with patch.object(webserver.ssl_processor.ssl.cert_manager, 'issue_certificate', side_effect=error):
        hosts = [Host(hostname=hostname, port=443, scheme={"https"})]
        
        webserver.ssl_processor.process_ssl_certificates(hosts)
        
        # Should fallback to self-signed
        assert hosts[0].ssl_file == f"{hostname}.selfsigned"
        assert hostname in webserver.ssl_processor.self_signed


# ============================================================================
# Resilience Verification Tests
# ============================================================================

@pytest.mark.parametrize(
    "error, hostname",
    [
        # Sample ACME Errors
        (CertApiException("ACME error", step="Test"), "test-continue-acme.example.com"),
        (CertApiException("Rate limit exceeded", detail={"type": "urn:ietf:params:acme:error:rateLimited"}, step="ACME Certificate Request"), "test-continue-rate-limit.example.com"),
        
        # Sample Runtime Errors
        (ZeroDivisionError("Division by zero in library"), "test-continue-div-zero.example.com"),
        (AttributeError("Accessing non-existing attribute in library"), "test-continue-attr-error.example.com"),
        (IndexError("Index out of bounds in library"), "test-continue-index-error.example.com"),
    ]
)
def test_webserver_continues_after_errors(docker_client: DockerTestClient, error, hostname):
    """Test that webserver continues running after various errors"""
    docker_client.networks.create("frontend")
    
    with patch("nginx_proxy.WebServer.WebServer.loadconfig") as mock_loadconfig, \
         patch("certapi.manager.acme_cert_manager.AcmeCertManager.setup") as mock_acme_setup:
        mock_acme_setup.return_value = None
        mock_loadconfig.return_value = {
            "dummy_nginx": True,
            "ssl_dir": "./.run_data",
            "conf_dir": "./run_data",
            "client_max_body_size": "1m",
            "challenge_dir": "./.run_data/acme-challenges/",
            "default_server": True,
            "vhosts_template_dir": "./vhosts_template",
        }
        
        webserver = WebServer(docker_client, nginx_update_throtle_sec=0.1)
        
        # Simulate error
        with patch.object(webserver.ssl_processor.ssl.cert_manager, 'issue_certificate', side_effect=error):
            hosts = [Host(hostname=hostname, port=443, scheme={"https"})]
            webserver.ssl_processor.process_ssl_certificates(hosts)
        
        # Webserver should still be functional - add a container
        container = docker_client.containers.run(
            "nginx:alpine",
            name="test_resilience_container",
            environment={"VIRTUAL_HOST": "test-resilience.example.com"},
            network="frontend"
        )
        time.sleep(0.2)
        
        # Verify nginx config is still valid
        config = HttpBlock.parse(webserver.nginx.current_config)
        assert len(config.servers) >= 1
        
        # Cleanup
        container.remove(force=True)
        docker_client.close()
        webserver.cleanup()
        if webserver.ssl_processor.ssl.certificate_expiry_thread.is_alive():
            webserver.ssl_processor.ssl.certificate_expiry_thread.join(timeout=2)


@pytest.mark.parametrize(
    "error, hostname",
    [
        # Sample ACME Errors
        (CertApiException("ACME failure", step="Test"), "test-fallback-acme.example.com"),
        (CertApiException("Authorization failed", detail={"type": "urn:ietf:params:acme:error:unauthorized"}, step="ACME Authorization"), "test-fallback-auth-fail.example.com"),
        
        # Sample Runtime Errors
        (ZeroDivisionError("Division by zero in library"), "test-fallback-div-zero.example.com"),
        (AttributeError("Accessing non-existing attribute in library"), "test-fallback-attr-error.example.com"),
        (IndexError("Index out of bounds in library"), "test-fallback-index-error.example.com"),
    ]
)
def test_fallback_to_selfsigned_on_failure(webserver_for_error_tests, error, hostname):
    """Test that system falls back to self-signed certificates on various failures"""
    webserver = webserver_for_error_tests
    
    with patch.object(webserver.ssl_processor.ssl.cert_manager, 'issue_certificate', side_effect=error):
        hosts = [Host(hostname=hostname, port=443, scheme={"https"})]
        
        webserver.ssl_processor.process_ssl_certificates(hosts)
        
        # Should have self-signed certificate
        assert hosts[0].ssl_file == f"{hostname}.selfsigned"
        assert hostname in webserver.ssl_processor.self_signed


def test_blacklist_prevents_repeated_failures(webserver_for_error_tests):
    """Test that blacklist mechanism prevents repeated certificate request failures"""
    webserver = webserver_for_error_tests
    ssl = webserver.ssl_processor.ssl
    
    # First attempt should fail and add to blacklist
    error = CertApiException("ACME failure", step="Test")
    with patch.object(ssl.cert_manager, 'issue_certificate', side_effect=error) as mock_issue:
        result = ssl.register_certificate_or_selfsign(["test-blacklist.example.com"])
        
        # Should have attempted once
        assert mock_issue.call_count == 1
    
    # Verify domain is in blacklist by checking it won't be retried immediately
    # Second attempt within blacklist period should skip the domain
    with patch.object(ssl.cert_manager, 'issue_certificate', side_effect=error) as mock_issue:
        result = ssl.register_certificate_or_selfsign(["test-blacklist.example.com"])
        
        # Should not attempt again (blacklisted)
        assert mock_issue.call_count == 0


def test_multiple_simultaneous_cert_errors(webserver_for_error_tests):
    """Test handling of multiple certificate failures simultaneously"""
    webserver = webserver_for_error_tests
    
    error = CertApiException("ACME failure", step="Test")
    
    with patch.object(webserver.ssl_processor.ssl.cert_manager, 'issue_certificate', side_effect=error):
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
            assert host.hostname in webserver.ssl_processor.self_signed


def test_wildcard_cert_error_handling(webserver_for_error_tests):
    """Test that wildcard certificate errors are handled gracefully"""
    webserver = webserver_for_error_tests
    
    error = CertApiException("Wildcard cert failure", step="Test")
    
    with patch.object(webserver.ssl_processor.ssl, 'register_certificate', side_effect=error):
        hosts = [Host(hostname="*.wildcard-test.example.com", port=443, scheme={"https"})]
        
        # Should not crash
        webserver.ssl_processor.process_ssl_certificates(hosts)
        
        # Should fallback to self-signed
        assert hosts[0].ssl_file == "*.wildcard-test.example.com.selfsigned"