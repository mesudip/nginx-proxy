from docker.models.containers import Container


def process_ssl_certificates(container: Container, environment: map, certificates: set, hostmap: map):
    for host, port in hostmap:
        if host.secured:
            if host not in certificates:
                pass
