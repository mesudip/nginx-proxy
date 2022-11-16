#!/bin/sh
#When working on the project locally, you want to debug things running inside docker container
# like a normal python script. This helps achieve that by creating proper directory mapping in the container
# and then starting the container with pydevd enabled.
#

mkdir -p ./.run_data/conf.d
DUMMY_NGINX=y SSL_DIR=./.run_data NGINX_CONF_DIR=./.run_data python3 main.py