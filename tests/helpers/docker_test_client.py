from typing import List
import uuid
import ipaddress
import queue
import time
import json
import threading
import docker

class DockerTestClient:
    def __init__(self):
        self._containers = {}  # id -> MockContainer
        self._networks = {}    # id -> MockNetwork
        self._images = {}      # tag -> MockImage
        self.containers = MockContainerCollection(self)
        self.networks = MockNetworkCollection(self)
        self.images = MockImageCollection(self)
        self._lock = threading.RLock()
        self._event_queue = queue.Queue()
        # Default bridge network
        with self._lock:
            bridge_id = self._generate_id()
            bridge = MockNetwork(bridge_id, 'bridge', self)
            bridge.attrs = {'IPAM': {'Config': [{'Subnet': '172.17.0.0/16', 'Gateway': '172.17.0.1'}]}}
            bridge._next_ip = ipaddress.ip_address('172.17.0.2')
            bridge._connected = {}
            self._networks[bridge_id] = bridge
            self._next_subnet = 18  # For incremental subnets like 172.18.0.0/16
    def close(self):
        """Signals the event stream to stop."""
        self._emit_event(None)

    def _generate_id(self):
        return uuid.uuid4().hex + uuid.uuid4().hex

    def _get_next_subnet(self):
        subnet = f'172.{self._next_subnet}.0.0/16'
        self._next_subnet += 1
        return subnet

    def _emit_event(self, event):
        self._event_queue.put(event)

    def events(self, decode=False, filters=None):
        def match(event):
            if event is None:
                return True
            if not filters:
                return True
            for key, values in filters.items():
                if key == "type":
                    if event.get("Type") not in values:
                        return False
                elif key == "event":
                    action = event.get("Action") or event.get("status")
                    if action not in values:
                        return False
            # Additional filter types can be added if needed
            return True

        while True:
            event = self._event_queue.get()
            if event is None:
                # Sentinel value to stop the iterator
                self._event_queue.put(None) # Put it back for other listeners if any
                break 

            if match(event):
                if decode:
                    yield event
                else:
                    yield json.dumps(event).encode()

class MockContainerCollection:
    def __init__(self, client:'DockerTestClient'):
        self.client: 'DockerTestClient' = client

    def list(self, **kwargs):
        with self.client._lock:
            return list(self.client._containers.values())

    def get(self, container_id):
        with self.client._lock:
            if container_id in self.client._containers:
                return self.client._containers[container_id]
            raise ValueError("No such container")

    def create(self, image, command=None, **kwargs):
        with self.client._lock:
            cid = self.client._generate_id()
            name = kwargs.get('name')
            if not name:
                name = f'container-{len(self.client._containers) + 1}'
            environment = kwargs.get('environment', {})
            cont = MockContainer(cid, name, image, self.client)
            cont.status = 'created'
            cont.attrs = {
                'Name': f'/{name}', # Docker container names are prefixed with /
                'Config': {
                    'Env': [f'{k}={v}' for k, v in environment.items()],
                    'Image': image,
                },
                'NetworkSettings': {
                    'Ports': {}, # Add Ports here
                    'Networks': {}
                }
            }
            if command:
                cont.attrs['Config']['Cmd'] = command if isinstance(command, list) else command.split()
            self.client._containers[cid] = cont
            # Emit create event
            self.client._emit_event({
                "status": "create",
                "id": cid,
                "from": image,
                "Type": "container",
                "Action": "create",
                "Actor": {
                    "ID": cid,
                    "Attributes": {
                        "name": name,
                        "image": image
                    }
                },
                "scope": "local",
                "time": int(time.time()),
                "timeNano": time.time_ns()
            })
            # Connect to specified or default network
            network = kwargs.get('network')
            if network:
                net = self.client.networks.get(network)
            else:
                net = self.client.networks.get('bridge')
            net.connect(cont, **kwargs)  # Pass aliases, ipv4_address, etc.
            return cont

    def run(self, image, command=None, **kwargs)-> 'MockContainer':
        cont = self.create(image, command, **kwargs)
        cont.start()
        return cont

class MockImageCollection:
    def __init__(self, client:'DockerTestClient'):
        self.client: 'DockerTestClient' = client

    def build(self, path, tag, **kwargs):
        """Mock image build - just registers the tag without actually building"""
        with self.client._lock:
            image_id = self.client._generate_id()
            image = MockImage(image_id, tag, self.client)
            self.client._images[tag] = image
            # Optionally emit build event
            self.client._emit_event({
                "status": "build",
                "id": image_id,
                "Type": "image",
                "Action": "build",
                "Actor": {
                    "ID": image_id,
                    "Attributes": {
                        "name": tag
                    }
                },
                "scope": "local",
                "time": int(time.time()),
                "timeNano": time.time_ns()
            })
            return image, []  # Return image and empty logs list

    def get(self, image_tag):
        with self.client._lock:
            if image_tag in self.client._images:
                return self.client._images[image_tag]
            raise ValueError("No such image")

    def list(self, **kwargs):
        with self.client._lock:
            return list(self.client._images.values())

