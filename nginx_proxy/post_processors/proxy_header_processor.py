import re
from typing import Iterable, List, Sequence, Tuple

from nginx_proxy.Host import Host


class ProxyHeaderProcessor:
    _HEADER_PATTERN = re.compile(r"^\s*proxy_set_header\s+([^\s;]+)", re.IGNORECASE)
    _DEFAULT_HEADERS: Sequence[Tuple[str, str]] = (
        ("Host", "$http_host"),
        ("X-Real-IP", "$remote_addr"),
        ("X-Forwarded-For", "$proxy_add_x_forwarded_for"),
        ("X-Forwarded-Proto", "$proxy_x_forwarded_proto"),
        ("X-Forwarded-Ssl", "$proxy_x_forwarded_ssl"),
        ("X-Forwarded-Port", "$proxy_x_forwarded_port"),
        ("Proxy", '""'),
    )
    _WEBSOCKET_HEADERS: Sequence[Tuple[str, str]] = (
        ("Connection", "$connection_upgrade"),
        ("Upgrade", "$http_upgrade"),
    )

    def process(self, hosts: List[Host]) -> None:
        for host in hosts:
            for location in host.locations.values():
                location.reinsert_global_config = False
                location.reinserted_proxy_set_headers = []

                container = getattr(location, "container", None)
                if container is None or container.type == "static_site":
                    continue

                overridden_headers = self._overridden_proxy_header_names(location.extras.get("injected", []))
                needs_reinsertion = bool(overridden_headers) or (location.websocket and not location.http)
                if not needs_reinsertion:
                    continue

                headers = list(self._DEFAULT_HEADERS)
                if location.websocket:
                    headers.extend(self._WEBSOCKET_HEADERS)

                overridden = {header.lower() for header in overridden_headers}
                location.reinsert_global_config = True
                location.reinserted_proxy_set_headers = [
                    (header, value) for header, value in headers if header.lower() not in overridden
                ]

    def _overridden_proxy_header_names(self, injected: Iterable[str] | str | None) -> set[str]:
        if injected is None:
            return set()
        directives = injected if isinstance(injected, list) else [injected]
        overridden = set()
        for directive in directives:
            match = self._HEADER_PATTERN.match(str(directive).strip())
            if match is not None:
                overridden.add(match.group(1))
        return overridden
