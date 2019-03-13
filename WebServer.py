from docker import DockerClient
from Host import Host
from jinja2 import Template
import sys
import copy
from nginx.Nginx import Nginx
import requests
from SSL import SSL

class WebServer():
    def __init__(self, client: DockerClient, *args):
        self.client = client
        self.nginx = Nginx("/etc/nginx/conf.d/default.conf")
        self.ssl=SSL("/etc/ssl/private","/etc/nginx/conf.d/acme-nginx.conf")
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
        if self.nginx.config_test():
            if not self.nginx.start():
                print("ERROR: Config test succeded but nginx failed to start",file=sys.stderr)
                print("Exiting .....",file=sys.stderr)
            self.reload()
        else:
            print ("ERROR: Existing nginx configuration has error, trying to override with new configuration")
            if not self.reload(forced=True):
                print("ERROR: Existing nginx configuration has error", file=sys.stderr)
                print("ERROR: New generated configuration also has error",file=sys.stderr)
                print("Please check the configuration of your containers and restart this container",file=sys.stderr)
                print("EXITING .....",file=sys.stderr)
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
        new_host = Host.from_container(container, known_networks=self.networks.keys())

        if new_host:
            if "com.docker.swarm.service.name" in container.attrs["Config"]["Labels"]:
                id = container.attrs["Config"]["Labels"]["com.docker.swarm.service.name"]
                self.services.add(id)
            else:
                self.containers[new_host.id] = ""
            if new_host.server_name in self.hosts:
                existing_host = self.hosts[new_host.server_name]
                for location, location_content in existing_host.locations.items():
                    if location not in new_host.locations:
                        new_host.locations[location] = location_content
                new_host.ssl_host = existing_host.ssl_host if existing_host.ssl_host is not None else new_host.ssl_host
            self.hosts[new_host.server_name] = new_host

            return True
        return False

    def remove_container(self, event):
        if event["id"] in self.containers:
            del self.containers[event["id"]]
            for host in self.hosts.values():
                for location, location_content in host.locations.items():
                    if location_content.host_id == event["id"]:
                        break
                else:
                    continue
                break
            else:
                return
            del host.locations[location]
            if not len(host.locations):
                del self.hosts[host.server_name]
            self.reload()

    def reload(self,forced=False) -> bool:
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        host_list = [copy.copy(host) for host in self.hosts.values()]
        hosts = set()
        for host in host_list:
            host.locations = list(host.locations.values())
            if host.ssl_host:
                self.ssl.registerCertificate(host.ssl_host)
        output = self.template.render(virtual_servers=host_list)
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
