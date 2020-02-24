#!/usr/bin/env sh
mkdir -p /etc/nginx/dhparam
if ! openssl dhparam -in /etc/nginx/dhparam/dhparam.pem >/dev/null 2>&1; then
  echo "Generating new DH Parameters for SSL as it's missing"
  openssl dhparam -out /etc/nginx/dhparam/dhparam.pem ${DHPARAM_SIZE:-2048}
fi
python3 -u main.py
