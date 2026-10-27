"""Run with nginx and CertAPI server dependencies installed; uses only loopback ports."""

import ipaddress
import runpy
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from jinja2 import Template

pytest.importorskip("flask_restx")
from flask import Flask
from flask_restx import Api, Namespace
from werkzeug.serving import make_server
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from certapi import AcmeCertManager, FileSystemKeyStore, FilesystemChallengeSolver, Key, SelfCertIssuer
from certapi.server.api import create_api_resources
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.Host import Host
from nginx_proxy.certapi_config import load_certapi_config
from nginx.Nginx import Nginx
from nginx_proxy.post_processors.ssl_certificate_processor import SslCertificateProcessor

ROOT = Path(__file__).resolve().parents[1]


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the renewal thread"
        time.sleep(0.05)


def tls_context(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "tls.crt", tmp_path / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    seen_sni = []
    context.set_servername_callback(lambda sock, name, ctx: seen_sni.append(name))
    return context, cert_path, seen_sni


@pytest.mark.parametrize("backend_mode", ["local", "http", "http-runtime", "https"])
def test_commands_through_real_nginx_and_certapi(tmp_path, monkeypatch, backend_mode):
    nginx_binary = shutil.which("nginx")
    if not nginx_binary:
        pytest.skip("nginx executable is required")
    solver = FilesystemChallengeSolver(str(tmp_path / "challenges"))
    issuer = SelfCertIssuer(Key.generate("ecdsa"), common_name="test CA")
    manager = AcmeCertManager(FileSystemKeyStore(str(tmp_path / "server-ssl")), issuer, [solver])
    app = Flask(__name__)
    api = Api(app)
    namespace = Namespace("api")
    api.add_namespace(namespace)
    create_api_resources(namespace, manager)

    @app.get("/.well-known/acme-challenge/<token>")
    def challenge(token):
        value = solver.get_challenge(token)
        return (value, 200) if value is not None else ("", 404)

    context, ca_bundle, sni = tls_context(tmp_path)
    upstream = make_server("127.0.0.1", 0, app, threaded=True, ssl_context=context if backend_mode == "https" else None)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    proxy = None
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            proxy_port = sock.getsockname()[1]
        certapi_url = f"{'https' if backend_mode == 'https' else 'http'}://127.0.0.1:{upstream.server_port}"
        certapi = load_certapi_config(certapi_url) if backend_mode != "local" else None
        if certapi:
            # The TLS service's DNS name must be sent as SNI even with an IP upstream.
            certapi["host"] = "localhost"
        error_conf = tmp_path / "errors.conf"
        error_conf.write_text("")
        auth = tmp_path / "htpasswd"
        auth.write_text("test:invalid-password-hash\n")
        down = Host("down.example", 80)
        down.is_down = True
        protected = Host("protected.example", 80)
        protected.is_down = False
        protected.extras = {"security": True, "security_file": str(auth)}
        protected.add_container(
            "/", BackendTarget("test-backend", address="127.0.0.1", port=upstream.server_port, path="/")
        )
        protected.locations["/"].container = protected.locations["/"].backends[0]
        redirect = Host("redirect.example", 80)
        redirect.is_redirect = True
        redirect.full_redirect = Host("target.example", 443, {"https"})
        config = dict(
            certapi=certapi,
            nginx_resolvers=["127.0.0.1"] if backend_mode == "http-runtime" else [],
            wellknown_path="/.well-known/acme-challenge/",
            challenge_dir=str(tmp_path / "challenges") + "/",
            rendered_error_conf_path=str(error_conf),
            client_max_body_size="1m",
            default_server=True,
            enable_ipv6=False,
            vhosts_template_dir=str(tmp_path),
            ssl_certs_dir=str(tmp_path),
            ssl_key_dir=str(tmp_path),
        )
        rendered = Template((ROOT / "vhosts_template/default.conf.jinja2").read_text()).render(
            config=config, virtual_servers=[down, protected, redirect], upstreams=[]
        )
        rendered = rendered.replace("listen 80", f"listen 127.0.0.1:{proxy_port}").replace(
            "access_log /var/log/nginx/access.log;", "access_log off;"
        )
        conf = tmp_path / "nginx.conf"
        conf.write_text(
            f"pid {tmp_path / 'nginx.pid'};\nerror_log {tmp_path / 'error.log'};\nevents {{}}\nhttp {{\n{rendered}\n}}"
        )
        check = subprocess.run(
            [nginx_binary, "-e", str(tmp_path / "error.log"), "-t", "-p", str(tmp_path), "-c", str(conf)],
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stderr
        proxy = subprocess.Popen(
            [
                nginx_binary,
                "-e",
                str(tmp_path / "error.log"),
                "-p",
                str(tmp_path),
                "-c",
                str(conf),
                "-g",
                "daemon off;",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        session = requests.Session()
        session.trust_env = False
        deadline = time.monotonic() + 3
        while True:
            try:
                session.get(f"http://127.0.0.1:{proxy_port}/", timeout=0.2)
                break
            except requests.ConnectionError:
                if time.monotonic() >= deadline:
                    pytest.fail("nginx did not start: " + (tmp_path / "error.log").read_text())
                time.sleep(0.02)
        seen = []

        def route_probe(url, **kwargs):
            # Map public DNS + port 80 to this isolated high-port nginx listener.
            from urllib.parse import urlparse

            parsed = urlparse(url)
            seen.append(parsed.hostname)
            return session.get(
                f"http://127.0.0.1:{proxy_port}{parsed.path}", headers={"Host": parsed.hostname}, **kwargs
            )

        monkeypatch.setattr(requests, "get", route_probe)
        monkeypatch.setenv("CHALLENGE_DIR", str(tmp_path / "challenges"))
        monkeypatch.setenv("NGINX_CONF_DIR", str(tmp_path))
        monkeypatch.setenv("SSL_DIR", str(tmp_path / "proxy-ssl"))
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_bundle))
        if certapi:
            monkeypatch.setenv("CERTAPI_URL", certapi_url)
        else:
            monkeypatch.delenv("CERTAPI_URL", raising=False)
        domains = ["down.example", "protected.example", "redirect.example", "default.example"]
        monkeypatch.setattr(sys, "argv", ["verify", *domains])
        assert runpy.run_path(str(ROOT / "verify"))["main"]() == 0
        assert seen == domains
        assert list((tmp_path / "challenges").iterdir()) == []
        assert session.get(f"http://127.0.0.1:{proxy_port}/", headers={"Host": "down.example"}).status_code == 503
        assert session.get(f"http://127.0.0.1:{proxy_port}/", headers={"Host": "protected.example"}).status_code == 401
        assert (
            session.get(
                f"http://127.0.0.1:{proxy_port}/", headers={"Host": "redirect.example"}, allow_redirects=False
            ).status_code
            == 301
        )
        if certapi:
            monkeypatch.setattr("nginx.Nginx.Nginx.reload", lambda self: True)
            monkeypatch.setattr(sys, "argv", ["getssl", "down.example"])
            assert runpy.run_path(str(ROOT / "getssl"))["main"]() == 0
            key_store = FileSystemKeyStore(str(tmp_path / "proxy-ssl"), keys_dir_name="private")
            assert key_store.find_key_and_cert_by_domain("down.example") is not None
            # Reuse path must also persist the existing certificate into the proxy's keystore.
            assert runpy.run_path(str(ROOT / "getssl"))["main"]() == 0
            assert list((tmp_path / "challenges").iterdir()) == []
            manager.obtain = Mock(wraps=manager.obtain)
            monkeypatch.setenv("CERTAPI_BATCH_DOMAINS", "false")
            processor = SslCertificateProcessor(
                Nginx(str(conf), create_challenge_dir=False),
                SimpleNamespace(config={"certapi": load_certapi_config(certapi_url)}, enqueue_reload=Mock()),
                ssl_dir=str(tmp_path / "automatic-ssl"),
            )
            try:
                host = Host("protected.example", 443, {"https"})
                processor.process_ssl_certificates([host])
                # The render does not wait for CertAPI: self-signed first, forced reload once the cert is in.
                assert host.ssl_file == "protected.example.selfsigned"
                processor.start()
                wait_for(lambda: processor.server.enqueue_reload.called)
                processor.server.enqueue_reload.assert_called_once_with(force=True)
                processor.process_ssl_certificates([host])
                assert host.ssl_file == "protected.example"
                assert processor.key_store.find_key_and_cert_by_domain("protected.example") is not None
                assert manager.obtain.call_args.kwargs["self_verify"] is True
                assert manager.obtain.call_args.kwargs["skip_failing"] is True
                assert manager.obtain.call_args.kwargs["batch_domains"] is False
            finally:
                processor.shutdown()
        if backend_mode == "https":
            assert "localhost" in sni
    finally:
        if proxy:
            proxy.terminate()
            proxy.wait(timeout=3)
        upstream.shutdown()
        upstream_thread.join(timeout=3)
