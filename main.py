import docker
import json
from nginx_proxy import WebServer as containers
import os
import re
import subprocess
import pydevd

config = {}
if "PYTHON_DEBUG_PORT" in os.environ:
    if os.environ["PYTHON_DEBUG_PORT"].strip():
        config["port"] = int(os.environ["PYTHON_DEBUG_PORT"].strip())
if "PYTHON_DEBUG_HOST" in os.environ:
    config["host"] = os.environ["PYTHON_DEBUG_HOST"]
if "PYTHON_DEBUG_ENABLE" in os.environ:
    if os.environ["PYTHON_DEBUG_ENABLE"].strip() == "true":
        if "host" not in config:
            config["host"] = re.findall("([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)+",
                                        subprocess.run(["ip", "route"], stdout=subprocess.PIPE).stdout.decode().split(
                                            "\n")[0])[0]

if len(config):
    pydevd.settrace(stdoutToServer=True, stderrToServer=True, **config)

client = docker.from_env()

hosts = containers.WebServer(client)


def eventLoop():
    client.containers.list()
    for e in client.events():
        try:
            e = e.decode()
        except AttributeError:
            pass
        event = json.loads(e)
        eventType = event["Type"]

        if eventType == "service":
            process_service_event(event["Action"], event)
        elif eventType == "network":
            process_network_event(event["Action"], event)
        elif eventType == "container":
            process_container_event(event["Action"], event)


def process_service_event(action, event):
    if action == "create":
        print("service created")


def process_container_event(action, event):
    if action == "start":
        # print("container started", event["id"])
        hosts.update_container(event["id"])
    elif action == "die":
        # print("container died", event["id"])
        hosts.remove_container(event["id"])


def process_network_event(action, event):
    if action == "create":
        # print("network created")
        pass
    elif "container" in event["Actor"]["Attributes"]:
        if action == "disconnect":
            # print("network disconnect")
            hosts.disconnect(network=event["Actor"]["ID"], container=event["Actor"]["Attributes"]["container"],
                             scope=event["scope"])
        elif action == "connect":
            # print("network connect")
            hosts.connect(network=event["Actor"]["ID"], container=event["Actor"]["Attributes"]["container"],
                          scope=event["scope"])
    elif action == "destroy":
        # print("network destryed")
        pass


eventLoop()
