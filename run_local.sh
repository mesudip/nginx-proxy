#!/bin/sh
# When working on the project locally, You don't want to run the container inside docker or interact with nginx.
# To achieve that, we create only nginx configs and don't interact with nginx. The configs will be inside `./.run_data` folder
#

#  sudo mount -t nfs -o vers=4 172.31.0.2:/srv/nginx/acme_challenges /home/sudip/Documents/mesudip/nginx-proxy/.run_data/acme_challenges
#

mkdir -p ./.run_data/conf.d
#
DUMMY_NGINX=y CHALLENGE_DIR=./.run_data/acme_challenges SSL_DIR=./.run_data NGINX_CONF_DIR=./.run_data python3 main.py