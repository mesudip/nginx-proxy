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
        self._pending_backend_timers: dict[str, threading.Timer] = {}
        self._waiting_for_healthy: set[str] = set()
        self._started_containers: set[str] = self._load_started_container_ids()

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
                        if eventAction and eventAction.startswith("health_status"):
                            self._process_container_health_event(eventAction, event)
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
            except docker.errors.NotFound:
                print(f"WARN: Service {service_id} not found ...", file=sys.stderr)
            except (KeyboardInterrupt, SystemExit):
                raise
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
            self._started_containers.add(container_id)
            self._handle_container_start(container_id, attributes)
        elif action == "stop" or action == "die" or action == "destroy":
            self._started_containers.discard(container_id)
            pending_startup = self._clear_pending_startup_state(container_id)
            if pending_startup:
                self._log_container_event("Container crashed   ", container_id, attributes=attributes)
            self.web_server.remove_backend(container_id)

    def _process_container_health_event(self, action, event):
        container_id = event.get("Actor", {}).get("ID") or event.get("id")
        attributes = event.get("Actor", {}).get("Attributes", {})

        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        if swarm_mode != "ignore" and "com.docker.swarm.service.id" in attributes:
            return

        health_status = (action or "").strip().lower().removeprefix("health_status:").strip()
        if health_status == "healthy":
            self._clear_pending_startup_state(container_id)
            self._activate_backend_if_running(container_id)
        elif health_status == "unhealthy":
            self.web_server.remove_backend(container_id)

    def _handle_container_start(self, container_id: str, attributes=None):
        try:
            container = self.client.containers.get(container_id)
            if self._container_has_healthcheck(container):
                if self._container_health_status(container) == "healthy":
                    self._activate_backend_if_running(container_id, container=container)
                else:
                    self._waiting_for_healthy.add(container_id)
                    self._log_container_event("Container waiting   ", container_id, container=container, detail="for healthy")
                return

            grace_seconds = float(self.web_server.config.get("backend_start_grace_seconds", 0) or 0)
            if grace_seconds > 0:
                self._schedule_backend_activation(container_id, grace_seconds)
                return

            self._activate_backend_if_running(container_id, container=container)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"Error processing container event start for {container_id}: {e}", file=sys.stderr)

    def _schedule_backend_activation(self, container_id: str, grace_seconds: float):
        self._clear_pending_startup_state(container_id)

        def activate():
            with self.lock:
                self._pending_backend_timers.pop(container_id, None)
                self._activate_backend_if_running(container_id)

        timer = threading.Timer(grace_seconds, activate)
        timer.daemon = True
        self._pending_backend_timers[container_id] = timer
        timer.start()

    def _cancel_pending_backend_activation(self, container_id: str):
        timer = self._pending_backend_timers.pop(container_id, None)
        if timer is not None:
            timer.cancel()

    def _clear_pending_startup_state(self, container_id: str) -> bool:
        had_pending_timer = container_id in self._pending_backend_timers
        was_waiting_for_healthy = container_id in self._waiting_for_healthy
        self._cancel_pending_backend_activation(container_id)
        self._waiting_for_healthy.discard(container_id)
        return had_pending_timer or was_waiting_for_healthy

    def _activate_backend_if_running(self, container_id: str, container=None):
        try:
            container = container or self.client.containers.get(container_id)
            if not self._container_is_running(container):
                return
            backend = BackendTarget.from_container(container)
            self.web_server.update_backend(backend)
        except docker.errors.NotFound:
            return
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"Error activating backend for {container_id}: {e}", file=sys.stderr)

    @staticmethod
    def _container_has_healthcheck(container) -> bool:
        healthcheck = container.attrs.get("Config", {}).get("Healthcheck")
        return bool(healthcheck and healthcheck.get("Test") not in (None, [], ["NONE"], ["NONE", ""]))

    @staticmethod
    def _container_health_status(container) -> str | None:
        return container.attrs.get("State", {}).get("Health", {}).get("Status")

    @staticmethod
    def _container_is_running(container) -> bool:
        state_status = container.attrs.get("State", {}).get("Status")
        return state_status == "running" or getattr(container, "status", None) == "running"

    def _log_container_event(self, label: str, container_id: str, container=None, attributes=None, detail: str | None = None):
        container_name = self._container_name(container=container, container_id=container_id, attributes=attributes)
        parts = [label, "Id:" + container_id[:12]]
        if container_name:
            parts.append("    " + container_name)
        if detail:
            parts.append(detail)
        print(*parts, sep="\t")

    def _container_name(self, container=None, container_id: str | None = None, attributes=None) -> str | None:
        if attributes and attributes.get("name"):
            return attributes["name"]
        if container is not None:
            name = getattr(container, "name", None) or container.attrs.get("Name")
            if isinstance(name, str):
                return name.lstrip("/")
        if container_id is None:
            return None
        try:
            resolved = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            return None
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return None
        name = getattr(resolved, "name", None) or resolved.attrs.get("Name")
        return name.lstrip("/") if isinstance(name, str) else None

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
                container_id = event["Actor"]["Attributes"]["container"]
                if self._should_forward_network_connect(container_id):
                    self.web_server.connect(
                        network=event["Actor"]["ID"],
                        container=container_id,
                        scope=event["scope"],
                    )
        elif action == "destroy":
            # print("network destryed")
            pass

    def _should_forward_network_connect(self, container_id: str) -> bool:
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            return False
        except ValueError:
            return False
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return False

        if not self._container_is_running(container):
            return False
        if self._container_has_healthcheck(container) and self._container_health_status(container) != "healthy":
            return False
        if container_id not in self._started_containers:
            return False
        if self._is_pending_startup(container_id):
            return False

        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        labels = container.attrs.get("Config", {}).get("Labels", {})
        if swarm_mode != "ignore" and "com.docker.swarm.service.id" in labels:
            return False
        return True

    def _is_pending_startup(self, container_id: str) -> bool:
        return container_id in self._pending_backend_timers or container_id in self._waiting_for_healthy

    def _load_started_container_ids(self) -> set[str]:
        try:
            return {container.id for container in self.client.containers.list() if self._container_is_running(container)}
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return set()
