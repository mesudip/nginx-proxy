import pytest

from nginx.NginxConf import NginxConfig
@pytest.fixture
def loaded_config():
    CONFIG = """
user  www www;

worker_processes  2;

pid /var/run/nginx.pid;

#                          [ debug | info | notice | warn | error | crit ]

error_log  /var/log/nginx.error_log  info;

events {
    worker_connections   2000;

    # use [ kqueue | epoll | /dev/poll | select | poll ];
    use kqueue;
}

http {

    include       conf/mime.types;
    default_type  application/octet-stream;


    log_format main      '$remote_addr - $remote_user [$time_local] '
                         '"$request" $status $bytes_sent '
                         '"$http_referer" "$http_user_agent" '
                         '"$gzip_ratio"';

    log_format download  '$remote_addr - $remote_user [$time_local] '
                         '"$request" $status $bytes_sent '
                         '"$http_referer" "$http_user_agent" '
                         '"$http_range" "$sent_http_content_range"';

    client_header_timeout  3m;
    client_body_timeout    3m;
    send_timeout           3m;

    client_header_buffer_size    1k;
    large_client_header_buffers  4 4k;

    gzip on;
    gzip_min_length  1100;
    gzip_buffers     4 8k;
    gzip_types       text/plain;

    output_buffers   1 32k;
    postpone_output  1460;

    sendfile         on;
    tcp_nopush       on;
    tcp_nodelay      on;
    send_lowat       12000;

    keepalive_timeout  75 20;

    #lingering_time     30;
    #lingering_timeout  10;
    #reset_timedout_connection  on;


    server {
        listen        one.example.com;
        server_name   one.example.com  www.one.example.com;

        access_log   /var/log/nginx.access_log  main;

        location / {
            proxy_pass         http://127.0.0.1/;
            proxy_redirect     off;

            proxy_set_header   Host             $host;
            proxy_set_header   X-Real-IP        $remote_addr;
            #proxy_set_header  X-Forwarded-For  $proxy_add_x_forwarded_for;

            client_max_body_size       10m;
            client_body_buffer_size    128k;

            client_body_temp_path      /var/nginx/client_body_temp;

            proxy_connect_timeout      70;
            proxy_send_timeout         90;
            proxy_read_timeout         90;
            proxy_send_lowat           12000;

            proxy_buffer_size          4k;
            proxy_buffers              4 32k;
            proxy_busy_buffers_size    64k;
            proxy_temp_file_write_size 64k;

            proxy_temp_path            /var/nginx/proxy_temp;

            charset  koi8-r;
        }

        error_page  404  /404.html;

        location = /404.html {
            root  /spool/www;
        }

        location /old_stuff/ {
            rewrite   ^/old_stuff/(.*)$  /new_stuff/$1  permanent;
        }

        location /download/ {

            valid_referers  none  blocked  server_names  *.example.com;

            if ($invalid_referer) {
                #rewrite   ^/   http://www.example.com/;
                return   403;
            }

            #rewrite_log  on;

            # rewrite /download/    */mp3/*.any_ext to /download/*/mp3/*.mp3
            rewrite ^/(download/.*)/mp3/(.*)\..*$
                    /$1/mp3/$2.mp3                   break;

            root         /spool/www;
            #autoindex    on;
            access_log   /var/log/nginx-download.access_log  download;
        }

        location ~* \.(jpg|jpeg|gif)$ {
            root         /spool/www;
            access_log   off;
            expires      30d;
        }
    }
}
"""
    config = NginxConfig()
    config.load(CONFIG.strip())
    return config

def test_top_level_directives(loaded_config):
    config = loaded_config
    assert config.user == "www www"
    assert config.worker_processes == "2"
    assert config.pid == "/var/run/nginx.pid"
    assert config.error_log == "/var/log/nginx.error_log info"

def test_events_block(loaded_config):
    events = loaded_config.events
    assert events is not None
    assert events.worker_connections == "2000"
    assert events.use == "kqueue"

def test_http_block(loaded_config):
    http = loaded_config.http
    assert http is not None
    assert http.include == "conf/mime.types"
    assert http.default_type == "application/octet-stream"

    # 
    assert http.log_formats["main"] == "'$remote_addr - $remote_user [$time_local] ' '\"$request\" $status $bytes_sent ' '\"$http_referer\" \"$http_user_agent\" ' '\"$gzip_ratio\"'"
    assert http.log_formats["download"] == "'$remote_addr - $remote_user [$time_local] ' '\"$request\" $status $bytes_sent ' '\"$http_referer\" \"$http_user_agent\" ' '\"$http_range\" \"$sent_http_content_range\"'"
    assert http.client_header_timeout == "3m"
    assert http.client_body_timeout == "3m"
    assert http.send_timeout == "3m"
    assert http.client_header_buffer_size == "1k"
    assert http.large_client_header_buffers == "4 4k"
    assert http.gzip == "on"
    assert http.gzip_min_length == "1100"
    assert http.gzip_buffers == "4 8k"
    assert http.gzip_types == "text/plain"
    assert http.output_buffers == "1 32k"
    assert http.postpone_output == "1460"
    assert http.sendfile == "on"
    assert http.tcp_nopush == "on"
    assert http.tcp_nodelay == "on"
    assert http.send_lowat == "12000"
    assert http.keepalive_timeout == "75 20"

