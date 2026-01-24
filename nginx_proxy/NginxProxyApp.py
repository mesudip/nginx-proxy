import os
import re
import signal
import subprocess
import sys
import threading  # Re-adding threading for start_background
import traceback
import time
from typing import TypedDict

import docker
from docker import DockerClient

from nginx_proxy.WebServer import WebServer
from nginx_proxy.DockerEventListener import DockerEventListener
from nginx_proxy.NginxConfig import render_nginx_conf


class NginxProxyAppConfig(TypedDict):
    """Configuration for the NginxProxyApp loaded from environment variables."""

    cert_renew_threshold_days: int
    dummy_nginx: bool
    ssl_dir: str
    ssl_certs_dir: str
    ssl_key_dir: str
    conf_dir: str
    client_max_body_size: str
    challenge_dir: str
    default_server: bool
    vhosts_template_dir: str
    certapi_url: str
    wellknown_path: str
    enable_ipv6: bool


def _strip_end(s: str, char="/") -> str:
    return s[:-1] if s.endswith(char) else s


class NginxProxyApp:
    def __init__(self):
        self.server: WebServer | None = None
        self.docker_event_listener: DockerEventListener | None = None
        self.config: NginxProxyAppConfig = self._loadconfig()
        self._setup_nginx_conf()
        self.docker_client = self._init_docker_client()

    def _loadconfig(self) -> NginxProxyAppConfig:
        """
        Load application configuration from environment variables.
        """
        certapi_url = os.getenv("CERTAPI_URL", "").strip()
        wellknown_path = os.getenv("WELLKNOWN_PATH", "/.well-known/acme-challenge/").strip()
        # Ensure wellknown_path starts with / and ends with /
        if not wellknown_path.startswith("/"):
            wellknown_path = "/" + wellknown_path
        if not wellknown_path.endswith("/"):
            wellknown_path = wellknown_path + "/"

        ssl_dir = _strip_end(os.getenv("SSL_DIR", "/etc/ssl").strip())
        ssl_certs_dir = os.getenv("SSL_CERTS_DIR", ssl_dir + "/certs").strip()
        ssl_key_dir = os.getenv("SSL_KEY_DIR", ssl_dir + "/private").strip()

        return NginxProxyAppConfig(
            cert_renew_threshold_days=int(os.getenv("CERT_RENEW_THRESHOLD_DAYS", "30").strip()),
            dummy_nginx=os.getenv("DUMMY_NGINX") is not None,
            ssl_dir=ssl_dir,
            ssl_certs_dir=ssl_certs_dir,
            ssl_key_dir=ssl_key_dir,
            conf_dir=_strip_end(os.getenv("NGINX_CONF_DIR", "/etc/nginx").strip()),
            client_max_body_size=os.getenv("CLIENT_MAX_BODY_SIZE", "1m").strip(),
            challenge_dir=_strip_end(os.getenv("CHALLENGE_DIR", "/etc/nginx/challenges").strip())
            + "/",  # the nginx challenge dir must end with a /
            default_server=os.getenv("DEFAULT_HOST", "true").strip().lower() == "true",
            vhosts_template_dir=_strip_end(os.getenv("VHOSTS_TEMPLATE_DIR", "./vhosts_template").strip()),
            certapi_url=certapi_url,
            wellknown_path=wellknown_path,
            enable_ipv6=os.getenv("ENABLE_IPV6", "false").strip().lower() == "true",
        )

    def _setup_nginx_conf(self):
        """
        Render nginx.conf from template using environment variables.
        This allows customization of nginx settings like worker_processes and worker_connections.
        """
        template_path = os.path.join(self.config["vhosts_template_dir"], "nginx.conf.jinja2")
        output_path = os.path.join(self.config["conf_dir"], "nginx.conf")

        if os.path.exists(template_path):
            render_nginx_conf(template_path, output_path)
        else:
            print(f"[INFO] nginx.conf template not found at {template_path}, using existing nginx.conf")

    def _init_docker_client(self) -> DockerClient:
        try:
            client = docker.from_env()
            client.version()
            return client
        except Exception as e:
            print(
                "There was error connecting with the docker server \nHave you correctly mounted /var/run/docker.sock?\n"
                + str(e.args),
                file=sys.stderr,
            )
            sys.exit(1)

    def start(self):
        self.server = WebServer(self.docker_client, self.config)
        self.docker_event_listener = DockerEventListener(self.server, self.docker_client)

    def stop(self):
        print("Stopping NginxProxyApp...")
        self.cleanup()

    def cleanup(self):
        # No explicit stop for DockerEventListener needed as it's not a thread
        if self.server is not None:
            self.server.cleanup()
            self.server = None

    def run_forever(self):
        self.start()
        try:
            # Run the Docker event listener directly in the main thread
            if self.docker_event_listener:
                self.docker_event_listener.run()
        except (KeyboardInterrupt, SystemExit):
            print("-------------------------------\nPerforming Graceful ShutDown !!")
        finally:
            self.stop()
            print("---- See You ----")

    def start_background(self):
        """
        Starts the NginxProxyApp in a separate daemon thread.
        Returns the thread object.
        """
        print("Starting NginxProxyApp in background thread...")
        app_thread = threading.Thread(target=self.run_forever, daemon=True)
        app_thread.start()
        return app_thread
