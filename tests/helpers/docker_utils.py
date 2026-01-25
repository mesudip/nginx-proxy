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

    if backend_type == "service":
        time.sleep(5)
        return docker_client.services.create(
            image=image_name,
            env=env_list,
            networks=[test_network.name],
            name=f"test-service-{uuid.uuid4().hex}",
            labels={"com.docker.stack.namespace": "test"},  # optional common label
        )
    else:
        time.sleep(2)
        return docker_client.containers.run(
            image_name,
            detach=True,
            environment=virtual_host_env,  # run accepts dict or list
            network=test_network.name,
            name=f"test-backend-{uuid.uuid4().hex}",
            restart_policy={"Name": "no"},
        )
    # Give the backend container/service a moment to start


def stop_backend(
    backend: docker.models.containers.Container | docker.models.services.Service,
):
    if backend is None:
        return
    if isinstance(backend, docker.models.containers.Container):
        backend.remove(force=True)
    else:
        # Services don't support force remove, and remove() is enough
        backend.remove()
