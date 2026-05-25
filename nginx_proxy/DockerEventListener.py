import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from typing import Any

import docker

from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy.WebServer import WebServer


SERVICE_EVENT_DELAY_SECONDS = 5
SERVICE_EVENT_RETRY_DELAY_SECONDS = 20
SERVICE_EVENT_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ServiceEvent:
    action: str
    event: dict[str, Any]


@dataclass(frozen=True)
class ContainerEvent:
    action: str
    event: dict[str, Any]


@dataclass(frozen=True)
class ContainerHealthEvent:
    action: str
    event: dict[str, Any]


@dataclass(frozen=True)
class NetworkEvent:
    action: str
    event: dict[str, Any]


@dataclass(frozen=True)
class ActivateBackend:
    container_id: str
    generation: int


@dataclass(frozen=True)
class ProcessServiceUpsert:
    service_id: str
    action: str
    attempt: int
    generation: int


@dataclass(frozen=True)
class RemoveBackend:
    backend_id: str


@dataclass(frozen=True)
class RescanAndReload:
    force: bool = False
    bypass_start_grace: bool = True


@dataclass(frozen=True)
class Reload:
    force: bool = False


_STOP = object()


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
        self._command_queue: queue.Queue = queue.Queue()
        self._dispatcher_thread: threading.Thread | None = None
        self._dispatcher_stop = threading.Event()
        self._dispatcher_thread_id: int | None = None
        self._pending_backend_timers: dict[str, threading.Timer] = {}
        self._pending_backend_generations: dict[str, int] = {}
        self._pending_service_timers: dict[str, threading.Timer] = {}
        self._pending_service_generations: dict[str, int] = {}
        self._waiting_for_healthy: set[str] = set()
        self._started_containers: set[str] = self._load_started_container_ids()
        self.web_server.docker_event_listener = self

    def start_dispatcher(self):
        if self.is_dispatcher_running():
            return
        self._dispatcher_stop.clear()
        self.web_server.set_reload_dispatcher(self.enqueue, self.is_dispatcher_thread)
        self._dispatcher_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher_thread.start()

    def stop_dispatcher(self):
        self._dispatcher_stop.set()
        self._cancel_all_timers()
        self._command_queue.put(_STOP)
        if self._dispatcher_thread is not None:
            self._dispatcher_thread.join(timeout=5)
            self._dispatcher_thread = None
        self._dispatcher_thread_id = None
        self.web_server.set_reload_dispatcher(None, None)

    def is_dispatcher_running(self) -> bool:
        return self._dispatcher_thread is not None and self._dispatcher_thread.is_alive()

    def is_dispatcher_thread(self) -> bool:
        return self._dispatcher_thread_id == threading.get_ident()

    def enqueue(self, command):
        self._command_queue.put(command)
        return True

    def drain_commands(self, limit: int = 100):
        processed = 0
        while processed < limit:
            try:
                command = self._command_queue.get_nowait()
            except queue.Empty:
                return processed
            if command is _STOP:
                self._command_queue.task_done()
                continue
            self._dispatch(command)
            self._command_queue.task_done()
            processed += 1
        return processed

    def _dispatch_loop(self):
        self._dispatcher_thread_id = threading.get_ident()
        while True:
            command = self._command_queue.get()
            try:
                if command is _STOP:
                    return
                self._dispatch(command)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print("Unexpected dispatcher error :" + e.__class__.__name__ + " -> " + str(e), file=sys.stderr)
                traceback.print_exc(limit=10)
            finally:
                self._command_queue.task_done()

    def _dispatch(self, command):
        if isinstance(command, ServiceEvent):
            self._process_service_event(command.action, command.event)
        elif isinstance(command, ContainerEvent):
            self._process_container_event(command.action, command.event)
        elif isinstance(command, ContainerHealthEvent):
            self._process_container_health_event(command.action, command.event)
        elif isinstance(command, NetworkEvent):
            self._process_network_event(command.action, command.event)
        elif isinstance(command, ActivateBackend):
            self._process_backend_activation(command.container_id, command.generation)
        elif isinstance(command, ProcessServiceUpsert):
            self._process_scheduled_service_upsert(
                command.service_id, command.action, command.attempt, command.generation
            )
        elif isinstance(command, RemoveBackend):
            self.web_server.remove_backend(command.backend_id)
        elif isinstance(command, RescanAndReload):
            self.web_server.rescan_all_container(bypass_start_grace=command.bypass_start_grace)
            self.web_server._do_reload(command.force)
        elif isinstance(command, Reload):
            self.web_server._do_reload(command.force)
        elif callable(command):
            command()
        else:
            raise ValueError(f"Unknown DockerEventListener command: {command!r}")

    def run(self):
        self.start_dispatcher()
        try:
            swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
            if self.client == self.swarm_client:
                self._listen(self.client)
            else:
                threads = []
                if swarm_mode != "strict" and self.client is not None:
                    t1 = threading.Thread(target=self._listen, args=(self.client,), daemon=True)
                    t1.start()
                    threads.append(t1)

                if swarm_mode in ("enable", "prefer-local", "strict") and self.swarm_client is not None:
                    t2 = threading.Thread(target=self._listen, args=(self.swarm_client,), daemon=True)
                    t2.start()
                    threads.append(t2)

                for t in threads:
                    t.join()
        finally:
            self.stop_dispatcher()

    def _listen(self, client):
        client_url = getattr(getattr(client, "api", None), "base_url", "unknown")
        print(f"Starting Docker event listener loop for client {client_url}")

        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        types = []
        events = ["health_status"]  # common events

        if client == self.swarm_client and swarm_mode in ("enable", "prefer-local", "strict"):
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

                if eventType == "service":
                    self.enqueue(ServiceEvent(eventAction, event))
                elif eventType == "network":
                    self.enqueue(NetworkEvent(eventAction, event))
                elif eventType == "container":
                    if eventAction and eventAction.startswith("health_status"):
                        self.enqueue(ContainerHealthEvent(eventAction, event))
                    else:
                        self.enqueue(ContainerEvent(eventAction, event))

            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print("Unexpected error :" + e.__class__.__name__ + " -> " + str(e), file=sys.stderr)
                traceback.print_exc(limit=10)
        print(f"Docker event listener loop stopped for client {client_url}")

    def _process_service_event(self, action, event):
        service_id = event.get("Actor", {}).get("ID") or event.get("id")
        if action in ("create", "update"):
            self._schedule_service_processing(service_id, action, SERVICE_EVENT_DELAY_SECONDS, attempt=1)
        elif action == "remove":
            self._cancel_pending_service_processing(service_id)
            self.web_server.remove_backend(service_id)

    def _schedule_service_processing(self, service_id: str, action: str, delay_seconds: float, attempt: int):
        self._cancel_pending_service_processing(service_id)
        generation = self._pending_service_generations.get(service_id, 0) + 1
        self._pending_service_generations[service_id] = generation

        def process():
            self.enqueue(ProcessServiceUpsert(service_id, action, attempt, generation))

        timer = threading.Timer(delay_seconds, process)
        timer.daemon = True
        self._pending_service_timers[service_id] = timer
        timer.start()

    def _cancel_pending_service_processing(self, service_id: str):
        timer = self._pending_service_timers.pop(service_id, None)
        if timer is not None:
            timer.cancel()
        self._pending_service_generations.pop(service_id, None)

    def _cancel_all_timers(self):
        for timer in self._pending_backend_timers.values():
            timer.cancel()
        for timer in self._pending_service_timers.values():
            timer.cancel()
        self._pending_backend_timers.clear()
        self._pending_backend_generations.clear()
        self._pending_service_timers.clear()
        self._pending_service_generations.clear()

    def _process_scheduled_service_upsert(self, service_id: str, action: str, attempt: int, generation: int):
        if self._pending_service_generations.get(service_id) != generation:
            return
        self._pending_service_timers.pop(service_id, None)
        self._pending_service_generations.pop(service_id, None)
        self._process_service_upsert(service_id, action, attempt)

    def _process_service_upsert(self, service_id: str, action: str, attempt: int):
        try:
            service = self.swarm_client.services.get(service_id)
            backend = BackendTarget.from_service(service)
            if not self._service_backend_has_reachable_vip(backend) and self._retry_service_event(
                service_id, action, attempt, "has no reachable VIP"
            ):
                return
            self.web_server.update_backend(backend)
        except docker.errors.NotFound:
            if not self._retry_service_event(service_id, action, attempt, "not found"):
                print(f"WARN: Service {service_id} not found ...", file=sys.stderr)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            print(f"Error processing service event {action} for {service_id}: {e}", file=sys.stderr)

    def _retry_service_event(self, service_id: str, action: str, attempt: int, reason: str) -> bool:
        if attempt >= SERVICE_EVENT_MAX_ATTEMPTS:
            return False
        print(
            f"WARN: Service {service_id} {reason}; retrying in {SERVICE_EVENT_RETRY_DELAY_SECONDS}s",
            file=sys.stderr,
        )
        self._schedule_service_processing(
            service_id,
            action,
            SERVICE_EVENT_RETRY_DELAY_SECONDS,
            attempt=attempt + 1,
        )
        return True

    def _service_backend_has_reachable_vip(self, backend: BackendTarget) -> bool:
        known_networks = set(self.web_server.networks.keys())
        for detail in backend.network_settings.values():
            if detail.get("NetworkID") in known_networks and detail.get("IPAddress"):
                return True
        return False

    def _process_container_event(self, action, event):
        container_id = event.get("Actor", {}).get("ID") or event.get("id")
        attributes = event.get("Actor", {}).get("Attributes", {})

        swarm_mode = self.web_server.config.get("docker_swarm", "ignore")
        if swarm_mode not in ("ignore", "prefer-local") and "com.docker.swarm.service.id" in attributes:
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
        if swarm_mode not in ("ignore", "prefer-local") and "com.docker.swarm.service.id" in attributes:
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
                    self._log_container_event(
                        "Container waiting   ", container_id, container=container, detail="for healthy"
                    )
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
        generation = self._pending_backend_generations.get(container_id, 0) + 1
        self._pending_backend_generations[container_id] = generation

        def activate():
            self.enqueue(ActivateBackend(container_id, generation))

        timer = threading.Timer(grace_seconds, activate)
        timer.daemon = True
        self._pending_backend_timers[container_id] = timer
        timer.start()

    def _cancel_pending_backend_activation(self, container_id: str):
        timer = self._pending_backend_timers.pop(container_id, None)
        if timer is not None:
            timer.cancel()
        self._pending_backend_generations.pop(container_id, None)

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

    def _process_backend_activation(self, container_id: str, generation: int):
        if self._pending_backend_generations.get(container_id) != generation:
            return
        self._pending_backend_timers.pop(container_id, None)
        self._pending_backend_generations.pop(container_id, None)
        self._activate_backend_if_running(container_id)

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

    def _log_container_event(
        self, label: str, container_id: str, container=None, attributes=None, detail: str | None = None
    ):
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
        except Exception as e:
            self._log_unexpected_error(f"Could not resolve container name for {container_id}", e)
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
            print(f"WARN: Ignoring network connect for invalid container id {container_id!r}", file=sys.stderr)
            return False
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            self._log_unexpected_error(f"Could not inspect container {container_id} for network connect", e)
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
        if swarm_mode not in ("ignore", "prefer-local") and "com.docker.swarm.service.id" in labels:
            return False
        return True

    def _is_pending_startup(self, container_id: str) -> bool:
        return container_id in self._pending_backend_timers or container_id in self._waiting_for_healthy

    def _load_started_container_ids(self) -> set[str]:
        try:
            return {
                container.id for container in self.client.containers.list() if self._container_is_running(container)
            }
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            self._log_unexpected_error("Could not load running containers during Docker event listener startup", e)
            return set()

    @staticmethod
    def _log_unexpected_error(message: str, error: Exception):
        print(f"WARN: {message}: {error.__class__.__name__} -> {error}", file=sys.stderr)
        traceback.print_exc(limit=10)
