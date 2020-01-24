import os
import re
import subprocess
import sys

import docker

from nginx_proxy import WebServer as containers

debug_config = {}
if "PYTHON_DEBUG_PORT" in os.environ:
    if os.environ["PYTHON_DEBUG_PORT"].strip():
        debug_config["port"] = int(os.environ["PYTHON_DEBUG_PORT"].strip())
if "PYTHON_DEBUG_HOST" in os.environ:
    debug_config["host"] = os.environ["PYTHON_DEBUG_HOST"]
if "PYTHON_DEBUG_ENABLE" in os.environ:
    if os.environ["PYTHON_DEBUG_ENABLE"].strip() == "true":
        if "host" not in debug_config:
            debug_config["host"] = re.findall("([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)+",
                                              subprocess.run(["ip", "route"],
                                                             stdout=subprocess.PIPE).stdout.decode().split(
                                            "\n")[0])[0]

if len(debug_config):
    import pydevd

    pydevd.settrace(stdoutToServer=True, stderrToServer=True, **debug_config)

# fix for https://trello.com/c/dMG5lcTZ
try:
    client = docker.from_env()
    client.version()
except Exception as e:
    print("There was error connecting with the docker server \nHave you correctly mounted /var/run/docker.sock?\n"+str(e.args),file=sys.stderr)
    sys.exit(1)
hosts = containers.WebServer(client)


def eventLoop():
    for event in client.events(decode=True):
        try:
            eventType = event["Type"]
            if eventType == "service":
                process_service_event(event["Action"], event)
            elif eventType == "network":
                process_network_event(event["Action"], event)
            elif eventType == "container":
                process_container_event(event["Action"], event)
        except Exception as e:
            print("Unexpected error :" + e.__class__.__name__ + str(e))

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


try:
    eventLoop()
except (KeyboardInterrupt, SystemExit):
    hosts.cleanup();
