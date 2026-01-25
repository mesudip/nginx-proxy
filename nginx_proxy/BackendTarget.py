from typing import Union, List

from docker.models.containers import Container as DockerContainer


class BackendTarget:
    def __init__(
        self,
        id: str,
        scheme: Union[str] = "http",
        address=None,
        port=None,
        path=None,
        name=None,
        env: dict = None,
        labels: dict = None,
        network_settings: dict = None,
        ports: dict = None,
        backend_type: str = "container",
    ):
        self.name = name
        self.id = id
        self.address: str = address
        self.port: int = port
        self.path: Union[str, None] = path
        self.scheme: str = scheme
        self.networks = set()  # the list networks through which this container is accessible.
        self.env = env if env else {}
        self.labels = labels if labels else {}
        self.network_settings = network_settings if network_settings else {}
        self.ports = ports if ports else {}
        self.type = backend_type

    @staticmethod
    def from_container(container: DockerContainer):
        env = BackendTarget.get_container_env_map(container)
        labels = container.attrs["Config"].get("Labels", {})
        container_name = container.attrs["Name"].replace("/", "")
        network_settings = container.attrs["NetworkSettings"]["Networks"]
        ports = container.attrs["NetworkSettings"]["Ports"]

        # Determine strict defaults, these should be refined during processing users of this object
        return BackendTarget(
            id=container.id,
            name=container_name,
            env=env,
            labels=labels,
            network_settings=network_settings,
            ports=ports,
            backend_type="container",
        )

    @staticmethod
    def from_service(service):
        # Service structure is different: service.attrs['Spec']['TaskTemplate']['ContainerSpec']
        spec = service.attrs.get("Spec", {})
        task_template = spec.get("TaskTemplate", {})
        container_spec = task_template.get("ContainerSpec", {})

        # Env is list "KEY=VAL"
        env_list = container_spec.get("Env", [])
        env = {x.split("=", 1)[0]: x.split("=", 1)[1] for x in env_list if "=" in x}

        labels = spec.get("Labels", {})
        # Merge with ContainerSpec labels? Usually Service labels are what we care about for traefik/nginx-proxy

        name = service.attrs.get("Spec", {}).get("Name")

        # Networks
        # task_template['Networks'] is list of dicts: [{'Target': 'network-id', ...}]
        # We need to resolve IPs?
        # Services in Swarm (VIP) have VirtualIPs in service.attrs['Endpoint']['VirtualIPs']
        # [{'NetworkID': '...', 'Addr': '10.0.0.x/y'}]

        endpoint = service.attrs.get("Endpoint", {})
        virtual_ips = endpoint.get("VirtualIPs", [])

        network_settings = {}
        for vip in virtual_ips:
            net_id = vip.get("NetworkID")
            addr = vip.get("Addr", "").split("/")[0]  # Strip CIDR
            if net_id:
                network_settings[net_id] = {"NetworkID": net_id, "IPAddress": addr}

        # Ports
        # endpoint['Ports'] -> [{'Protocol': 'tcp', 'TargetPort': 80, 'PublishedPort': 8080, ...}]
        # BackendTarget ports expects dict?
        # In Container, it was {'80/tcp': ...}.
        ports = {}
        for p in endpoint.get("Ports", []):
            proto = p.get("Protocol", "tcp")
            target = p.get("TargetPort")
            if target:
                ports[f"{target}/{proto}"] = p

        return BackendTarget(
            id=service.id,
            name=name,
            env=env,
            labels=labels,
            network_settings=network_settings,
            ports=ports,
            backend_type="service",
        )

    def add_network(self, network_id: str):
        self.networks.add(network_id)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other) -> bool:
        if type(other) is BackendTarget:
            return self.id == other.id
        if type(other) is str:
            return self.id == other
        return False

    def __repr__(self):
        return str({"scheme": self.scheme, "address": self.address, "port": self.port, "path": self.path})

    @staticmethod
    def get_container_env_map(container: DockerContainer):
        # first we get the list of tuples each containing data in form (key, value)
        container_env = container.attrs["Config"]["Env"]

        env_list = [x.split("=", 1) for x in container_env] if container_env else []
        # convert the environment list into map
        return {x[0]: x[1].strip() for x in env_list if len(x) == 2}


class UnconfiguredBackend(Exception):
    def __init__(self, backend_type="container"):
        self.backend_type = backend_type


class UnreachableNetwork(UnconfiguredBackend):
    def __init__(self, network_names: List[str], backend_type="container"):
        super().__init__(backend_type)
        self.network_names = network_names


class NoHostConiguration(UnconfiguredBackend):
    def __init__(self, backend_type="container"):
        super().__init__(backend_type)
