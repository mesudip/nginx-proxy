import docker
import time

import docker.models
import docker.models.containers

def start_backend_container(docker_client:docker.DockerClient, test_network:str, virtual_host_env:dict[str, str] | list[str] | None)->docker.models.containers.Container:
    image_name = "mesudip/test-backend:test"
    
    # Ensure the backend image is built
    try:
        docker_client.images.build(path="tests/websocket/", tag=image_name, rm=True)
    except docker.errors.BuildError as e:
        print(f"Docker backend build failed: {e}")
        raise

    print(f"Starting backend container with VIRTUAL_HOST: {virtual_host_env}...")
    container = docker_client.containers.run(
        image_name,
        detach=True,
        environment=virtual_host_env,
        network=test_network.name,
        name=f"test-backend-{time.time_ns()}", # Unique name for each test run
        restart_policy={"Name": "no"}
    )
    # Give the backend container a moment to start
    time.sleep(2) 
    return container
