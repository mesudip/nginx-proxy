import os
import re
import subprocess
import sys
import threading
import traceback

import docker

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.WebServer import WebServer


class DockerEventListener:
    def __init__(
        self,
        web_server: WebServer,
        docker_client: docker.DockerClient,
        swarm_client: docker.DockerClient = None,
    ):
        self.web_server = web_server
        self.client = docker_client
        self.swarm_client = swarm_client if swarm_client is not None else docker_client
        self.lock = threading.Lock()

    def run(self):
        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        if self.client == self.swarm_client:
            self._listen(self.client)
        else:
            threads = []
            if swarm_mode != "strict" and self.client is not None:
                t1 = threading.Thread(target=self._listen, args=(self.client,), daemon=True)
                t1.start()
                threads.append(t1)

            if swarm_mode in ("enable", "strict") and self.swarm_client is not None:
                t2 = threading.Thread(target=self._listen, args=(self.swarm_client,), daemon=True)
                t2.start()
                threads.append(t2)

            for t in threads:
                t.join()

    def _listen(self, client):
        client_url = getattr(getattr(client, "api", None), "base_url", "unknown")
        print(f"Starting Docker event listener loop for client {client_url}")

        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        types = []
        events = ["health_status"]  # common events

        if client == self.swarm_client and swarm_mode in ("enable", "strict"):
            types.append("service")
            events.extend(["create", "update", "remove"])

        if client == self.client:
            types.append("network")
            events.extend(["connect", "disconnect"])
            if swarm_mode != "strict":
                types.append("container")
                events.extend(["start", "stop", "die", "destroy"])

        if not types:
            print(f"No relevant event types to listen for client {client_url}")
            return

        filters = {
            "type": list(set(types)),
            "event": list(set(events)),
        }
        for event in client.events(decode=True, filters=filters):
            try:
                eventType = event.get("Type")
                eventAction = event.get("Action")

                with self.lock:
                    if eventType == "service":
                        self._process_service_event(eventAction, event)
                    elif eventType == "network":
                        self._process_network_event(eventAction, event)
                    elif eventType == "container":
                        if eventAction == "health_status":
                            # self._process_container_health_event(event)
                            continue
                        else:
                            self._process_container_event(eventAction, event)

            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print("Unexpected error :" + e.__class__.__name__ + " -> " + str(e), file=sys.stderr)
                traceback.print_exc(limit=10)
        print(f"Docker event listener loop stopped for client {client_url}")

    def _process_service_event(self, action, event):
        service_id = event.get("Actor", {}).get("ID") or event.get("id")
        if action in ("create", "update"):
            try:
                service = self.swarm_client.services.get(service_id)
                backend = BackendTarget.from_service(service)
                self.web_server.update_backend(backend)
            except Exception as e:
                print(f"Error processing service event {action} for {service_id}: {e}", file=sys.stderr)
        elif action == "remove":
            self.web_server.remove_backend(service_id)

    def _process_container_event(self, action, event):
        container_id = event.get("Actor", {}).get("ID") or event.get("id")
        attributes = event.get("Actor", {}).get("Attributes", {})
        
        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        if swarm_mode != "ignore" and "com.docker.swarm.service.id" in attributes:
            # print(f"Skipping event {action} for service task container {container_id}")
            return

        if action == "start":
            # print("container started", event["id"])
            try:
                container = self.client.containers.get(container_id)
                backend = BackendTarget.from_container(container)
                self.web_server.update_backend(backend)
            except Exception as e:
                print(f"Error processing container event {action} for {container_id}: {e}", file=sys.stderr)
        elif action == "stop" or action == "die" or action == "destroy":
            self.web_server.remove_backend(container_id)

    def _process_network_event(self, action, event):
        if action == "create":
            # print("network created")
            pass
        elif "container" in event["Actor"]["Attributes"]:
            if action == "disconnect":
                # print("network disconnect")
                self.web_server.disconnect(
                    network=event["Actor"]["ID"],
                    container=event["Actor"]["Attributes"]["container"],
                    scope=event["scope"],
                )
            elif action == "connect":
                # print("network connect")
                self.web_server.connect(
                    network=event["Actor"]["ID"],
                    container=event["Actor"]["Attributes"]["container"],
                    scope=event["scope"],
                )
        elif action == "destroy":
            # print("network destryed")
            pass
