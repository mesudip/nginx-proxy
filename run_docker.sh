#!/bin/sh
#When working on the project locally, you want to debug things running inside docker container
# like a normal python script. This helps achieve that by creating proper directory mapping in the container
# and then starting the container with pydevd enabled.
#
if ! docker network inspect frontend >>/dev/null; then
  docker network create frontend >>/dev/null
fi
IMAGE_NAME="mesudip/nginx-proxy:local"
echo "Started Docker build. This will take a while if you have changed requirements.txt"
docker build -t "$IMAGE_NAME" --build-arg WORK_DIR="$(pwd)" . >>/dev/null
docker rm --force mesudip-nginx-local-debug >/dev/null
docker volume create local_ssl || echo "volume already exists"
docker run -d \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v local_ssl:/etc/ssl \
  -v "$(pwd):$(pwd)" \
  -v /etc/ssl/dhparam:/etc/nginx/dhparam \
  -v /tmp/mesdip-nginx-conf:/etc/nginx/conf.d \
  -p 81:80 -p 444:443 \
  --entrypoint /bin/sh \
  --name mesudip-nginx-local-debug \
  "$IMAGE_NAME" -c "cd $(pwd) && ./docker-entrypoint.sh"
docker network connect frontend mesudip-nginx-local-debug
echo "Container started :) "
docker logs -f mesudip-nginx-local-debug
