import copy
import os
import re
import sys
from typing import List

import requests
from docker import DockerClient
from docker.models.containers import Container as DockerContainer
from jinja2 import Template

import nginx_proxy.post_processors as post_processors
import nginx_proxy.pre_processors as pre_processors
from nginx.Nginx import Nginx
from nginx_proxy import Container
from nginx_proxy import ProxyConfigData
from nginx_proxy.Host import Host


class WebServer():
    def __init__(self, client: DockerClient, *args):
        self.config = self.loadconfig()
        self.shouldExit = False
        self.client = client
        conf_file = "/etc/nginx/conf.d/default.conf"
        self.nginx = Nginx(conf_file, self.config['challenge_dir'])
        self.config_data = ProxyConfigData()
        self.services = set()
        self.networks = {}
        self.conf_file_name = "/etc/nginx/conf.d/default.conf"
        self.host_file = "/etc/hosts"
        file = open("vhosts_template/default.conf.jinja2")
        self.template = Template(file.read())
        file.close()
        self.learn_yourself()
        self.rescan_all_container()
        self.rescan_time = None
        self.ssl_processor = post_processors.SslCertificateProcessor(self.nginx, self, start_ssl_thread=False)
        self.basic_auth_processor = post_processors.BasicAuthProcessor()
        self.redirect_processor = post_processors.RedirectProcessor()

        if self.nginx.config_test():
            if not self.nginx.start():
                print("ERROR: Config test succeded but nginx failed to start", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
                exit(1)
            if len(self.nginx.last_working_config) < 50:
                print("Writing default config before reloading server.")
                if not self.nginx.forced_update(self.template.render(config=self.config)):
                    print("Nginx failed when reloaded with default config",file=sys.stderr)
                    print("Exiting .....", file=sys.stderr)
                    exit(1)
            self.reload()
        else:
            print("ERROR: Existing nginx configuration has error, trying to override with default configuration",
                  file=sys.stderr)
            if not self.nginx.forced_update(self.template.render(config=self.config)):
                print("Nginx failed when reloaded with default config", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
                exit(1)
        self.ssl_processor.certificate_expiry_thread.start()

    def learn_yourself(self):
        """
            Looks in it's own filesystem to find out the container in which it is running.
            Recognizing which container this code is running helps us to
            know the networks accessible from this container and find all other accessible containers.
        """
        try:
            file = open("/proc/self/cgroup")
            self.id = [l.strip() for l in file.readlines() if l.find("cpu") != -1][0].split("/")[-1]
            if len(self.id) > 64:
                slice = [x for x in re.split('[^a-fA-F0-9]', self.id) if len(x) is 64]
                if len(slice) is 1:
                    self.id = slice[0]
                else:
                    print("[ERROR] Couldn't parse container id from value :", self.id, file=sys.stderr)
                    raise Exception()
            self.container = self.client.containers.get(self.id)
            self.networks = [a for a in self.container.attrs["NetworkSettings"]["Networks"].keys()]
            self.networks = {self.client.networks.get(a).id: a for a in self.networks}
            file.close()
        except (KeyboardInterrupt, SystemExit) as e:
            raise e
        except Exception as e:
            print("[ERROR]Couldn't determine container ID of this container:", e.args,
                  "\n Is it running in docker environment?",
                  file=sys.stderr)
            print("Falling back to default network", file=sys.stderr)
            network = self.client.networks.get("frontend")
            self.networks[network.id] = "frontend"

    def _register_container(self, container: DockerContainer):
        """
         Find the details about container and register it and return True.
         If it's not configured with desired settings or is not accessible, return False
         @:returns True if the container is added to virtual hosts, false otherwise.
        """
        environments = Container.Container.get_env_map(container)
        known_networks = set(self.networks.keys())
        hosts = pre_processors.process_virtual_hosts(container, environments, known_networks)
        if len(hosts):
            pre_processors.process_default_server(container, environments, hosts)
            pre_processors.process_basic_auth(container, environments, hosts.config_map)
            pre_processors.process_redirection(container, environments, hosts.config_map)
            hosts.print()
            for h in hosts.host_list():
                self.config_data.add_host(h)
        return len(hosts) > 0

    # removes container from the maintained list.
    # this is called when a caontainer dies or leaves a known network
    def remove_container(self, container_id: str):
        deleted, deleted_domain = self.config_data.remove_container(container_id)
        if deleted:
            self.reload()

    def reload(self, forced=False) -> bool:
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        self.redirect_processor.process_redirection(self.config_data)
        hosts: List[Host] = []
        has_default = False
        for host_data in self.config_data.host_list():
            host = copy.deepcopy(host_data)
            host.upstreams = {}
            host.is_down = host_data.isempty()
            if 'default_server' in host.extras:
                if has_default:
                    del host.extras['default_server']
                else:
                    has_default = True
            for i, location in enumerate(host.locations.values()):
                location.container = list(location.containers)[0]
                if len(location.containers) > 1:
                    location.upstream = host_data.hostname + "-" + str(host.port) + "-" + str(i + 1)
                    host.upstreams[location.upstream] = location.containers
                else:
                    location.upstream = False
            host.upstreams = [{"id": x, "containers": y} for x, y in host.upstreams.items()]
            hosts.append(host)

        self.basic_auth_processor.process_basic_auth(hosts)
        self.ssl_processor.process_ssl_certificates(hosts)
        output = self.template.render(virtual_servers=hosts, config=self.config, add_default_server=not has_default)
        if forced:
            response = self.nginx.forced_update(output)
        else:
            response = self.nginx.update_config(output)
        return response

    def disconnect(self, network, container, scope):
        if container == self.id:
            if network in self.networks:
                # it's weird that the disconnect log is sent twice. this this check is  necessary
                del self.networks[network]
                self.rescan_and_reload()
        elif container in self.containers and network in self.networks:
            if not self.update_container(container):
                self.remove_container(container)
                self.reload()

    def connect(self, network, container, scope):
        if container == self.id:
            if network not in self.networks:
                self.networks[network] = self.client.networks.get(network).name
                self.rescan_and_reload()
        elif network in self.networks:
            self.update_container(container)

    def update_container(self, container_id):
        '''
        Rescan the container to detect changes. And update nginx configuration if necessary.
        This is usually called in one of the following conditions:
        -- new container was started
        -- an existing container has left a network in which nginx-proxy is connected.
        -- during  full container rescan
        :param container container id to update
        :return: true if container state change affected the nginx configuration else false
        '''
        try:
            if not self.config_data.has_container(container_id):
                if self._register_container(self.client.containers.get(container_id)):
                    self.reload()
                    return True
        except requests.exceptions.HTTPError as e:
            pass
        return False

    def rescan_all_container(self):
        '''
        Rescan all the containers to detect changes. And update nginx configuration if necessary.
        This is called in one of the following conditions:
        -- in the beginnig of execution of this program
        -- nginx-proxy container itself joins or leaves some network.
        :return:
        '''
        containers = self.client.containers.list()
        self.containers = set()
        self.hosts = {}
        for container in containers:
            self._register_container(container)

    def rescan_and_reload(self):
        self.rescan_all_container()
        return self.reload()

    def cleanup(self):
        self.ssl_processor.shutdown()

    def loadconfig(self):
        return {
            'client_max_body_size': os.getenv("CLIENT_MAX_BODY_SIZE", "1m"),
            'challenge_dir': os.getenv("CHALLENGE_DIR", "/tmp/acme-challenges")
        }
