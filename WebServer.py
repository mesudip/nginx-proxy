import docker
import json
from docker import DockerClient
from Host import Host,Location
from jinja2 import Template
import sys
import json
import copy
from subprocess import call, run
import subprocess

class WebServer():
    def __init__(self, client: DockerClient, *args):
        self.client = client
        self.containers = set()
        self.services = set()
        self.networks = {}
        self.conf_file_name="/etc/nginx/conf.d/default.conf"

        self.host_file="/etc/hosts"
        self.hosts = {}
        file = open("default.conf.template")
        self.template = Template(file.read())
        file.close()
        self.learn_yourself()
        self.scan_live_containers()

    def learn_yourself(self):
        try:
            file=open("/proc/self/cgroup")
            self.id=[l for l in file.read().split("\n") if l.find("cpu") != -1][0].split("/")[-1]
            self.container=self.client.containers.get(self.id)
            self.networks=[a for a in self.container.attrs["NetworkSettings"]["Networks"]["frontend"].keys()]
            self.networks.append([ self.client.networks.get(a).id for a in self.networks])
            file.close()
        except Exception as e:
            network = self.client.networks.get("frontend")
            self.networks[network.id] = network
            self.networks[network.short_id] = network
            self.networks[network.name] = network
            print("Couldn't determine it's own id from the list of containers",file=sys.stderr)

    def reload_nginx(self):
        test_result=subprocess.run(["nginx", "-t"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if test_result.returncode is not 0:
            print("Nginx configtest failed!",file=sys.stderr)
            print(test_result.stderr.decode("utf-8"),file=sys.stderr)
        else:
            reload_result=subprocess.run(["nginx","-s","reload"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            if reload_result is not 0:
                print("This is chaos",file=sys.stderr)
                print(reload_result.stderr.decode("utf-8"),file=sys.stderr)


    def scan_live_containers(self):
        containers = self.client.containers.list()
        for container in containers:
            self._register_container(container)
        self.reload()

    def _register_container(self, container):
        # if it's a service container we can skip it.
        host=Host.from_container(container,known_networks={self.networks["frontend"].id,})

        if host:
            if "com.docker.swarm.service.name" in container.attrs["Config"]["Labels"]:
                id = container.attrs["Config"]["Labels"]["com.docker.swarm.service.name"]
                self.services.add(id)
            else:
                self.containers.add(host.id)
            if host.server_name in self.hosts:
                existing_host=self.hosts[host.server_name]
                for location, location_content in existing_host.locations.items():
                    if location not in host.locations:
                        host.locations[location]=location_content
            self.hosts[host.server_name]=host
            return True
        return False


    def register_container(self, event):
        if self._register_container(self.client.containers.get(event["id"])):
            self.reload()

    def remove_container(self, event):
        if event["id"] in self.containers:
            self.containers.remove(event["id"])
            for host in self.hosts.values():
                for location,location_content in host.locations.items():
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


    def reload(self):
        # print(json.dumps(json.loads(str(self.hosts).replace("'",'"').replace("None","null"  )),indent=4,sort_keys=True))
        host_list=[copy.copy(host) for host in self.hosts.values()]
        hosts=set()
        for host in host_list:
            host.locations=list(host.locations.values())
        output=self.template.render(virtual_servers=host_list)
        file=open(self.conf_file_name,"w")
        file.write(output)
        file.close()
        self.reload_nginx()
        # l=[ x for x in output.split("\n") if not (x.startswith("//") and len(x) <5 )]
        #
        print(output)

