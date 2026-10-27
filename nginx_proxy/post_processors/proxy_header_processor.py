import re
from typing import Iterable, List, Sequence, Tuple

from nginx.ConfigParser import ConfigParser
from nginx_proxy.Host import Host


class ProxyHeaderProcessor:
    """
    Re-emit the globally configured headers into locations that would otherwise lose them.

    nginx inherits array directives such as `proxy_set_header` and `add_header` from the
    enclosing level only when the current level declares none of that directive. Declaring
    a single one in a location therefore discards the whole inherited set for that
    directive, and nginx falls back to its own built-in defaults (`Host $proxy_host`, which
    renders as the upstream group name). Each directive inherits independently, so an
    injected `add_header` drops the inherited `add_header`s while leaving `proxy_set_header`
    untouched, and vice versa.
    """

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
    # Mirrors the http-level add_header in vhosts_template/default.conf.jinja2;
    # test_http_level_add_header_matches_processor_defaults guards them against drift.
    _DEFAULT_ADD_HEADERS: Sequence[Tuple[str, str]] = (("Strict-Transport-Security", '"max-age=31536000" always'),)

    _HEADER_PATTERNS = {
        "proxy_set_header": re.compile(r"^\s*proxy_set_header\s+([^\s;]+)", re.IGNORECASE),
        "add_header": re.compile(r"^\s*add_header\s+([^\s;]+)", re.IGNORECASE),
    }

    def process(self, hosts: List[Host]) -> None:
        for host in hosts:
            for location in host.locations.values():
                location.reinsert_global_config = False
                location.reinserted_proxy_set_headers = []
                location.reinserted_add_headers = []

                container = getattr(location, "container", None)
                if container is None:
                    continue

                # A static site is served from disk: the location has no proxy_pass, so
                # proxy_set_header would do nothing there. add_header does apply to the
                # files it serves, and injecting one still drops the inherited set.
                serves_from_disk = container.type == "static_site"

                declared = self._declared_directives(location)
                overridden_proxy = self._overridden_header_names(declared, "proxy_set_header")
                overridden_add = self._overridden_header_names(declared, "add_header")

                # A websocket location needs Upgrade/Connection, and this processor is the
                # only thing that emits them (_WEBSOCKET_HEADERS below). Emitting them is
                # itself what discards the inherited proxy_set_headers, so the defaults have
                # to ride along in the same list even when nothing was injected.
                needs_proxy_headers = not serves_from_disk and (bool(overridden_proxy) or location.websocket)
                needs_add_headers = bool(overridden_add)
                if not needs_proxy_headers and not needs_add_headers:
                    continue

                location.reinsert_global_config = True

                if needs_proxy_headers:
                    headers = list(self._DEFAULT_HEADERS)
                    if location.websocket:
                        headers.extend(self._WEBSOCKET_HEADERS)
                    location.reinserted_proxy_set_headers = self._not_overridden(headers, overridden_proxy)

                if needs_add_headers:
                    location.reinserted_add_headers = self._not_overridden(self._DEFAULT_ADD_HEADERS, overridden_add)

    @staticmethod
    def _declared_directives(location) -> list[str]:
        """
        Every directive the rendered location block will contain, one entry per directive.

        `injected` already contains one directive per entry. `vhost_config` is a
        static site's nginx_vhost.conf emitted verbatim, so parse it as an nginx
        location body. Both land inside the same location block, so a header declared
        in either one discards the inherited set and has to be accounted for here.
        """
        injected = location.extras.get("injected", [])
        directives = [injected] if isinstance(injected, str) else list(injected or [])

        vhost_config = location.extras.get("vhost_config")
        if isinstance(vhost_config, str):
            parser = ConfigParser()
            parser.load(f"location / {{\n{vhost_config}\n}}\n")
            locations = parser.data.get_blocks("location")
            if locations:
                directives.extend(
                    " ".join([entry.name, *entry.values])
                    for entry in locations[0].contents
                    if entry.is_direction()
                )

        return directives

    @staticmethod
    def _not_overridden(headers: Sequence[Tuple[str, str]], overridden: set[str]) -> list[tuple[str, str]]:
        lowered = {header.lower() for header in overridden}
        return [(header, value) for header, value in headers if header.lower() not in lowered]

    def _overridden_header_names(self, injected: Iterable[str] | str | None, directive: str) -> set[str]:
        if injected is None:
            return set()
        pattern = self._HEADER_PATTERNS[directive]
        directives = [injected] if isinstance(injected, str) else injected
        overridden = set()
        for entry in directives:
            match = pattern.match(str(entry).strip())
            if match is not None:
                overridden.add(match.group(1))
        return overridden
