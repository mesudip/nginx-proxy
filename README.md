Nginx-Proxy
===================================================
Docker container for automatically creating nginx configuration based on active containers in docker host.

## Basic setup of nginx-proxy
```
docker network create frontend;    # create a network for nginx proxy 
docker run  --network frontend \
            --name nginx \
            -v /var/run/docker.sock:/var/run/docker.sock:ro \
            -v /etc/ssl:/etc/ssl \
            -p 80:80 \
            -p 443:443 \
            mesudip/nginx-proxy
```
### Volumes
Following directries can be made into volumes to persist configurations
- `/etc/nginx/conf.d` nginx configuration directory. You can add your own server configurations here
-  `/etc/nginx/dhparam` the directory for storing DH parameter for ssl connections
- `/etc/ssl` directory for storing ssl certificates, ssl private key and letsencrypt account key.

## Configuring the container in detail
The only thing that matters is that the container shares at least one common network to the nginx container and `VIRTUAL_HOST` 
environment variable is set. If you have multiple exposed ports in the container, don't forget to 
mention the container port too. 
```
docker run --network frontend \
          --name test-host \
          -e VIRTUAL_HOST="example.com" \
          nginx:alpine
```

### Using the environment `VIRTUAL_HOST`
When you want a container's to be hosted on a domain set `VIRTUAL_HOST` environment variable to desired `server_name` entry.
For virtual host to work it requires 
- nginx-proxy container to be on the same network as that of the container.
- port to be exposed in Docker file or while creating the container. When missing or if it has multiple exposed ports, port 80 is used by default.
- when hosting on port other than 80, make sure that your nginx-proxy container's port is bind-mounted to host port.

Some configurations possible through `VIRTUAL_HOST`

 `VIRTUAL_HOST` | release address |    container path | container port
:--- | :--- | :--- | :---
example.com |  http:<span></span>//example.com | / | exposed port
example.com:8080 | http:<span></span>//example.com:8080 | / | exposed port
example.com -> :8080 | http:<span></span>//example.com | / | `8080`
https://<span></span>example.com  | https:<span></span>//example.com | / | exposed port
example.com/<span></span>api | http://<span></span>example.com/api |/ | exposed port
example.com/<span></span>api -> :8080/api | http://<span></span>example.com/api | /api | 8080
https://<span></span>example.com/<span></span>api/v1:5001  -> :8080/api | https://<span></span>example.com/<span></span>api/v1:5001 | /api | 8080
wss://example.com/websocket | wss://example.com/websocket | / | exposed port

### Support for websocket
Exposing websocket requires the websocket endpoint to be explicitly configured via virtual host. The websocket endpoint can be `ws://` or `wss://`.
If you want to use both websocket and non-websocket endpoints you will have to use multiple hosts

`-e "VIRTUAL_HOST=wss://ws.example.com -> :8080/websocket"`

### Multiple Virtual Hosts on same container
To have multiple virtual hosts out of single container, you can use `VIRTUAL_HOST1`, `VIRTUAL_HOST2`, `VIRTUAL_HOST3` and so on. In fact the only thing it matters is that the environment variable starts with `VIRTUAL_HOST`.

**Example:** setting up a go-ethereum node.
```bash
    docker run -d  --network frontend \
    -e "VIRTUAL_HOST1=https://ethereum.example.com -> :8545" \
    -e "VIRTUAL_HOST2=wss://ethereum.example.com/ws -> :8546" \
    ethereum/client-go \
    --rpc --rpcaddr "0.0.0.0"  --ws --wsaddr 0.0.0.0
```

## SSL Support
Issuing of SSL certificate is done using acme-nginx library for Let's Encrypt. If a precheck determines that
the domain we are trying to issue certificate is not owned by current machine, a self signed certificate is
generated instead.

### Using SSL for exposing endpoint
Certificate is automatically requested by the nginx-proxy container.
It requests for a challenge and verifies the challenge to obtain the certificate.
It is saved under directory `/etc/ssl/certs` and the private key is located inside
directoy `/etc/ssl/private`
 
### Using your Own SSL certificate
If you already have a ssl certificate that you want to use, copy it under the `/etc/ssl/certs` directory and it's key under the directory `/etc/ssl/private` file should be named `domain.crt` and `domain.key`. 
 
Wildcard certificates can be used. For example to use `*.example.com` wildcard, you should create files  
`/etc/ssl/certs/*.example.com.crt` and `/etc/ssl/private/*.example.com.key` in the container's filesystem.

**Note that `*.blah` or `*` is not a valid wildcard.**

`/etc/ssl/certs/*.example.com.crt` certificate will :
- be used for `host1.example.com`
- be used for `www.example.com`
- not be used for `xyz.host1.example.com`
- not be used for `example.com`


 ***DHPARAM_SIZE :***
 Default size of DH key used for https connection is `2048`bits. The key size can be changed by changing `DHPARAM_SIZE` environment variable
 
### Manually obtaining certificate.
You can manually obtain Let's encrypt certificate using the nginx-proxy container.
Note that you must set ip in  DNS entry to point the correct server.
 
To issue a certificate for a domain you can simply use this command.
-  `docker exec nginx getssl www.example.com`

    Obtained certificate is saved on `/etc/ssl/certs/www.example.com` and private is saved on `/etc/ssl/private/www.example.com`

To issue certificates for multiple domain you can simply add more parameters to the above command
 
 - `docker exec nginx getssl www.example.com example.com ww.example.com`
 
    All the domains are registered on the same certificate and the filename is set from the first parameter
    passed to the command. so `/etc/ssl/certs/www.example.com`  and `/etc/ssl/private/www.example.com` are generated

Use  `docker exec nginx getssl --help`   for getting help with the command

## Compatibility with jwilder/nginx-proxy
This nginx-proxy supports `VIRTUAL_HOST` `LETSENCRYPT_HOST` AND `VIRTUAL_PORT` like in jwilder/nginx-proxy.
But comma separated `VIRTUAL_HOST` is not supported. It's still missing a lot of other feature of jwilder/nginx-proxy 
hopefully they will be available in future versions.
