Nginx-Proxy
===================================================
Docker container for automatically creating nginx configuration based on active containers in docker host.

- Easy server configuration with environment variables
- Map multiple containers to different locations on same server
- Automatic Let's Encrypt ssl certificate registration
- Basic Authorization

## Quick Setup  
### Setup nginx-proxy
```
docker pull mesudip/nginx-proxy
docker network create frontend;    # create a network for nginx proxy 
docker run  --network frontend \
            --name nginx-proxy \
            -v /var/run/docker.sock:/var/run/docker.sock:ro \
            -v /etc/ssl:/etc/ssl \
            -v /etc/nginx/dhparam:/etc/nginx/dhparam \
            -p 80:80 \
            -p 443:443 \
            -d --restart always mesudip/nginx-proxy
```
### Setup your container
The only thing that matters is that the container shares at least one common network to the nginx container and `VIRTUAL_HOST` 
environment variable is set. 

Examples:
- **WordPress**
```
docker run --network frontend \
          --name wordpress-server \
          -e VIRTUAL_HOST="wordpress.example.com" \
          wordpress
```
 - **Docker Registry**  
 ```
docker run --network frontend \
          --name docker-registry \
          -e VIRTUAL_HOST='https://registry.example.com/v2 -> /v2; client_max_body_size 2g' \
          -e PROXY_BASIC_AUTH="registry.example.com -> user1:password,user2:password2,user3:password3" \
          registry:2
```

Details of Using nginx-proxy
======================
 - [Configure `nginx-proxy`](#configure-nginx-proxy)
 - [Configure enrironment VIRTUAL_HOST in your containers](#configure-environment-virtual_host-in-your-containers)
    - [WebSockets](#support-for-websocket)
    - [Multiple hosts in same container](#multiple-virtual-hosts-on-same-container)
 - [Redirection](#redirection)
 - [Https and SSL](#ssl-support)
 - [Basic Authorization](#basic-authorization)
 - [Default Server](#default-server)

## Configure `nginx-proxy`
Following directries can be made into volumes to persist configurations
- `/etc/nginx/conf.d` nginx configuration directory. You can add your own server configurations here
- `/etc/nginx/dhparam` the directory for storing DH parameter for ssl connections
- `/etc/ssl` directory for storing ssl certificates, ssl private key and Let's Encrypt account key.
- `/var/log/nginx` directory nginx logs 
- `/tmp/acme-challenges` directory for storing challenge content when registering Let's Encrypt certificate

Some of the default behaviour of `nginx-proxy` can be changed with environment variables.
-   `DHPARAM_SIZE`  Default - `2048` : Set size of dhparam usd for ssl certificates
-   `CLIENT_MAX_BODY_SIZE` Default - `1m` : Set default max body size for all the servers.

## Configure environment `VIRTUAL_HOST` in your containers
When you want a container's to be hosted on a domain set `VIRTUAL_HOST` environment variable to desired `server_name` entry.
For virtual host to work it requires 
- nginx-proxy container to be on the same network as that of the container.
- port to be exposed in Docker file or while creating the container. When missing or if it has multiple exposed ports, port 80 is used by default.
- when hosting on port other than 80, make sure that your nginx-proxy container's port is bind-mounted to host port.

Some configurations possible through `VIRTUAL_HOST`

 `VIRTUAL_HOST` | release address | container path | container port
:--- | :--- |:---------------| :---
example.com |  http:<span></span>//example.com | /              | exposed port
example.com:8080 | http:<span></span>//example.com:8080 | /              | exposed port
example.com -> :8080 | http:<span></span>//example.com | /              | `8080`
https://<span></span>example.com  | https:<span></span>//example.com | /              | exposed port
example.com/<span></span>api | http://<span></span>example.com/api | /api           | exposed port
example.com/<span></span>api/ -> / | http://<span></span>example.com/api | /           | exposed port
example.com/<span></span>api -> :8080/api | http://<span></span>example.com/api | /api           | 8080
https://<span></span>example.com/<span></span>api/v1:5001  -> :8080/api | https://<span></span>example.com/<span></span>api/v1:5001 | /api           | 8080
wss://example.com/websocket | wss://example.com/websocket | /              | exposed port
 
With `VIRTUAL_HOST` you can inject nginx directives into location each configuration must be separed with a `;`
. You can see the possible directives in nginx documentation.

**Example :** `VIRTUAL_HOST=somesite.example.com -> :8080 ;proxy_read_timeout  900;client_max_body_size 2g;` will generate configuration as follows
```nginx.conf
server{
    server_name somesite.example.com;
    listen 80;
    location /{
        proxy_read_timeout 900;
        client_max_body_size 2g;
        proxy_pass http://127.2.3.4; // your container ip here
    }
}
```
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
## Redirection
 Let's say you want to serve a website on `example.uk`. You might want users visiting `www.example.uk`,`example.com`,`www.example.com`
 to redirect to  `example.uk`.  You can simply use `PROXY_FULL_REDIRECT` environment variable. 
 ```
  -e 'VIRTUAL_HOST=https://example.uk -> :7000' \
  -e 'PROXY_FULL_REDIRECT=example.com,www.example.com,www.example.uk->example.uk'
 ```

## SSL Support
Issuing of SSL certificate is done using acme-nginx library for Let's Encrypt. If a precheck determines that
the domain we are trying to issue certificate is not owned by current machine, a self-signed certificate is
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

**Note that `*.com` or `*` is not a valid wildcard.** Wild card must have at least 2 dots.

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
-  `docker exec nginx-proxy getssl www.example.com`

    Obtained certificate is saved on `/etc/ssl/certs/www.example.com` and private is saved on `/etc/ssl/private/www.example.com`

To issue certificates for multiple domain you can simply add more parameters to the above command
 
 - `docker exec nginx-proxy getssl www.example.com example.com ww.example.com`
 
    All the domains are registered on the same certificate and the filename is set from the first parameter
    passed to the command. so `/etc/ssl/certs/www.example.com`  and `/etc/ssl/private/www.example.com` are generated

Use  `docker exec nginx-proxy getssl --help`   for getting help with the command

## Basic Authorization
Basic Auth can be enabled on the container with environment variable `PROXY_BASIC_AUTH`.
- `PROXY_BASIC_AUTH=user1:password1,user2:password2,user3:password3` adds basic auth feature to your configured `VIRTUAL_HOST` server root.
- `PROXY_BASIC_AUTH=example.com/api/v1/admin -> admin1:password1,admin2:password2` adds basic auth only to the location starting from `api/v1/admin`

**Note:** Basic authorization will be ignored if the container's host doesn't use `https`

## Default Server
When request comes for a server name that is not registered in `nginx-proxy`, It responds with 503 by default.
If you want the requested to be passed to a container instead, when setting up the container you can add `PROXY_DEFAULT_SERVER=true` environment along with `VIRTUAL_HOST`.

This much is sufficient for http connections, but for https connections, you might want to setup
[wildcard certificates](#using-your-own-ssl-certificate) so that your users dont get invalid ssl certificate errors.
