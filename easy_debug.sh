#!/bin/sh

if ! docker network inspect frontend >>/dev/null; then
  docker network create frontend >>/dev/null
fi
IMAGE_NAME="mesudip/nginx-proxy:local-debug"
docker build -f debug.Dockerfile -t "$IMAGE_NAME" --build-arg WORK_DIR="$(pwd)" . >>/dev/null
docker rm --force mesudip-nginx-local-debug >/dev/null
docker run -d \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /etc/ssl:/etc/ssl \
  -v "$(pwd):$(pwd)" \
  -v /etc/ssl/dhparam:/etc/nginx/dhparam \
  -e PYTHON_DEBUG_ENABLE=true -e PYTHON_DEBUG_PORT=5678 \
  -p 80:80 -p 443:443 \
  --entrypoint /bin/sh \
  --name mesudip-nginx-local-debug \
  "$IMAGE_NAME" -e "$(pwd)/docker-entrypoint.sh"
docker network connect frontend mesudip-nginx-local-debug
echo "Container started :) "
docker logs -f mesudip-nginx-local-debug
