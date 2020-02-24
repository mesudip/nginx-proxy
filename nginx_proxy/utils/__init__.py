def split_url(entry_string: str):
    # Tried parsing urls with urllib.parse.urlparse but it doesn't work quiet
    # well when scheme( eg: "https://") is missing eg "example.com"
    # it says that example.com is path not the hostname.
    split_scheme = entry_string.strip().split("://", 1)
    scheme, host_part = split_scheme if len(split_scheme) is 2 else (None, split_scheme[0])
    host_entries = host_part.split("/", 1)
    hostport, location = (host_entries[0], "/" + host_entries[1]) if len(host_entries) is 2 else (
        host_entries[0], None)
    hostport_entries = hostport.split(":", 1)
    host, port = hostport_entries if len(hostport_entries) is 2 else (hostport_entries[0], None)

    return {
        "scheme": set([x for x in scheme.split("+") if x]) if scheme else [],
        "host": host if host else None,
        "port": port,
        "location": location
    }


def parse_url_with_default(entry_string: str, scheme='http', port='80', location='/'):
    data = split_url(entry_string)
    if data['scheme'] is None:
        data['scheme'] = scheme
    if data['port'] is None:
        data['port'] = port
    if data['location'] is None:
        data['location'] = location
