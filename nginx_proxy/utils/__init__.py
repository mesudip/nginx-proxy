from typing import Union, Dict


def split_url(entry_string: str, default_scheme=None, default_port=None, default_location=None) -> Dict[
    str, Union[str, int]]:
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

    return {
        "scheme": set([x for x in scheme.split("+") if x]) if scheme else default_scheme,
        "host": host if host else None,
        "port": port,
        "location": location
    }
