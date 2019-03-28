# Nginx-proxy
Docker container for automatically creating nginx configuration based on active services in docker machine including swarm services.

 ## :bangbang: Work In Progress
 This is a work in progress and not yet ready for production

## setting up nginx-proxy
```
docker network create nginx-proxy;
docker run  --network nginx-proxy \
            --name nginx-proxy \
            -v /var/run/docker.sock:/var/run/docker.sock:ro \
            -v /etc/ssl:/etc/ssl \
            -p 80:80 \
            -p 443:443 \
            mesudip/nginx-proxy
```
## configure container
```
docker run --network nginx-proxy
          --name nginx
          -e VIRTUAL_HOST="https://example.com"
          nginx:alpine
```
## Possible values for `VIRTUAL_HOST`
 - **-e VIRTUAL_HOST=example.com** *:* host on example.com port 80
 - **-e VIRTUAL_HOST="example.com -> :8080"** *:* port 8080 of the container must be used. port 80 is used by default.
 - **-e VIRTUAL_HOST=example.com:8080** *:* host on example.com port 8080
 - **-e VIRTUAL_HOST=example.com:443** *:* host on `https://example.com`.
 - **-e VIRTUAL_HOST=example.com:443/api** *:* mount the container on /api location.
 - **-e VIRTUAL_HOST="example.com:443/api -> :8080/api"** *:* mount the container on /api. connect to container's port 8080 on location /api
 - **-e VIRTUAL_HOST="https://example.com:8080"** *:* use SSL but on different port
 
 
