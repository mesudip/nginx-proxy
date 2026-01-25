import os
import re
import subprocess
import sys
import traceback

import docker

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.WebServer import WebServer


class DockerEventListener:
    def __init__(self, web_server: WebServer, docker_client: docker.DockerClient):
        self.web_server = web_server
        self.client = docker_client

    def run(self):
        print("Starting Docker event listener loop.")
        filters = {
            "type": ["service", "network", "container"],
            "event": ["start", "stop", "create", "destroy", "die", "health_status", "connect", "disconnect"],
        }
        for event in self.client.events(decode=True, filters=filters):
            try:
                eventType = event.get("Type")
                eventAction = event.get("Action")
                # print("New event",eventType,eventAction)

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
        print("Docker event listener loop stopped.")

    def _process_service_event(self, action, event):
        service_id = event.get("Actor", {}).get("ID") or event.get("id")
        if action in ("create", "update"):
            try:
                service = self.client.services.get(service_id)
                backend = BackendTarget.from_service(service)
                self.web_server.update_backend(backend)
            except Exception as e:
                print(f"Error processing service event {action} for {service_id}: {e}", file=sys.stderr)
        elif action == "remove":
            self.web_server.remove_backend(service_id)

    def _process_container_event(self, action, event):
        container_id = event.get("Actor", {}).get("ID") or event.get("id")
        attributes = event.get("Actor", {}).get("Attributes", {})
        if "com.docker.swarm.service.id" in attributes:
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
