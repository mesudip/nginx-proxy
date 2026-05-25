import hashlib
from typing import List, Dict, Any

from nginx_proxy.Host import Host


class UpstreamProcessor:
    def process(self, hosts: List[Host], prefer_local: bool = False) -> List[Dict[str, Any]]:
        global_upstreams = {}

        for host in hosts:
            for i, location in enumerate(host.locations.values()):
                if len(location.backends) > 1:
                    local_service_ids = self._local_service_ids(location.backends)
                    for backend in location.backends:
                        backend.backup = prefer_local and backend.type == "service" and backend.id in local_service_ids
                    if prefer_local and local_service_ids:
                        self._align_service_backup_ports(location.backends)

                    backend_key = tuple(
                        sorted([(str(b.address), str(b.port), bool(b.backup)) for b in location.backends])
                    )
                    if backend_key in global_upstreams:
                        location.upstream = global_upstreams[backend_key]["id"]
                    else:
                        upstream_id = (
                            host.hostname.strip()
                            + "_"
                            + hashlib.sha1(str(backend_key).encode("utf-8")).hexdigest()[:12]
                        )
                        sticky_value = None
                        if not any(b.backup for b in location.backends):
                            sticky_value = self._sticky_value(location.backends)

                        global_upstreams[backend_key] = {
                            "id": upstream_id,
                            "containers": location.backends,
                            "sticky": sticky_value,
                        }
                        location.upstream = upstream_id
                else:
                    for backend in location.backends:
                        backend.backup = False
                    location.upstream = False

        return list(global_upstreams.values())

    @staticmethod
    def _sticky_value(backends):
        for backend in backends:
            if "NGINX_STICKY_SESSION" not in backend.env:
                continue
            value = backend.env["NGINX_STICKY_SESSION"]
            env_value = value.lower().strip()
            if env_value == "true":
                return "ip_hash"
            if env_value == "false":
                return None
            return value
        return None

    @staticmethod
    def _local_service_ids(backends):
        return {
            b.labels.get("com.docker.swarm.service.id")
            for b in backends
            if b.type != "service" and b.labels.get("com.docker.swarm.service.id")
        }

    @staticmethod
    def _align_service_backup_ports(backends):
        for backend in backends:
            if backend.type != "service" or not backend.backup or int(backend.port or 80) != 80:
                continue
            local_ports = {
                int(b.port)
                for b in backends
                if b.type != "service"
                and b.port is not None
                and b.labels.get("com.docker.swarm.service.id") == backend.id
            }
            if len(local_ports) != 1:
                continue
            local_port = next(iter(local_ports))
            if local_port != 80:
                backend.port = local_port
