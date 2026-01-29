import re
import docker
import time
import uuid

import docker.models
import docker.models.containers


def start_backend(
    docker_client: docker.DockerClient,
    test_network: docker.models.networks.Network,
    virtual_host_env: dict[str, str] | list[str] | None,
    backend_type: str = "container",
    sleep=True,
    pytest_request=None,
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
        s = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-')
        s = s[-63:].lstrip('-')
        return s
    svc_name=f"test-service-{uuid.uuid4().hex}" if pytest_request is None else slug63(f"{pytest_request.node.name}-{uuid.uuid4().hex[:8]}")
    
    if backend_type == "service":
        backend= docker_client.services.create(
            image=image_name,
            env=env_list,
            networks=[test_network.name],
            name=svc_name,
            labels={"com.nginx-proxy.test.container": "tetruet"},  # optional common label
        )
        if sleep:
            time.sleep(5)
    else:
        backend=docker_client.containers.run(
            image_name,
            detach=True,
            environment=virtual_host_env,  # run accepts dict or list
            network=test_network.name,
            name=f"test-backend-{uuid.uuid4().hex}",
            restart_policy={"Name": "no"},
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
            containers = backend.client.containers.list(all=True, filters={"label": f"com.docker.swarm.service.name={backend.name}"})
            for container in containers:
                container.remove(force=True)
        except Exception:
            pass