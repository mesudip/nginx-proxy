# Nginx-proxy
Docker container for automatically creating nginx configuration based on active containers in docker host.

## Setting up nginx-proxy
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
## Configure container
The only thing that matters is that the container shares at least one common network to the nginx container and `VIRTUAL_HOST` 
environment variable is set. If you have multiple exposed ports in the container, don't forget to 
mention the container port too. 
```
docker run --network frontend \
          --name test-host \
          -e VIRTUAL_HOST="example.com" \
          nginx:alpine
```

## Using the environment `VIRTUAL_HOST`
When you want a container's to be hosted on a domain set `VIRTUAL_HOST` environment variable to desired `server_name` entry.
For virtual host to work it requires 
- nginx-proxy container to be on the same network as that of the container.
- port to be exposed in Docker file or while creating the container. When missing or if it has multiple exposed ports, port 80 is used by default.
- when hosting on port other than 80, make sure that your nginx-proxy container's port is mapped to the host.

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
## Multiple Virtual Hosts on same container
To have multiple virtual hosts  out of single container, you can use `VIRTUAL_HOST1`, `VIRTUAL_HOST2`, `VIRTUAL_HOST3` and so on. In fact the only thing it matters is that the environment variable starts with `VIRTUAL_HOST`.

**Example:** setting up a Ethereum geth node.
```bash
    docker run -d  \
    -e "VIRTUAL_HOST1=https://ethereum.example.com -> :8545" \
    -e "VIRTUAL_HOST2=wss://ethereum.example.com/ws -> :8546" \
    --rpc --rpcaddr "0.0.0.0"  --ws --wsaddr 0.0.0.0 \
    ethereum/client-go 

```

 
## SSL Support
Issuing of SSL certificate is done using acme-nginx library for Let's Encrypt. If a precheck determines that
the domain we are trying to issue certificate is not owned by current machine, a self signed certificate is
generated instead.
##### Using ssl for exposing endpoint
 Certificate is automatically requested by the nginx-proxy container.
 It requests for a challenge and verifies the challenge to obtain the certificate.
 It is saved under directory `/etc/ssl/certs` and the private key is located inside
 directoy `/etc/ssl/private`
 
##### Manually obtaining certificate.
 You can manually obtain Let's encrypt certificate using the nginx-proxy container when `VIRTUL_HOST` begins with `https.//`
 or has port `443`. 
 Note that you must set ip in  DNS entry to point the correct server.
 
 To issue a certificate for a domain you can simply use this command.
-  `docker exec nginx getssl www.example.com`

    Obtained certificate is saved on `/etc/ssl/certs/www.example.com` and private is saved on `/etc/ssl/private/www.example.com`

To issue certificates for multiple domain you can simply add more parameters to the above command
 
 - `docker exec nginx getssl www.example.com example.com ww.example.com`
 
    All the domains are registered on the same certificate and the filename is set from the first parameter
    passed to the command. so `/etc/ssl/certs/www.example.com`  and `/etc/ssl/private/www.example.com` are generated
    
#### Manual Configuration for Nginx 
If you want to manually add servers to the nginx configuration, You can simply create a fine ending with `.conf` 
in the folder `/etc/nginx/conf.d/` of the container. This will get lost when deleting the container,
so in order to preserve it, you can create a volume or mount it to a host directory when creating nginx-proxy container.

#### Compatibility with jwilder/nginx-proxy
This nginx-proxy supports `VIRTUAL_HOST` `LETSENCRYPT_HOST` AND `VIRTUAL_PORT` like in jwilder/nginx-proxy.
But comma separated `VIRTUAL_HOST` is not supported. It's still missing a lot of other feature of jwilder/nginx-proxy 
hopefully they will be available in future versions.
