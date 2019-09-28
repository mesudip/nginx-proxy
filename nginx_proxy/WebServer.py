import copy
import datetime
import re
import sys
import threading

import requests
from docker import DockerClient
from jinja2 import Template

from nginx.Nginx import Nginx
from nginx_proxy import Container
from nginx_proxy.Host import Host
from nginx_proxy.SSL import SSL


class WebServer():
    def __init__(self, client: DockerClient, *args):
        self.shouldExit = False
        self.client = client
        self.nginx = Nginx("/etc/nginx/conf.d/default.conf")
        self.ssl = SSL("/etc/ssl", "/etc/nginx/conf.d/acme-nginx.conf", nginx=self.nginx)
        self.containers = set()
        self.services = set()
        self.networks = {}
        self.conf_file_name = "/etc/nginx/conf.d/default.conf"
        self.host_file = "/etc/hosts"
        self.hosts = {}
        self.ssl_certificates = {}
        self.self_signed_certificates = set()
        self.next_ssl_expiry = None
        file = open("vhosts_template/default.conf.template")
        self.template = Template(file.read())
        file.close()
        self.learn_yourself()
        self.rescan_all_container()
        self.rescan_time = None
        self.lock = threading.Condition()
        self.expiry_changed = threading.Event()
        self.certificate_expiry_thread = threading.Thread(target=self.check_certificate_expiry)

        if self.nginx.config_test():
            if not self.nginx.start():
                print("ERROR: Config test succeded but nginx failed to start", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
            self.reload()
        else:
            print("ERROR: Existing nginx configuration has error, trying to override with new configuration",
                  file=sys.stderr)
            if not self.reload(forced=True):
                print("ERROR: Existing nginx configuration has error", file=sys.stderr)
                print("ERROR: New generated configuration also has error", file=sys.stderr)
                print("Please check the configuration of your containers and restart this container", file=sys.stderr)
                print("EXITING .....", file=sys.stderr)
                exit(1)
        self.certificate_expiry_thread.start()

    def check_certificate_expiry(self):
        self.lock.acquire()
        while True:
            if self.shouldExit:
                return
            if self.next_ssl_expiry is None:
                print("[SSL Refresh Thread]  Looks like there no ssl certificates, Sleeping until  there's one")
                self.lock.wait()
            else:
                now = datetime.datetime.now()
                remaining_days = (self.next_ssl_expiry - now).days
                remaining_days = 30 if remaining_days > 30 else remaining_days

                if remaining_days > 2:
                    print(
                        "[SSL Refresh Thread] All the certificates are up to date sleeping for" + str(
                            remaining_days) + "days.")
                    self.lock.wait((remaining_days - 2) * 3600 * 24)
                else:
                    print("[SSL Refresh Thread] Looks like we need to refresh certificates that are about to expire")
                    for x in self.ssl_certificates:
                        print("Remaining days :", x, ":", (self.ssl_certificates[x] - now).days)
                    x = [x for x in self.ssl_certificates if (self.ssl_certificates[x] - now).days < 6]
                    acme_ssl_certificates = set(self.ssl.register_certificate_or_selfsign(x, ignore_existing=True))
                    for host in x:
                        if host not in acme_ssl_certificates:
                            del self.ssl_certificates[host]
                            self.self_signed_certificates.add(host)
                        else:
                            self.ssl_certificates[host] = self.ssl.expiry_time(domain=host)
                    self.reload(forced=True)

    def learn_yourself(self):
        """
            Looks in it's own filesystem to find out the container in which it is running.
            Recognizing which container this code is running helps us to
            know the networks accessible from this container and find all other accessible containers.
        """
        try:
            file = open("/proc/self/cgroup")
            self.id = [l for l in file.read().split("\n") if l.find("cpu") != -1][0].split("/")[-1]
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
        except Exception as e:
            print("[ERROR]Couldn't determine container ID of this container:", e.args,
                  "\n Is it running in docker environment?",
                  file=sys.stderr)
            print("Falling back to default network", file=sys.stderr)
            network = self.client.networks.get("frontend")
            self.networks[network.id] = "frontend"

    def _register_container(self, container):
        """
         Find the details about container and register it and return True.
         If it's not configured with desired settings or is not accessible, return False
         @:returns True if the container is added to virtual hosts, false otherwise.
        """
        found = False
        try:
            for host, location, container in Container.Container.host_generator(container,
                                                                                known_networks=self.networks.keys()):
                # it might return string if there's a error in processing
                if type(host) is not str:
                    if (host.hostname, host.port) in self.hosts:
                        existing_host: Host = self.hosts[(host.hostname, host.port)]
                        existing_host.add_container(location, container)
                        ## if any of the containers in for the virtualHost require https, the all others will be redirected to https.
                        if host.scheme == "https":
                            existing_host.scheme = "https"
                        host = existing_host
                    else:
                        host.add_container(location, container)
                        self.hosts[(host.hostname, host.port)] = host
                    if host.scheme == "https":
                        if host.hostname not in self.ssl_certificates:
                            host.ssl_expiry = self.ssl.expiry_time(host.hostname)
                        else:
                            host.ssl_expiry = self.ssl_certificates[host.host.hostname]
                        if (host.ssl_expiry - datetime.datetime.now()).days > 2:
                            self.ssl_certificates[host.hostname] = host.ssl_expiry

            found = True
            self.containers.add(container.id)

        except Container.NoHostConiguration:
            print("Skip Container:", "No VIRTUAL_HOST configuration", "Id:" + container.id,
                  "Name:" + container.attrs["Name"].replace("/", ""), sep="\t")
        except Container.UnreachableNetwork:
            print("Skip Container:", "UNREACHABLE Network           ", "Id:" + container.id,
                  "Name:" + container.attrs["Name"].replace("/", ""), sep="\t")
        return found

    # removes container from the maintained list.
    # this is called when a caontainer dies or leaves a known network
    def remove_container(self, container):
        if type(container) is Container:
            container = container.id

        if container in self.containers:
            removed = False
            deletions = []
            for host in self.hosts.values():
                if host.remove_container(container):
                    removed = True
                    if host.isEmpty():
                        if host.scheme == "https":
                            if host.hostname in self.self_signed_certificates:
                                self.self_signed_certificates.remove(host.hostname)
                            else:
                                del self.ssl_certificates[host.hostname]
                        deletions.append((host.hostname, host.port))
            if removed:
                for d in deletions:
                    del self.hosts[d]
                self.containers.remove(container)
                return self.reload()

    def reload(self, forced=False) -> bool:
        self.lock.acquire()
        """
        Creates a new configuration based on current state and signals nginx to reload.
        This is called whenever there's change in container or network state.
        :return:
        """
        host_list = [copy.deepcopy(host) for host in self.hosts.values()]

        next_reload = None
        now = datetime.datetime.now()
        ssl_requests = set()
        for host in host_list:
            host.locations = list(host.locations.values())
            host.upstreams = {}
            for i, location in enumerate(host.locations):
                location.container = list(location.containers)[0]
                if len(location.containers) > 1:
                    location.upstream = host.hostname + "-" + str(host.port) + "-" + str(i + 1)
                    host.upstreams[location.upstream] = location.containers
                else:
                    location.upstream = False
            host.upstreams = [{"id": x, "containers": y} for x, y in host.upstreams.items()]
            if host.scheme == "https":
                if int(host.port) in (80, 443):
                    host.ssl_redirect = True
                    host.port = 443
                host.ssl_host = True

                if host.hostname in self.ssl_certificates:
                    host.ssl_file = host.hostname
                elif host.hostname in self.self_signed_certificates:
                    host.ssl_file = host.hostname + ".selfsigned"
                else:
                    ssl_requests.add(host.hostname)

        if len(ssl_requests):
            registered = self.ssl.register_certificate_or_selfsign(list(ssl_requests))
            for host in host_list:
                if host.hostname in ssl_requests:
                    if host.hostname not in registered:
                        self.ssl.register_certificate_self_sign(host.hostname)
                        host.ssl_file = host.hostname + ".selfsigned"
                        self.self_signed_certificates.add(host.hostname)
                    else:
                        host.ssl_file = host.hostname
                        self.ssl_certificates[host.hostname] = self.ssl.expiry_time(host.hostname)
                        host.ssl_expiry = self.ssl_certificates[host.hostname]
                        if self.next_ssl_expiry:
                            if self.next_ssl_expiry > host.ssl_expiry:
                                self.next_ssl_expiry = host.ssl_expiry
                                self.lock.notify()
                        else:
                            self.lock.notify()
                            self.next_ssl_expiry = host.ssl_expiry

        output = self.template.render(virtual_servers=host_list)
        if forced:
            response = self.nginx.forced_update(output)
        else:
            response = self.nginx.update_config(output)
        self.lock.release()
        return response

    def disconnect(self, network, container, scope):
        if container == self.id:
            if network in self.networks:
                # it's weird that the disconnect log is sent twice. this this check is  necessary
                del self.networks[network]
                self.rescan_and_reload()
        elif container in self.containers and network in self.networks:
            if not self.update_container(container):
                self.remove_container(container.id)
                self.reload()

    def connect(self, network, container, scope):
        if container == self.id:
            if network not in self.networks:
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
        -- during  full container rescan
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
        self.containers = set()
        self.hosts = {}
        for container in containers:
            self._register_container(container)

    def rescan_and_reload(self):
        self.rescan_all_container()
        return self.reload()

    def cleanup(self):
        self.lock.acquire()
        self.shouldExit = True
        self.lock.notify()
        self.lock.release()