class MockImage:
    def __init__(self, id, tag, client):
        self.id = id
        self.tags = [tag] if tag else []
        self.client: 'DockerTestClient' = client
        self.attrs = {
            'Id': id,
            'RepoTags': self.tags
        }

    def tag(self, repository, tag=None, **kwargs):
        """Add a tag to this image"""
        full_tag = f"{repository}:{tag}" if tag else repository
        if full_tag not in self.tags:
            self.tags.append(full_tag)
            self.attrs['RepoTags'] = self.tags
        return True

    def remove(self, **kwargs):
        """Remove this image"""
        with self.client._lock:
            for tag in self.tags:
                if tag in self.client._images:
                    del self.client._images[tag]

class MockContainer:
    def __init__(self, id, name, image, client):
        self.id = id
        self.name = name
        self.image = image
        self.status = 'created'
        self.client: 'DockerTestClient' = client
        self.attrs = {'Name': f'/{name}'} # Docker container names are prefixed with /

    def start(self, **kwargs):
        with self.client._lock:
            if self.status in ('created', 'stopped'):
                self.status = 'running'
                # Assign dynamic IPs if not set
                for net_name, settings in self.attrs['NetworkSettings']['Networks'].items():
                    if not settings['IPAddress']:
                        net = self.client.networks.get(net_name)
                        ip = str(net._next_ip)
                        settings['IPAddress'] = ip
                        net._next_ip += 1
                        if self.id in net._connected:
                            net._connected[self.id]['ip'] = ip
                # Emit start event
                self.client._emit_event({
                    "status": "start",
                    "id": self.id,
                    "from": self.image,
                    "Type": "container",
                    "Action": "start",
                    "Actor": {
                        "ID": self.id,
                        "Attributes": {
                            "name": self.name,
                            "image": self.image
                        }
                    },
                    "scope": "local",
                    "time": int(time.time()),
                    "timeNano": time.time_ns()
                })

    def stop(self, **kwargs):
        with self.client._lock:
            if self.status == 'running':
                self.status = 'stopped'
                # Emit stop event (and die for similarity to real behavior)
                self.client._emit_event({
                    "status": "die",
                    "id": self.id,
                    "from": self.image,
                    "Type": "container",
                    "Action": "die",
                    "Actor": {
                        "ID": self.id,
                        "Attributes": {
                            "name": self.name,
                            "image": self.image,
                            "exitCode": "0"  # Mocked
                        }
                    },
                    "scope": "local",
                    "time": int(time.time()),
                    "timeNano": time.time_ns()
                })
                self.client._emit_event({
                    "status": "stop",
                    "id": self.id,
                    "from": self.image,
                    "Type": "container",
                    "Action": "stop",
                    "Actor": {
                        "ID": self.id,
                        "Attributes": {
                            "name": self.name,
                            "image": self.image
                        }
                    },
                    "scope": "local",
                    "time": int(time.time()),
                    "timeNano": time.time_ns()
                })

    def remove(self, **kwargs):
        force = kwargs.get('force', False)
        with self.client._lock:
                if self.id not in self.client._containers:
                    raise ValueError("No such container")
                if self.status == 'running' and not force:
                    raise ValueError(f"You cannot remove a running container {self.name}. Stop the container before attempting removal or force=True")
                if self.status == 'running' and force:
                    # Emit die event for force remove
                    self.client._emit_event({
                        "status": "die",
                        "id": self.id,
                        "from": self.image,
                        "Type": "container",
                        "Action": "die",
                        "Actor": {
                            "ID": self.id,
                            "Attributes": {
                                "name": self.name,
                                "image": self.image,
                                "exitCode": "137"  # Mocked for force kill
                            }
                        },
                        "scope": "local",
                        "time": int(time.time()),
                        "timeNano": time.time_ns()
                    })
                    self.status = 'stopped'
                    self.client._emit_event({
                        "status": "stop",
                        "id": self.id,
                        "from": self.image,
                        "Type": "container",
                        "Action": "stop",
                        "Actor": {
                            "ID": self.id,
                            "Attributes": {
                                "name": self.name,
                                "image": self.image
                            }
                        },
                        "scope": "local",
                        "time": int(time.time()),
                        "timeNano": time.time_ns()
                    })
                # Disconnect from all networks
                for net_name in list(self.attrs['NetworkSettings']['Networks']):
                    net = self.client.networks.get(net_name)
                    net.disconnect(self)
                del self.client._containers[self.id]
                # Emit destroy event
                self.client._emit_event({
                    "status": "destroy",
                    "id": self.id,
                    "from": self.image,
                    "Type": "container",
                    "Action": "destroy",
                    "Actor": {
                        "ID": self.id,
                        "Attributes": {
                            "name": self.name,
                            "image": self.image
                        }
                    },
                    "scope": "local",
                    "time": int(time.time()),
                    "timeNano": time.time_ns()
                })
    def reload (self,**kwargs):
        pass
    def logs(self,**kwargs):
        return b""
