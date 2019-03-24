from docker import DockerClient

from nginx_proxy import Container
from nginx_proxy.Host import Host
from jinja2 import Template
import sys
import copy
from nginx.Nginx import Nginx
import requests
from nginx_proxy.SSL import SSL
import datetime


class WebServer():
    def __init__(self, client: DockerClient, *args):
        self.client = client
        self.nginx = Nginx("/etc/nginx/conf.d/default.conf")
        self.ssl = SSL("ssl", "/etc/nginx/conf.d/acme-nginx.conf")
        self.containers = {}
        self.services = set()
        self.networks = {}
        self.conf_file_name = "/etc/nginx/conf.d/default.conf"
        self.host_file = "/etc/hosts"
        self.hosts = {}
        file = open("default.conf.template")
        self.template = Template(file.read())
        file.close()
        self.learn_yourself()
        self.rescan_all_container()
        self.rescan_time = None
        if self.nginx.config_test():
            if not self.nginx.start():
                print("ERROR: Config test succeded but nginx failed to start", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
            self.reload()
        else:
            print("ERROR: Existing nginx configuration has error, trying to override with new configuration")
            if not self.reload(forced=True):
                print("ERROR: Existing nginx configuration has error", file=sys.stderr)
                print("ERROR: New generated configuration also has error", file=sys.stderr)
                print("Please check the configuration of your containers and restart this container", file=sys.stderr)
                print("EXITING .....", file=sys.stderr)
                exit(1)

    def learn_yourself(self):
        try:
            file = open("/proc/self/cgroup")
            self.id = [l for l in file.read().split("\n") if l.find("cpu") != -1][0].split("/")[-1]
            self.container = self.client.containers.get(self.id)
            self.networks = [a for a in self.container.attrs["NetworkSettings"]["Networks"].keys()]
            self.networks = {self.client.networks.get(a).id: a for a in self.networks}
            file.close()
        except Exception as e:
            print("Couldn't determine container ID of this container. Is it running in docker environment?",
                  file=sys.stderr)
            print("Falling back to default network", file=sys.stderr)
            network = self.client.networks.get("frontend")
            self.networks[network.id] = "frontend"

    def _register_container(self, container):
        # if it's a service container we can skip it.
        try:
            scheme, hostname, port, location, mapping = Container.Container.get_contaier_info(container,
                                                                                              known_networks=self.networks.keys())

            if "com.docker.swarm.service.name" in container.attrs["Config"]["Labels"]:
                id = container.attrs["Config"]["Labels"]["com.docker.swarm.service.name"]
                self.services.add(id)
            else:
                self.containers[container.id] = ""

            if (hostname, port) in self.hosts:
                host: Host = self.hosts[(hostname, port)]
                host.add_container(location, mapping)
            else:
                host: Host = Host(client=self.client, hostname=hostname, port=port, scheme=scheme)
                host.add_container(location, mapping)
                self.hosts[(hostname, port)] = host

        except Container.UnconfiguredContainer as ignore:
            pass
        return False

    def remove_container(self, container):
        if container["id"] in self.containers:
            del self.containers[container]
            for host in self.hosts.values():
                a: Host = host
                if a.remove_container(self.containers[container]):
                    break
            else:
                return
            self.reload()

    def reload(self, forced=False) -> bool:
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        host_list = [copy.deepcopy(host) for host in self.hosts.values()]

        next_reload = None
        now = datetime.datetime.now()
        for host in host_list:
            host.locations = list(host.locations.values())
            host.upstreams = {}
            for i, location in enumerate(host.locations):
                location.container = list(location.containers)[0]
                if len(location.containers) > 1:
                    location.upstream = host.hostname + "-" + host.port + "-" + str(i + 1)
                    host.upstreams[location.upstream] = location.containers
                else:
                    location.upstream = False
            host.upstreams = [{"id": x, "containers": y} for x, y in host.upstreams.items()]
            if host.scheme == "https":
                if host.port == 80 or host.port == 443 or host.port is None:
                    host.ssl_redirect = True
                    host.port = 443
                host.ssl_host = True
                expiry = self.ssl.expiry_time(host.hostname)
                remain = expiry - now
                if remain.days < 5:
                    self.ssl.register_certificate(host.hostname)
                    self.hosts[host].ssl_expiry = self.ssl.expiry_time(host.hostname)
                else:
                    self.hosts[host].ssl_expiry = expiry
                if next_reload:
                    if next_reload > expiry:
                        next_reload = expiry
                else:
                    next_reload = expiry

        output = self.template.render(virtual_servers=host_list)
        print(self.template.render(virtual_servers=host_list))
        self.rescan_time = next_reload
        if forced:
            return self.nginx.forced_update(output)
        else:
            return self.nginx.update_config(output)

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
            self.networks[network] = self.client.networks.get(network).name
            self.rescan_and_reload()
        elif container not in self.containers and network in self.networks:
            if self.update_container(container):
                self.reload()

    def update_container(self, container):
        '''
        Rescan the container to detect changes. And update nginx configuration if necessary.
        This is usually called in one of the following conditions:
        -- new container was started
        -- an existing container has left a network in which nginx-proxy is connected.
        -- during container list rescan
        :param container container id to update
        :return: true if container state change affected the nginx configuration else false
        '''
        try:
            if self._register_container(self.client.containers.get(container)):
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
        for container in containers:
            self._register_container(container)

    def rescan_and_reload(self):
        self.rescan_all_container()
        return self.reload()