def test_server_block(loaded_config):
    http = loaded_config.http
    assert len(http.servers) == 1
    server = http.servers[0]
    assert server.listen == "one.example.com"
    assert server.server_names == ["one.example.com", "www.one.example.com"]
    assert server.access_log == "/var/log/nginx.access_log main"
    assert server.error_page == "404 /404.html"

def test_location_blocks(loaded_config):
    server = loaded_config.http.servers[0]
    assert len(server.locations) == 5

    # Location /
    loc0 = server.locations[0]
    assert loc0.path == "/"
    assert loc0.proxy_pass == "http://127.0.0.1/"
    assert loc0.proxy_redirect == "off"
    assert loc0.proxy_set_headers == [("Host", "$host"), ("X-Real-IP", "$remote_addr")]
    assert loc0.client_max_body_size == "10m"
    assert loc0.client_body_buffer_size == "128k"
    assert loc0.client_body_temp_path == "/var/nginx/client_body_temp"
    assert loc0.proxy_connect_timeout == "70"
    assert loc0.proxy_send_timeout == "90"
    assert loc0.proxy_read_timeout == "90"
    assert loc0.proxy_send_lowat == "12000"
    assert loc0.proxy_buffer_size == "4k"
    assert loc0.proxy_buffers == "4 32k"
    assert loc0.proxy_busy_buffers_size == "64k"
    assert loc0.proxy_temp_file_write_size == "64k"
    assert loc0.proxy_temp_path == "/var/nginx/proxy_temp"
    assert loc0.charset == "koi8-r"

    # Location = /404.html
    loc1 = server.locations[1]
    assert loc1.path == "= /404.html"
    assert loc1.root == "/spool/www"

    # Location /old_stuff/
    loc2 = server.locations[2]
    assert loc2.path == "/old_stuff/"
    assert loc2.rewrite == "^/old_stuff/(.*)$ /new_stuff/$1 permanent"

    # Location /download/
    loc3 = server.locations[3]
    assert loc3.path == "/download/"
    assert loc3.valid_referers == ["none", "blocked", "server_names", "*.example.com"]
    assert loc3.rewrite == "^/(download/.*)/mp3/(.*)\..*$ /$1/mp3/$2.mp3 break"
    assert loc3.root == "/spool/www"
    assert loc3.access_log == "/var/log/nginx-download.access_log download"

    # Nested if in /download/
    assert len(loc3.ifs) == 1
    if_block = loc3.ifs[0]
    assert if_block.condition == "($invalid_referer)"
    assert if_block.return_code == "403"

    # Location ~* \.(jpg|jpeg|gif)$
    loc4 = server.locations[4]
    assert loc4.path == "~* \.(jpg|jpeg|gif)$"
    assert loc4.root == "/spool/www"
    assert loc4.access_log == "off"
    assert loc4.expires == "30d"

def test_http_block_parse():
    from nginx.NginxConf import HttpBlock
    http_block_str = """
        proxy_headers on;
        server {
            listen 80;
            server_name example.com;
        }
    """
    http_block = HttpBlock.parse(http_block_str)
    assert http_block is not None
    assert len(http_block.servers) == 1
    assert http_block.servers[0].listen == "80"
    assert http_block.servers[0].server_names == ["example.com"]

def test_http_block_parse_complex():
    from nginx.NginxConf import HttpBlock
    http_block_str = """
server {

        http2 on;
        ssl_certificate /etc/ssl/certs/*.sireto.dev.crt;
        ssl_certificate_key /etc/ssl/private/*.sireto.dev.key;
        
        listen 80 default_server;
        server_name _ ;
        location /.well-known/acme-challenge/ {
            alias ./.run_data/acme-challenges/;
            try_files $uri =404;
        }
        error_page 503 /503_default.html;

        location = /503_default.html {
            root ./vhosts_template/errors;
            internal;
        }

        location / {
            return 503;
        }
}
    """
    http_block = HttpBlock.parse(http_block_str)
    assert http_block is not None

    # Assert map block
  


    # Assert server block
    assert len(http_block.servers) == 1
    server = http_block.servers[0]
    assert server.listen == "80 default_server"
    assert server.server_names == ["_"]
    assert server.error_page == "503 /503_default.html"

    # Assert location blocks within the server block
    assert len(server.locations) == 3

    loc0 = server.locations[0]
    assert loc0.path == "/.well-known/acme-challenge/"
    assert loc0.alias == "./.run_data/acme-challenges/"
    assert loc0.try_files == "$uri =404"

    loc1 = server.locations[1]
    assert loc1.path == "= /503_default.html"
    assert loc1.root == "./vhosts_template/errors"
    assert loc1.internal == ""

    loc2 = server.locations[2]
    assert loc2.path == "/"
    assert loc2.return_code == "503"