class MockNetworkCollection:
    def __init__(self, client):
        self.client = client

    def list(self, **kwargs)-> List['MockNetwork']:
        with self.client._lock:
            return list(self.client._networks.values())

    def get(self, network_id, **kwargs) -> 'MockNetwork':
        with self.client._lock:
            if network_id in self.client._networks:
                return self.client._networks[network_id]
            for net in self.client._networks.values():
                if net.name == network_id:
                    return net
            # network not found
            raise docker.errors.NotFound("No such network: "+network_id)
    def create(self, name, **kwargs)-> 'MockNetwork':
        with self.client._lock:
            nid = self.client._generate_id()
            net = MockNetwork(nid, name, self.client)
            subnet = self.client._get_next_subnet()
            gateway = subnet.replace('.0.0/16', '.0.1')
            net.attrs = {'IPAM': {'Config': [{'Subnet': subnet, 'Gateway': gateway}]}}
            net._next_ip = ipaddress.ip_address(gateway) + 1
            net._connected = {}
            self.client._networks[nid] = net
            # Emit create event
            self.client._emit_event({
                "Type": "network",
                "Action": "create",
                "Actor": {
                    "ID": nid,
                    "Attributes": {
                        "name": name,
                        "type": "bridge"  # Mocked as bridge
                    }
                },
                "scope": "local",
                "time": int(time.time()),
                "timeNano": time.time_ns()
            })
            return net

class MockNetwork:
    def __init__(self, id, name, client):
        self.id = id
        self.name = name
        self.client:'DockerTestClient' = client
        self.attrs = {}
        self._next_ip = None
        self._connected = {}
    @property
    def containers(self):
        with self.client._lock:
            conts = []
            for cont_id in self._connected.keys():
                cont = self.client.containers.get(cont_id)
                if cont:
                    conts.append(cont)
            return conts
    def reload(self,*args, **kwargs):
        time.sleep(0.01)
        pass # all data is already in sync
    def remove(self):
        with self.client._lock:
            if self.name == 'bridge':
                raise ValueError("Cannot remove default bridge")
            if self._connected:
                raise ValueError("Network has connected containers")
            if self.id in self.client._networks:
                del self.client._networks[self.id]
                # Emit destroy event
                self.client._emit_event({
                    "Type": "network",
                    "Action": "destroy",
                    "Actor": {
                        "ID": self.id,
                        "Attributes": {
                            "name": self.name,
                            "type": "bridge"
                        }
                    },
                    "scope": "local",
                    "time": int(time.time()),
                    "timeNano": time.time_ns()
                })

    def connect(self, container, **kwargs):
        with self.client._lock:
            if isinstance(container, str):
                container = self.client.containers.get(container)
            cont_id = container.id
            aliases = kwargs.get('aliases', [])
            ipv4_address = kwargs.get('ipv4_address')
            ip = ipv4_address if ipv4_address else ''
            if container.status == 'running' and not ip:
                ip = str(self._next_ip)
                self._next_ip += 1
            if self.name in container.attrs['NetworkSettings']['Networks']:
                return
            container.attrs['NetworkSettings']['Networks'][self.name] = {
                'IPAddress': ip,
                'Aliases': aliases,
                'NetworkID': self.id
            }
            self._connected[cont_id] = {'ip': ip, 'aliases': aliases}
            self.client._emit_event({
                "Type": "network",
                "Action": "connect",
                "Actor": {
                    "ID": self.id,
                    "Attributes": {
                        "container": cont_id,
                        "name": self.name,
                        "type": "bridge"
                    }
                },
                "scope": "local",
                "time": int(time.time()),
                "timeNano": time.time_ns()
            })

    def disconnect(self, container, **kwargs):
        with self.client._lock:
            if isinstance(container, str):
                container = self.client.containers.get(container)
            cont_id = container.id
            if self.name in container.attrs['NetworkSettings']['Networks']:
                del container.attrs['NetworkSettings']['Networks'][self.name]
            if cont_id in self._connected:
                del self._connected[cont_id]
            # Emit disconnect event
            self.client._emit_event({
                "Type": "network",
                "Action": "disconnect",
                "Actor": {
                    "ID": self.id,
                    "Attributes": {
                        "container": cont_id,
                        "name": self.name,
                        "type": "bridge"
                    }
                },
                "scope": "local",
                "time": int(time.time()),
                "timeNano": time.time_ns()
            })
