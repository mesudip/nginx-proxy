from typing import List, Optional, Dict
from .ConfigParser import ConfigParser
from .Config import Block, Direction

class NginxConfig:
    def __init__(self):
        self.parser = ConfigParser()
        self.root: Optional[Block] = None

    def load(self, config_str: str):
        self.parser.load(config_str)
        self.root = self.parser.data  # Top-level block

    def dump(self) -> str:
        return self.parser.gen_config()

    @property
    def user(self) -> Optional[str]:
        return self._get_directive_value("user")

    @user.setter
    def user(self, value: str):
        self.root.set_directive("user", value)

    @property
    def worker_processes(self) -> Optional[str]:
        return self._get_directive_value("worker_processes")

    @worker_processes.setter
    def worker_processes(self, value: str):
        self.root.set_directive("worker_processes", value)

    @property
    def pid(self) -> Optional[str]:
        return self._get_directive_value("pid")

    @pid.setter
    def pid(self, value: str):
        self.root.set_directive("pid", value)

    @property
    def error_log(self) -> Optional[str]:
        return self._get_directive_value("error_log")

    @error_log.setter
    def error_log(self, value: str):
        self.root.set_directive("error_log", value)

    @property
    def events(self) -> Optional['EventsBlock']:
        blocks = self.root.get_blocks("events")
        return EventsBlock(blocks[0]) if blocks else None

    @property
    def http(self) -> Optional['HttpBlock']:
        blocks = self.root.get_blocks("http")
        return HttpBlock(blocks[0]) if blocks else None

    def _get_directive_value(self, name: str) -> Optional[str]:
        if not self.root:
            return None
        dirs = self.root.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None

class EventsBlock:
    def __init__(self, block: 'Block'):
        self.block = block

    @property
    def worker_connections(self) -> Optional[str]:
        return self._get_directive_value("worker_connections")

    @worker_connections.setter
    def worker_connections(self, value: str):
        self.block.set_directive("worker_connections", value)

    @property
    def use(self) -> Optional[str]:
        return self._get_directive_value("use")

    @use.setter
    def use(self, value: str):
        self.block.set_directive("use", value)

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None

class HttpBlock:
    def __init__(self, block: 'Block'):
        self.block = block
        self.servers: List['ServerBlock'] = [ServerBlock(b) for b in self.block.get_blocks("server")]

    @staticmethod
    def parse(http_block_str: str) -> 'HttpBlock':
        parser = ConfigParser()
        parser.load(http_block_str)
        return HttpBlock(parser.data)


    @property
    def return_code(self) -> Optional[str]:
        return self._get_directive_value("return")
    
    @property
    def upstreams(self) ->  list['Block']:
        return self.block.get_blocks('upstream')
    
    @property
    def include(self) -> Optional[str]:
        return self._get_directive_value("include")

    @include.setter
    def include(self, value: str):
        self.block.set_directive("include", value)

    @property
    def default_type(self) -> Optional[str]:
        return self._get_directive_value("default_type")


    @default_type.setter
    def default_type(self, value: str):
        self.block.set_directive("default_type", value)

    @property
    def log_formats(self) -> Dict[str, str]:
        formats = {}
        for d in self.block.get_directives("log_format"):
            parts = " ".join(d.values).split(maxsplit=1)
            if len(parts) == 2:
                formats[parts[0]] = parts[1]
        return formats

    @property
    def client_header_timeout(self) -> Optional[str]:
        return self._get_directive_value("client_header_timeout")

    @property
    def send_lowat(self) -> Optional[str]:
        return self._get_directive_value("send_lowat")

    @property
    def client_body_timeout(self) -> Optional[str]:
        return self._get_directive_value("client_body_timeout")

    @property
    def send_timeout(self) -> Optional[str]:
        return self._get_directive_value("send_timeout")

    @property
    def client_header_buffer_size(self) -> Optional[str]:
        return self._get_directive_value("client_header_buffer_size")

    @property
    def large_client_header_buffers(self) -> Optional[str]:
        return self._get_directive_value("large_client_header_buffers")

    @property
    def gzip(self) -> Optional[str]:
        return self._get_directive_value("gzip")

    @property
    def gzip_min_length(self) -> Optional[str]:
        return self._get_directive_value("gzip_min_length")

    @property
    def gzip_buffers(self) -> Optional[str]:
        return self._get_directive_value("gzip_buffers")

    @property
    def gzip_types(self) -> Optional[str]:
        return self._get_directive_value("gzip_types")

    @property
    def output_buffers(self) -> Optional[str]:
        return self._get_directive_value("output_buffers")

    @property
    def postpone_output(self) -> Optional[str]:
        return self._get_directive_value("postpone_output")

    @property
    def sendfile(self) -> Optional[str]:
        return self._get_directive_value("sendfile")

    @property
    def tcp_nopush(self) -> Optional[str]:
        return self._get_directive_value("tcp_nopush")

    @property
    def tcp_nodelay(self) -> Optional[str]:
        return self._get_directive_value("tcp_nodelay")

    @property
    def keepalive_timeout(self) -> Optional[str]:
        return self._get_directive_value("keepalive_timeout")

    @property
    def maps(self) -> List['MapBlock']:
        blocks = self.block.get_blocks("map")
        return [MapBlock(b) for b in blocks]

    @property
    def server_names_hash_bucket_size(self) -> Optional[str]:
        return self._get_directive_value("server_names_hash_bucket_size")

    @property
    def proxy_cache(self) -> Optional[str]:
        return self._get_directive_value("proxy_cache")

    @property
    def proxy_request_buffering(self) -> Optional[str]:
        return self._get_directive_value("proxy_request_buffering")

    @property
    def ssl_ciphers(self) -> Optional[str]:
        return self._get_directive_value("ssl_ciphers")

    @property
    def ssl_protocols(self) -> Optional[str]:
        return self._get_directive_value("ssl_protocols")

    @property
    def ssl_prefer_server_ciphers(self) -> Optional[str]:
        return self._get_directive_value("ssl_prefer_server_ciphers")

    @property
    def ssl_session_timeout(self) -> Optional[str]:
        return self._get_directive_value("ssl_session_timeout")

    @property
    def ssl_session_cache(self) -> Optional[str]:
        return self._get_directive_value("ssl_session_cache")

    @property
    def ssl_session_tickets(self) -> Optional[str]:
        return self._get_directive_value("ssl_session_tickets")

    @property
    def ssl_stapling(self) -> Optional[str]:
        return self._get_directive_value("ssl_stapling")

    @property
    def ssl_stapling_verify(self) -> Optional[str]:
        return self._get_directive_value("ssl_stapling_verify")

    @property
    def add_headers(self) -> List[str]:
        headers = []
        for d in self.block.get_directives("add_header"):
            headers.append(" ".join(d.values))
        return headers

    @property
    def access_log(self) -> Optional[str]:
        return self._get_directive_value("access_log")

    @property
    def client_max_body_size(self) -> Optional[str]:
        return self._get_directive_value("client_max_body_size")

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None


