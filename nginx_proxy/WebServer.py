import copy
import os
import sys
import threading
from typing import List, TYPE_CHECKING

import requests
from docker import DockerClient
from jinja2 import Template

import nginx_proxy.post_processors as post_processors
import nginx_proxy.pre_processors as pre_processors
from nginx.Nginx import Nginx
from nginx.DummyNginx import DummyNginx
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
        self._lock = threading.Lock()
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
            start_ssl_thread=True,
            ssl_dir=self.config["ssl_dir"],
            update_threshold_days=self.config["cert_renew_threshold_days"],
        )
        self.basic_auth_processor = post_processors.BasicAuthProcessor(self.config["conf_dir"] + "/basic_auth")
        self.redirect_processor = post_processors.RedirectProcessor()
        self.sticky_session_processor = post_processors.StickySessionProcessor()

        # Render default config for Nginx setup
        default_nginx_config = self.template.render(config=self.config)
        if not self.nginx.setup(default_nginx_config):
            print("Nginx setup failed. Exiting.", file=sys.stderr)
            sys.exit(1)

        print("Reachable Networks :", self.networks)
        self.setup_error_config()
        self.rescan_and_reload(force=True)

    def setup_error_config(self):
        # Render error.conf.jinja2 and save it
        rendered_error_conf_path = os.path.join(self.config["conf_dir"], "error.conf")
        with open(rendered_error_conf_path, "w") as f:
            f.write(self.error_template.render(config=self.config))

        # Pass the path to the rendered error file to the main template
        self.config["rendered_error_conf_path"] = rendered_error_conf_path

    def _do_reload(self, forced=False, has_addition=True) -> bool:
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        # print("web_server._do_reload(forced="+str(forced)+")")
        self.redirect_processor.process_redirection(self.config_data)
        hosts: List[Host] = []
        has_default = False
        for host_data in self.config_data.host_list():
            host = copy.deepcopy(host_data)
            host.is_down = host_data.isempty()
            if "default_server" in host.extras:
                if has_default:
                    del host.extras["default_server"]
                else:
                    has_default = True
            for i, location in enumerate(host.locations.values()):
                location.container = list(location.backends)[0]
            hosts.append(host)

        upstreams = self.sticky_session_processor.process(hosts)
        self.basic_auth_processor.process_basic_auth(hosts)
        self.ssl_processor.process_ssl_certificates(hosts)
        self.config["default_server"] = not has_default

        output = self.template.render(
            virtual_servers=hosts,
            upstreams=upstreams,
            config=self.config,
        )
        response = self.nginx.update_config(output, force=forced)
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

    def register_backend(self, backend: BackendTarget):
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
            for h in hosts.host_list():
                self.config_data.add_host(h)
        return len(hosts) > 0

    # removes container from the maintained list.
    # this is called when a caontainer dies or leaves a known network
    def remove_backend(self, container_id: str):
        deleted, deleted_domain = self.config_data.remove_backend(container_id)
        if deleted:
            print(
                "Container removed   ",
                "Id:" + container_id[:12],
                "    " + deleted.name,
                sep="\t",
            )
            self.reload(has_addition=False)

    def reload(self, immediate=False, force=False, has_addition=True) -> bool:
        """
        Schedules or performs a reload of the Nginx configuration.
        Returns True if a reload was initiated or scheduled.
        """
        return self.throttler.throttle(lambda: self._do_reload(force, has_addition), immediate=immediate or force)

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
                    if not self.update_backend(backend):
                        self.remove_backend(
                            container
                        )  # remove_backend not implemented yet, using remove_container (it takes ID)
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
                if swarm_mode != "ignore" and "com.docker.swarm.service.id" in container_obj.attrs["Config"].get("Labels", {}):
                    # print(f"Skipping network connect for service task container {container}")
                    return
                backend = BackendTarget.from_container(container_obj)
                self.update_backend(backend)
            except Exception as e:
                print(f"Error processing connect for container {container}: {e}", file=sys.stderr)

    def update_backend(self, backend: BackendTarget):
        """
        Rescan the backend to detect changes. And update nginx configuration if necessary.
        :param backend: BackendTarget object
        :return: true if state change affected the nginx configuration else false
        """
        try:
            if not self.config_data.has_backend(backend.id):
                if self.register_backend(backend):
                    self.reload()
                    return True
        except requests.exceptions.HTTPError as e:
            pass
        return False

    def rescan_all_container(self):
        """
        Rescan all the containers and services to detect changes. 
        Previously this only did containers, but now it's a full rescan for consistency.
        """
        swarm_mode = self.config.get("docker_swarm", "ignore")
        with self._lock:
            # Clear previous state to ensure we don't leak dead containers/services
            self.config_data.clear()
            
            # 1. Register local containers (unless in strict swarm mode)
            if swarm_mode != "strict" and self.client is not None:
                try:
                    containers = self.client.containers.list()
                    for container in containers:
                        if swarm_mode != "ignore" and "com.docker.swarm.service.id" in container.attrs["Config"].get("Labels", {}):
                            continue
                        backend = BackendTarget.from_container(container)
                        self.register_backend(backend)
                except Exception as e:
                    print(f"Error scanning containers: {e}", file=sys.stderr)

            # 2. Register swarm services (if enable or strict)
            if swarm_mode in ("enable", "strict"):
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
                            self.register_backend(backend)
                    elif node_state == "active":
                        # If node is active but not manager, we can't list services on this client.
                        # However, if we have a remote swarm_client, it might be a manager.
                        # But self.swarm_client.info() would have returned is_manager=True if it were.
                        pass
                except Exception as e:
                    print(f"Error scanning services: {e}", file=sys.stderr)

    def rescan_services(self):
        """
        Included for compatibility with DockerEventListener. Calls rescan_all_container
        to perform a unified full rescan.
        """
        self.rescan_all_container()

    def rescan_and_reload(self, force=False):
        self.rescan_all_container()
        return self.reload(force)

    def cleanup(self):
        self.throttler.shutdown()
        self.ssl_processor.shutdown()
        self.nginx.stop()
