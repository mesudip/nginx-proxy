import copy
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable, List, TYPE_CHECKING

import docker
import requests
from docker import DockerClient
from jinja2 import Template

import nginx_proxy.post_processors as post_processors
import nginx_proxy.pre_processors as pre_processors
from nginx.Nginx import Nginx
from nginx.DummyNginx import DummyNginx
from nginx import Url
from nginx_proxy.BackendTarget import BackendTarget
from nginx_proxy import ProxyConfigData
from nginx_proxy.Host import Host
from nginx_proxy.Throttler import Throttler

if TYPE_CHECKING:
    from nginx_proxy.NginxProxyApp import NginxProxyAppConfig


class WebServer:
    """
    In nginx-proxy Webserver is the controller class to manage nginx config and nginx process
    Events are sent to this class by DockerEventListener or other injectors.
    """

    def __init__(
        self,
        docker_client: DockerClient,
        config: "NginxProxyAppConfig",
        nginx_update_throtle_sec=5,
        swarm_client: DockerClient = None,
    ):
        self.config = config
        self.shouldExit = False
        self.client = docker_client
        self.swarm_client = swarm_client if swarm_client is not None else docker_client
        self.reload_interval = nginx_update_throtle_sec
        self.throttler = Throttler(self.reload_interval)
        NginxClass = DummyNginx if self.config["dummy_nginx"] else Nginx
        self.nginx: Nginx | DummyNginx = NginxClass(
            self.config["conf_dir"] + "/conf.d/nginx-proxy.conf", self.config["challenge_dir"]
        )
        self.config_data = ProxyConfigData()
        self._reload_dispatcher: Callable | None = None
        self._is_reload_dispatcher_thread: Callable[[], bool] | None = None
        self.services = set()
        self.networks = {}
        vhosts_template_path = os.path.join(self.config["vhosts_template_dir"], "default.conf.jinja2")
        with open(vhosts_template_path) as f:
            self.template = Template(f.read())

        error_template_path = os.path.join(self.config["vhosts_template_dir"], "error.conf.jinja2")
        with open(error_template_path) as f:
            self.error_template = Template(f.read())

        self.learn_yourself()
        self.ssl_processor = post_processors.SslCertificateProcessor(
            self.nginx,
            self,
            start_ssl_thread=False,
            ssl_dir=self.config["ssl_dir"],
            update_threshold_days=self.config["cert_renew_threshold_days"],
        )
        self.basic_auth_processor = post_processors.BasicAuthProcessor(self.config["conf_dir"] + "/basic_auth")
        self.redirect_processor = post_processors.RedirectProcessor()
        self.upstream_processor = post_processors.UpstreamProcessor()

        # Render default config for Nginx setup
        default_nginx_config = self.template.render(config=self.config)
        if not self.nginx.setup(default_nginx_config):
            print("Nginx setup failed. Exiting.", file=sys.stderr)
            sys.exit(1)

        print("Reachable Networks :", self.networks)
        self.setup_error_config()
        self.rescan_and_reload(force=True, bypass_start_grace=True)
        self.ssl_processor.start()

    def set_reload_dispatcher(self, dispatcher: Callable | None, is_dispatcher_thread: Callable[[], bool] | None):
        self._reload_dispatcher = dispatcher
        self._is_reload_dispatcher_thread = is_dispatcher_thread

    def setup_error_config(self):
        # Render error.conf.jinja2 and save it
        rendered_error_conf_path = os.path.join(self.config["conf_dir"], "error.conf")
        with open(rendered_error_conf_path, "w") as f:
            f.write(self.error_template.render(config=self.config))

        # Pass the path to the rendered error file to the main template
        self.config["rendered_error_conf_path"] = rendered_error_conf_path

    def _ensure_https_redirects(self, hosts: List[Host]) -> List[Host]:
        redirect_hosts: List[Host] = []
        http_hosts = {(host.hostname, int(host.port)): host for host in hosts if int(host.port) == 80}

        for host in hosts:
            if host.is_redirect or not host.secured or int(host.port) == 80:
                continue
            redirect_target = Url({"https"}, host.hostname, int(host.port), "/")
            http_host = http_hosts.get((host.hostname, 80))
            if http_host is None:
                redirect_host = Host(host.hostname, 80)
                redirect_host.full_redirect = redirect_target
                redirect_host.update_extras_content("redirect_status_code", "308")
                # Added after redirect post-processing, so mark it explicitly for template rendering.
                redirect_host.is_redirect = True
                redirect_hosts.append(redirect_host)
                http_hosts[(host.hostname, 80)] = redirect_host
                continue

            if "/" not in http_host.locations:
                http_host.update_extras_content("default_redirect_target", redirect_target)

        return hosts + redirect_hosts

    def _render_config(
        self,
        config_data: ProxyConfigData | None = None,
        update_ssl_watch_domains: bool = True,
        dry_run: bool = False,
        dry_run_auth_files: List[str] | None = None,
    ) -> str:
        render_config_data = copy.deepcopy(config_data if config_data is not None else self.config_data)
        render_config = copy.deepcopy(self.config)
        self.redirect_processor.process_redirection(render_config_data)

        hosts: List[Host] = []
        has_default = False
        for host_data in render_config_data.host_list():
            host = copy.deepcopy(host_data)
            host.is_down = host_data.isempty()
            if "default_server" in host.extras:
                if has_default:
                    del host.extras["default_server"]
                else:
                    has_default = True
            for location in host.locations.values():
                location.container = list(location.backends)[0]
            hosts.append(host)

        upstreams = self.upstream_processor.process(
            hosts, prefer_local=render_config.get("docker_swarm") == "prefer-local"
        )
        self.basic_auth_processor.process_basic_auth(hosts, dry_run=dry_run, created_files=dry_run_auth_files)
        self.ssl_processor.process_ssl_certificates(hosts, update_watch_domains=update_ssl_watch_domains)
        if dry_run:
            self._ensure_selfsigned_certificate_files(hosts)
        hosts = self._ensure_https_redirects(hosts)
        render_config["default_server"] = not has_default
        if not dry_run:
            self.config["default_server"] = render_config["default_server"]

        return self.template.render(
            virtual_servers=hosts,
            upstreams=upstreams,
            config=render_config,
        )

    def _validate_config_data(self, config_data: ProxyConfigData, backend: BackendTarget | None = None) -> bool:
        dry_run_auth_files: List[str] = []
        output = self._render_config(
            config_data,
            update_ssl_watch_domains=False,
            dry_run=True,
            dry_run_auth_files=dry_run_auth_files,
        )
        validation = self.nginx.validate_config(output)
        if isinstance(validation, tuple):
            valid, error = validation
        else:
            valid, error = bool(validation), None

        if valid:
            return True

        self._cleanup_dry_run_auth_files(dry_run_auth_files)

        if backend is not None:
            print(
                "WARN: Ignoring backend update because generated nginx config failed validation "
                f"type={backend.type} id={backend.id[:12]} name={backend.name}",
                file=sys.stderr,
            )
        else:
            print("WARN: Generated nginx config failed validation", file=sys.stderr)
        return False

    def _cleanup_dry_run_auth_files(self, file_paths: List[str]) -> None:
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                folder_path = os.path.dirname(file_path)
                if os.path.isdir(folder_path) and not os.listdir(folder_path):
                    os.rmdir(folder_path)
            except OSError as e:
                print(f"WARN: Could not clean up dry-run basic auth file {file_path}: {e}", file=sys.stderr)

    def _ensure_selfsigned_certificate_files(self, hosts: List[Host]) -> None:
        certs_dir = self.config.get("ssl_certs_dir") or os.path.join(self.config["ssl_dir"], "certs")
        keys_dir = self.config.get("ssl_key_dir") or os.path.join(self.config["ssl_dir"], "private")
        os.makedirs(certs_dir, exist_ok=True)
        os.makedirs(keys_dir, exist_ok=True)

        for host in hosts:
            ssl_file = getattr(host, "ssl_file", None)
            if not host.secured or not ssl_file or not str(ssl_file).endswith(".selfsigned"):
                continue

            cert_path = os.path.join(certs_dir, ssl_file + ".crt")
            key_path = os.path.join(keys_dir, ssl_file + ".key")
            if os.path.exists(cert_path) and os.path.exists(key_path):
                continue

            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-nodes",
                    "-newkey",
                    "rsa:2048",
                    "-days",
                    "30",
                    "-subj",
                    f"/CN={host.hostname}",
                    "-keyout",
                    key_path,
                    "-out",
                    cert_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _do_reload(self, forced=False, validate=True) -> bool:
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        # print("web_server._do_reload(forced="+str(forced)+")")
        output = self._render_config(self.config_data, update_ssl_watch_domains=True)
        response = self.nginx.update_config(output, force=forced, validate=validate)
        return response

    def learn_yourself(self):
        """
        Looks in it's own filesystem to find out the container in which it is running.
        Recognizing which container this code is running helps us to
        know the networks accessible from this container and find all other accessible containers.
        """
        try:
            if self.client is None:
                raise Exception("No local docker client available")
            hostname = os.getenv("HOSTNAME")
            if hostname is None:
                print("[ERROR] HOSTNAME environment variable is not set")
                raise Exception()
            self.container = self.client.containers.get(hostname)
            self.id = self.container.id
            networks = [a for a in self.container.attrs["NetworkSettings"]["Networks"].keys()]
            for network in networks:
                print("Registering network: ", network)
                net_detail = self.client.networks.get(network)
                self.networks[net_detail.id] = net_detail.name
                self.networks[net_detail.name] = net_detail.id
        except (KeyboardInterrupt, SystemExit) as e:
            raise e
        except Exception as e:
            self.id = None
            print(
                "[ERROR]Couldn't determine container ID of this container:",
                e.args if len(e.args) else "",
                "\n Is it running in docker environment?",
                file=sys.stderr,
            )
            print("Falling back to default network: frontend", file=sys.stderr)
            default_network = "frontend"
            network = self.client.networks.get(default_network)
            self.networks[network.id] = default_network
            self.networks[default_network] = network.id

    def register_backend(self, backend: BackendTarget, config_data: ProxyConfigData | None = None):
        """
        Find the details about container and register it and return True.
        If it's not configured with desired settings or is not accessible, return False
        @:returns True if the container is added to virtual hosts, false otherwise.
        """
        # print("_regiser_container("+str(container.name))
        known_networks = set(self.networks.keys())
        environments = backend.env
        hosts = pre_processors.process_virtual_hosts(backend, known_networks)
        if len(hosts):
            pre_processors.process_default_server(backend, environments, hosts)
            pre_processors.process_basic_auth(backend, environments, hosts.config_map)
            pre_processors.process_redirection(backend, environments, hosts.config_map)
            hosts.print()
            target_config_data = config_data if config_data is not None else self.config_data
            for h in hosts.host_list():
                self._remove_static_root_if_overridden(target_config_data, h)
                target_config_data.add_host(h)
        return len(hosts) > 0

    # removes container from the maintained list.
    # this is called when a caontainer dies or leaves a known network
    def remove_backend(self, container_id: str):
        deleted, deleted_domain = self._remove_backend_without_reload(container_id)
        if deleted:
            self._register_static_sites()
            service_id = deleted.labels.get("com.docker.swarm.service.id")
            has_service_id = isinstance(service_id, str) and bool(service_id)
            is_service = deleted.type == "service" or has_service_id
            label = "Service removed     " if is_service else "Container removed   "
            display_id = service_id[:12] if has_service_id else container_id[:12]
            print(
                label,
                "Id:" + display_id,
                "    " + deleted.name,
                sep="\t",
            )
            self.reload()

    def _remove_backend_without_reload(self, container_id: str):
        return self.config_data.remove_backend(container_id)

    def reload(self, immediate=False, force=False, validate=True) -> bool:
        """
        Schedules or performs a reload of the Nginx configuration.
        Returns True if a reload was initiated or scheduled.
        """

        return self.throttler.throttle(lambda: self._do_reload(force, validate=validate), immediate=immediate or force)

    def enqueue_reload(self, force=False) -> bool:
        if self._reload_dispatcher is None:
            return self.reload(immediate=force, force=force)
        if self._is_reload_dispatcher_thread is not None and self._is_reload_dispatcher_thread():
            return self.reload(immediate=force, force=force)

        from nginx_proxy.DockerEventListener import Reload

        return self._reload_dispatcher(Reload(force))

    def disconnect(self, network, container, scope):
        if self.id is not None and container == self.id:
            if network in self.networks:
                print("Nginx Proxy removed from network ", self.networks[network])
                print("Connected Networks:", self.networks)
                # it's weird that the disconnect log is sent twice. this this check is  necessary
                rev_id = self.networks[network]
                del self.networks[network]
                del self.networks[rev_id]
                self.rescan_and_reload()
        elif network in self.networks:
            swarm_mode = self.config.get("docker_swarm", "ignore")
            if swarm_mode == "strict":
                return
            if self.config_data.has_backend(container):
                try:
                    backend = BackendTarget.from_container(self.client.containers.get(container))
                    if not self.update_backend(backend, replace_existing=True):
                        self.remove_backend(
                            container
                        )  # remove_backend not implemented yet, using remove_container (it takes ID)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    print(f"Error processing disconnect for container {container}: {e}", file=sys.stderr)

    def connect(self, network, container, scope):
        if self.id is not None and container == self.id:
            if network not in self.networks:
                new_network = self.client.networks.get(network)
                self.networks[new_network.id] = new_network.name
                self.networks[new_network.name] = new_network.id
                self.rescan_and_reload()
        elif network in self.networks:
            swarm_mode = self.config.get("docker_swarm", "ignore")
            if swarm_mode == "strict":
                return
            try:
                container_obj = self.client.containers.get(container)
                if container_obj.status != "running":
                    return
                if (
                    self._container_has_healthcheck(container_obj)
                    and self._container_health_status(container_obj) != "healthy"
                ):
                    return
                if swarm_mode not in (
                    "ignore",
                    "prefer-local",
                ) and "com.docker.swarm.service.id" in container_obj.attrs["Config"].get("Labels", {}):
                    # print(f"Skipping network connect for service task container {container}")
                    return
                backend = BackendTarget.from_container(container_obj)
                self.update_backend(backend, replace_existing=True)
            except docker.errors.NotFound:
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print(f"Error processing connect for container {container}: {e}", file=sys.stderr)

    def update_backend(self, backend: BackendTarget, replace_existing: bool = False, reload: bool = True):
        """
        Rescan the backend to detect changes. And update nginx configuration if necessary.
        :param backend: BackendTarget object
        :return: true if state change affected the nginx configuration else false
        """
        try:
            candidate_config_data = copy.deepcopy(self.config_data)
            existing_backend = candidate_config_data.has_backend(backend.id)
            if existing_backend and backend.type != "service" and not replace_existing:
                return False

            removed = None
            if existing_backend:
                removed, _ = candidate_config_data.remove_backend(backend.id)

            registered = self.register_backend(backend, candidate_config_data)
            if registered or removed:
                if not self._validate_config_data(candidate_config_data, backend):
                    return False
                self.config_data = candidate_config_data
                if reload:
                    self.reload(validate=False)
                return True
        except requests.exceptions.HTTPError as e:
            pass
        return False

    def rescan_all_container(self, bypass_start_grace=False):
        """
        Rescan all the containers and services to detect changes.
        Previously this only did containers, but now it's a full rescan for consistency.
        """
        swarm_mode = self.config.get("docker_swarm", "ignore")
        backends: List[BackendTarget] = []

        # 1. Register local containers (unless in strict swarm mode)
        if swarm_mode != "strict" and self.client is not None:
            try:
                containers = self.client.containers.list()
                for container in containers:
                    if swarm_mode not in (
                        "ignore",
                        "prefer-local",
                    ) and "com.docker.swarm.service.id" in container.attrs["Config"].get("Labels", {}):
                        continue
                    if not self._should_register_container_now(container, bypass_start_grace=bypass_start_grace):
                        continue
                    backend = BackendTarget.from_container(container)
                    backends.append(backend)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print(f"Error scanning containers: {e}", file=sys.stderr)

        # 2. Register swarm services (if enable, prefer-local, or strict)
        if swarm_mode in ("enable", "prefer-local", "strict"):
            try:
                info = self.swarm_client.info()
                swarm_info = info.get("Swarm", {})
                node_state = swarm_info.get("LocalNodeState", "inactive")
                # ControlAvailable is usually present if it's a manager
                is_manager = swarm_info.get("ControlAvailable", False)

                if node_state == "active" and is_manager:
                    services = self.swarm_client.services.list()
                    for service in services:
                        backend = BackendTarget.from_service(service)
                        backends.append(backend)
                elif node_state == "active":
                    # If node is active but not manager, we can't list services on this client.
                    # However, if we have a remote swarm_client, it might be a manager.
                    # But self.swarm_client.info() would have returned is_manager=True if it were.
                    pass
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print(f"Error scanning services: {e}", file=sys.stderr)

        self.config_data = ProxyConfigData()
        for backend in backends:
            self.update_backend(backend, replace_existing=True, reload=False)
        self._register_static_sites()

    def _register_static_sites(self):
        candidate_config_data = copy.deepcopy(self.config_data)
        self._add_static_sites_to_config(candidate_config_data)
        if self._validate_config_data(candidate_config_data):
            self.config_data = candidate_config_data

    def _add_static_sites_to_config(self, config_data: ProxyConfigData):
        static_hosts = pre_processors.process_static_sites(self.config.get("static_site_root", "/static"))
        for host in static_hosts.host_list():
            self._register_static_host(host, config_data)

        default_ssl_hosts = pre_processors.process_default_ssl_domains(
            self.config.get("default_ssl_domains", []),
            os.path.join(self.config["vhosts_template_dir"], "errors"),
        )
        for host in default_ssl_hosts.host_list():
            self._register_static_host(host, config_data)

    def _register_static_host(self, host: Host, config_data: ProxyConfigData | None = None):
        target_config_data = config_data if config_data is not None else self.config_data
        existing_host = target_config_data.getHost(host.hostname, host.port)
        if existing_host is None:
            target_config_data.add_host(host)
            return

        if "/" in existing_host.locations:
            if self._location_has_static_site(existing_host.locations["/"]):
                return
            print(
                "[static-site] WARNING: Static site root skipped for "
                f"{host.hostname}:{host.port}. Existing / location overrides "
                f"{host.locations['/'].backends[0].path}",
                file=sys.stderr,
            )
            return

        target_config_data.add_host(host)

    @staticmethod
    def _location_has_static_site(location) -> bool:
        return any(backend.type == "static_site" for backend in location.backends)

    def _remove_static_root_if_overridden(self, config_data: ProxyConfigData, incoming_host: Host) -> None:
        incoming_root = incoming_host.locations.get("/")
        if incoming_root is None or self._location_has_static_site(incoming_root):
            return

        existing_host = config_data.getHost(incoming_host.hostname, incoming_host.port)
        if existing_host is None:
            return

        existing_root = existing_host.locations.get("/")
        if existing_root is None or not self._location_has_static_site(existing_root):
            return

        static_backends = [backend for backend in existing_root.backends if backend.type == "static_site"]
        static_paths = ", ".join(backend.path for backend in static_backends)
        print(
            "[static-site] WARNING: Container route overrides static site root "
            f"{incoming_host.hostname}:{incoming_host.port}/ ({static_paths})",
            file=sys.stderr,
        )
        for backend in static_backends:
            config_data.backends.discard(backend.id)
            existing_host.container_set.discard(backend.id)
        del existing_host.locations["/"]

    def rescan_services(self):
        """
        Included for compatibility with DockerEventListener. Calls rescan_all_container
        to perform a unified full rescan.
        """
        self.rescan_all_container(bypass_start_grace=True)

    def rescan_and_reload(self, force=False, bypass_start_grace=True):
        self.rescan_all_container(bypass_start_grace=bypass_start_grace)
        return self.reload(immediate=force, force=force)

    def cleanup(self):
        self.throttler.shutdown()
        self.ssl_processor.shutdown()
        self.nginx.stop()

    def _should_register_container_now(self, container, bypass_start_grace=False) -> bool:
        if not self._container_is_running(container):
            return False
        if self._container_has_healthcheck(container):
            return self._container_health_status(container) == "healthy"
        if bypass_start_grace:
            return True
        grace_seconds = float(self.config.get("backend_start_grace_seconds", 0) or 0)
        if grace_seconds <= 0:
            return True
        started_at = container.attrs.get("State", {}).get("StartedAt")
        if not started_at:
            return True
        try:
            started_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - started_time).total_seconds() >= grace_seconds

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
