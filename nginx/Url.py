class Url:
    root: 'Url' = None

    def __init__(self, scheme: set, hostname: str, port: int, location: str):
        self.scheme = scheme
        self.hostname = hostname
        self.port = port
        self.location = location

    def __repr__(self):
        return "%s://%s:%s%s" % \
               ('+'.join(list(self.scheme)) if len(self.scheme) else '?',
                self.hostname if self.hostname else '?',
                str(self.port) if self.port is not None else '?',
                self.location if self.location else '?')

    @staticmethod
    def parse(entry_string: str, default_scheme=None, default_port=None, default_location=None) -> 'Url':
        # Tried parsing urls with urllib.parse.urlparse but it doesn't work quiet
        # well when scheme( eg: "https://") is missing eg "example.com"
        # it says that example.com is path not the hostname.
        if default_scheme is None:
            default_scheme = []
        split_scheme = entry_string.strip().split("://", 1)
        scheme, host_part = split_scheme if len(split_scheme) is 2 else (default_scheme, split_scheme[0])
        host_entries = host_part.split("/", 1)
        hostport, location = (host_entries[0], "/" + host_entries[1]) if len(host_entries) is 2 else (
            host_entries[0], default_location)
        hostport_entries = hostport.split(":", 1)
        host, port = hostport_entries if len(hostport_entries) is 2 else (hostport_entries[0], default_port)
        return Url(scheme, host if host else None, port, location)


Url.root = Url(set(), None, None, '/')
