import docker
import json
import WebServer as containers


client = docker.from_env()
hosts=containers.WebServer(client)

def eventLoop():
    client.containers.list()
    for e in client.events():
        event=json.loads(e)
        eventType=event["Type"]

        if eventType =="service":
            process_service_event(event["Action"],event)
        elif eventType == "network":
            process_network_event(event[ "Action"],event)
        elif eventType=="container":
            process_container_event(event["Action"],event)

def process_service_event(action,event):
    if action == "create":
        print("service created")


def process_container_event(action,event):
    if action == "start":
        print ("container created",event["id"])
        hosts.register_container(event)
    elif action == "die":
        print ("container died", event["id"])
        hosts.remove_container(event)


def process_network_event(action,event):
    if action == "create":
        print( "network created")
eventLoop()

