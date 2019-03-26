#!/usr/bin/python3
from nginx_proxy import SSL
import sys
ssl = SSL.SSL("/etc/ssl", "/etc/nginx/conf.d")
if __name__=="__main__":
    ssl.register_certificate(sys.argv[1:])
