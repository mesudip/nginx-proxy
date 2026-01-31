import hashlib
from typing import List, Dict, Any
from nginx_proxy.Host import Host


class StickySessionProcessor:
    def process(self, hosts: List[Host]) -> List[Dict[str, Any]]:
        global_upstreams = {}

        for host in hosts:
            for i, location in enumerate(host.locations.values()):
                if len(location.backends) > 1:
                    backend_key = tuple(
                        sorted([(str(b.address), str(b.port)) for b in location.backends])
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
                        for b in location.backends:
                            if "NGINX_STICKY_SESSION" in b.env:
                                val = b.env["NGINX_STICKY_SESSION"]
                                env_value=val.lower().strip()
                                if env_value == "true":
                                    sticky_value = "ip_hash"
                                elif env_value == "false":
                                    sticky_value = None
                                else:
                                    sticky_value = val
                                break

                        global_upstreams[backend_key] = {
                            "id": upstream_id,
                            "containers": location.backends,
                            "sticky": sticky_value,
                        }
                        location.upstream = upstream_id
                else:
                    location.upstream = False

        return list(global_upstreams.values())