class ServerBlock:
    def __init__(self, block: 'Block'):
        self.block = block
        self.locations: List['LocationBlock'] = [LocationBlock(b) for b in self.block.get_blocks("location")]

    def __repr__(self) -> str:
        server_names = ", ".join(self.server_names) if self.server_names else "N/A"
        listen_port = self.listen if self.listen else "N/A"
        return f"<ServerBlock(server_names='{server_names}', listen='{listen_port}', locations={len(self.locations)})>"

    @property
    def listen(self) -> Optional[str]:
        return self._get_directive_value("listen")

    @listen.setter
    def listen(self, value: str):
        self.block.set_directive("listen", value)

    @property
    def return_code(self) -> Optional[str]:
        return self._get_directive_value("return")

    @property
    def server_names(self) -> List[str]:
        v = self._get_directive_value("server_name")
        return v.split() if v else []

    @server_names.setter
    def server_names(self, values: List[str]):
        self.block.set_directive("server_name", " ".join(values))

    @property
    def access_log(self) -> Optional[str]:
        return self._get_directive_value("access_log")

    @access_log.setter
    def access_log(self, value: str):
        self.block.set_directive("access_log", value)

    @property
    def error_page(self) -> Optional[str]:
        return self._get_directive_value("error_page")

    @error_page.setter
    def error_page(self, value: str):
        self.block.set_directive("error_page", value)

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None

