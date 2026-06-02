import re
import docker
import time
import uuid
import requests

import docker.models
import docker.models.containers


def start_nginx_proxy_container(
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
    docker_host_ip: str,
    swarm_mode: str,
    container_name: str,
    backend_start_grace_seconds: str = "2",
):
    image_name = "mesudip/nginx-proxy:test"

    print(f"\nBuilding {image_name}...")
    try:
        docker_client.images.build(path=".", tag=image_name, rm=True)
        print(f"Successfully built {image_name}")
    except docker.errors.BuildError as e:
        print(f"Docker build failed: {e}")
        raise

    print(f"Starting {image_name} container...")
    container = docker_client.containers.run(
        image_name,
        detach=True,
        ports={"80/tcp": None, "443/tcp": None},
        volumes={
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "ro"},
            "nginx-test-dhparam": {"bind": "/etc/nginx/dhparam", "mode": "rw"},
            "nginx-test-ssl": {"bind": "/etc/ssl", "mode": "rw"},
        },
        network=test_network.name,
        name=container_name,
        environment={
            "LETSENCRYPT_API": "https://acme-staging-v02.api.letsencrypt.org/directory",
            "VHOSTS_TEMPLATE_DIR": "/app/vhosts_template",
            "CHALLENGE_DIR": "/etc/nginx/acme-challenges",
            "DOCKER_SWARM": swarm_mode,
            "BACKEND_START_GRACE_SECONDS": backend_start_grace_seconds,
        },
        restart_policy={"Name": "no"},
    )

    time.sleep(1)
    container.reload()
    port_80 = container.ports["80/tcp"][0]["HostPort"]
    port_443 = container.ports["443/tcp"][0]["HostPort"]

    print(f"nginx-proxy running on host ports: HTTP={port_80}, HTTPS={port_443}")

    ready = False
    for i in range(120):
        try:
            response = requests.get(
                f"http://{docker_host_ip}:{port_80}",
                headers={"Host": "nonexistent.example.com"},
                timeout=1,
            )
            if response.status_code == 503:
                print(f"nginx-proxy is ready after {i+1} seconds.")
                ready = True
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)

    if not ready:
        print("\nnginx-proxy did not become ready in time. Container logs:")
        print(container.logs().decode("utf-8"))
        raise RuntimeError("nginx-proxy did not become ready in time.")

    return container, port_80, port_443


def start_backend(
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
    virtual_host_env: dict[str, str] | list[str] | None,
    backend_type: str = "container",
    sleep=True,
    pytest_request=None,
    healthcheck=None,
) -> docker.models.containers.Container | docker.models.services.Service:
    image_name = "mesudip/test-backend:test"

    # Ensure the backend image is built
    try:
        docker_client.images.build(path="tests/websocket/", tag=image_name, rm=True)
    except docker.errors.BuildError as e:
        print(f"Docker backend build failed: {e}")
        raise

    print(f"Starting backend {backend_type} with VIRTUAL_HOST: {virtual_host_env}...")

    env_list = virtual_host_env
    if isinstance(virtual_host_env, dict):
        env_list = [f"{k}={v}" for k, v in virtual_host_env.items()]

    def slug63(name: str) -> str:
        s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
        s = s[-63:].lstrip("-")
        return s

    svc_name = (
        f"test-service-{uuid.uuid4().hex}"
        if pytest_request is None
        else slug63(f"{pytest_request.node.name}-{uuid.uuid4().hex[:8]}")
    )

    if backend_type == "service":
        service_kwargs = {}
        if healthcheck is not None:
            service_kwargs["healthcheck"] = healthcheck
        backend = docker_client.services.create(
            image=image_name,
            env=env_list,
            networks=[test_network.name],
            name=svc_name,
            labels={"com.nginx-proxy.test.container": "tetruet"},  # optional common label
            **service_kwargs,
        )
        if sleep:
            time.sleep(5)
    else:
        container_kwargs = {}
        if healthcheck is not None:
            container_kwargs["healthcheck"] = healthcheck
        backend = docker_client.containers.run(
            image_name,
            detach=True,
            environment=virtual_host_env,  # run accepts dict or list
            network=test_network.name,
            name=f"test-backend-{uuid.uuid4().hex}",
            restart_policy={"Name": "no"},
            **container_kwargs,
        )
        if sleep:
            time.sleep(1)
    return backend


def stop_backend(
    backend: docker.models.containers.Container | docker.models.services.Service,
):
    if backend is None:
        return
    if isinstance(backend, docker.models.containers.Container):
        backend.remove(force=True)
    else:
        backend.remove()
        # additionally try to find container and force remove it
        try:
            containers = backend.client.containers.list(
                all=True, filters={"label": f"com.docker.swarm.service.name={backend.name}"}
            )
            for container in containers:
                container.remove(force=True)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
