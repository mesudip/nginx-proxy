import os
import re
import signal
import subprocess
import sys
import threading # Re-adding threading for start_background
import traceback
import time

import docker
from docker import DockerClient

from nginx_proxy.WebServer import WebServer
from nginx_proxy.DockerEventListener import DockerEventListener


class NginxProxyApp:
    def __init__(self):
        self.server = None
        self.docker_event_listener = None
        self.docker_client = self._init_docker_client()

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
        self.server = WebServer(self.docker_client)
        self.docker_event_listener = DockerEventListener(self.server, self.docker_client)


    def stop(self):
        print("Stopping NginxProxyApp...")
        self.cleanup()
        print("NginxProxyApp stopped.")

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