class LocationBlock:
    def __init__(self, block: 'Block'):
        self.block = block
        self.path: str = block.parameters
        self.ifs: List['IfBlock'] = [IfBlock(b) for b in self.block.get_blocks("if")]

    def __repr__(self) -> str:
        proxy_pass_info = f", proxy_pass='{self.proxy_pass}'" if self.proxy_pass else ""
        directive_count = len([c for c in self.block.contents if c.is_direction()])
        return f"<LocationBlock(path='{self.path}', ifs={len(self.ifs)}, directive_count={directive_count}{proxy_pass_info})>"

    @property
    def proxy_pass(self) -> Optional[str]:
        return self._get_directive_value("proxy_pass")

    @proxy_pass.setter
    def proxy_pass(self, value: str):
        self.block.set_directive("proxy_pass", value)

    @property
    def proxy_redirect(self) -> Optional[str]:
        return self._get_directive_value("proxy_redirect")

    @proxy_redirect.setter
    def proxy_redirect(self, value: str):
        self.block.set_directive("proxy_redirect", value)


    @property
    def return_code(self) -> Optional[str]:
        return self._get_directive_value("return")
    
    @property
    def client_max_body_size(self) -> Optional[str]:
        return self._get_directive_value("client_max_body_size")



    @client_max_body_size.setter
    def client_max_body_size(self, value: str):
        self.block.set_directive("client_max_body_size", value)

    @property
    def client_body_buffer_size(self) -> Optional[str]:
        return self._get_directive_value("client_body_buffer_size")

    @client_body_buffer_size.setter
    def client_body_buffer_size(self, value: str):
        self.block.set_directive("client_body_buffer_size", value)

    @property
    def client_body_temp_path(self) -> Optional[str]:
        return self._get_directive_value("client_body_temp_path")

    @client_body_temp_path.setter
    def client_body_temp_path(self, value: str):
        self.block.set_directive("client_body_temp_path", value)

    @property
    def proxy_connect_timeout(self) -> Optional[str]:
        return self._get_directive_value("proxy_connect_timeout")

    @proxy_connect_timeout.setter
    def proxy_connect_timeout(self, value: str):
        self.block.set_directive("proxy_connect_timeout", value)

    @property
    def proxy_send_timeout(self) -> Optional[str]:
        return self._get_directive_value("proxy_send_timeout")

    @proxy_send_timeout.setter
    def proxy_send_timeout(self, value: str):
        self.block.set_directive("proxy_send_timeout", value)

    @property
    def proxy_read_timeout(self) -> Optional[str]:
        return self._get_directive_value("proxy_read_timeout")

    @proxy_read_timeout.setter
    def proxy_read_timeout(self, value: str):
        self.block.set_directive("proxy_read_timeout", value)

    @property
    def proxy_send_lowat(self) -> Optional[str]:
        return self._get_directive_value("proxy_send_lowat")

    @proxy_send_lowat.setter
    def proxy_send_lowat(self, value: str):
        self.block.set_directive("proxy_send_lowat", value)

    @property
    def proxy_buffer_size(self) -> Optional[str]:
        return self._get_directive_value("proxy_buffer_size")

    @property
    def proxy_buffers(self) -> Optional[str]:
        return self._get_directive_value("proxy_buffers")

    @property
    def proxy_busy_buffers_size(self) -> Optional[str]:
        return self._get_directive_value("proxy_busy_buffers_size")

    @property
    def proxy_temp_file_write_size(self) -> Optional[str]:
        return self._get_directive_value("proxy_temp_file_write_size")

    @property
    def proxy_temp_path(self) -> Optional[str]:
        return self._get_directive_value("proxy_temp_path")

    @property
    def charset(self) -> Optional[str]:
        return self._get_directive_value("charset")

    @property
    def access_log(self) -> Optional[str]:
        return self._get_directive_value("access_log")

    @property
    def expires(self) -> Optional[str]:
        return self._get_directive_value("expires")

    @property
    def proxy_set_headers(self) -> List[tuple[str, str]]:
        headers = []
        for d in self.block.get_directives("proxy_set_header"):
            parts = " ".join(d.values).split(maxsplit=1)
            if len(parts) == 2:
                headers.append((parts[0], parts[1]))
        return headers

    @property
    def rewrite(self) -> Optional[str]:
        return self._get_directive_value("rewrite")

    @property
    def valid_referers(self) -> List[str]:
        v = self._get_directive_value("valid_referers")
        return v.split() if v else []

    @property
    def root(self) -> Optional[str]:
        return self._get_directive_value("root")

    @property
    def alias(self) -> Optional[str]:
        return self._get_directive_value("alias")
    @property
    def try_files(self) -> Optional[str]:
        return self._get_directive_value("try_files")

    @property
    def internal(self) -> Optional[str]:
        return self._get_directive_value("internal")

    # Add setters similarly where needed

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None

class MapBlock:
    def __init__(self, block: 'Block'):
        self.block = block
        self.parameters: str = block.parameters # e.g., "$http_upgrade $connection_upgrade"
        self.directives: Dict[str, str] = {}
        for item in self.block.contents:
            if item.is_direction():
                self.directives[item.name] = " ".join(item.values).strip()

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None

class IfBlock:
    def __init__(self, block: 'Block'):
        self.block = block
        self.condition: str = block.parameters

    @property
    def return_code(self) -> Optional[str]:
        return self._get_directive_value("return")

    @return_code.setter
    def return_code(self, value: str):
        self.block.set_directive("return", value)

    def _get_directive_value(self, name: str) -> Optional[str]:
        dirs = self.block.get_directives(name)
        return " ".join(filter(None, dirs[0].values)).strip() if dirs else None
