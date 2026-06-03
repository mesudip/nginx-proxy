import re
from typing import Dict, Any, List, Union

from .BackendTarget import BackendTarget


_BODY_SIZE_DIRECTIVES = {"client_max_body_size"}
_PROXY_TIMEOUT_DIRECTIVES = {"proxy_connect_timeout", "proxy_send_timeout", "proxy_read_timeout"}
_SCALAR_DIRECTIVES = _BODY_SIZE_DIRECTIVES | _PROXY_TIMEOUT_DIRECTIVES


def _parse_injected_directive(raw_directive: str):
    directive = str(raw_directive).strip()
    if not directive:
        return None, None
    equal_index = directive.find("=")
    whitespace_match = re.search(r"\s", directive)
    if whitespace_match is not None and (equal_index == -1 or whitespace_match.start() < equal_index):
        key, value = re.split(r"\s+", directive, maxsplit=1)
        key = key.strip()
        value = value.strip()
        return (key, value if value else None) if key else (None, None)
    if equal_index != -1:
        key, value = directive.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            return key, value if value else None
    return directive, None


def _format_injected_directive(key: str, value: str | None) -> str:
    if value is None:
        return key
    return f"{key} {value}"


def _parse_nginx_size(value: str | None):
    if value is None:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMgG]?)", value.strip())
    if match is None:
        return None
    number = float(match.group(1))
    suffix = match.group(2).lower()
    if number == 0:
        return float("inf")
    multiplier = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[suffix]
    return number * multiplier


def _parse_nginx_timeout(value: str | None):
    if value is None:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|M|y)?", value.strip())
    if match is None:
        return None
    number = float(match.group(1))
    suffix = match.group(2) or "s"
    multiplier = {
        "ms": 0.001,
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
        "M": 30 * 24 * 60 * 60,
        "y": 365 * 24 * 60 * 60,
    }[suffix]
    return number * multiplier


class Location:
    """
    Location Represents the Location block in
    """

    def __init__(self, name, is_websocket_backend=False, is_http_backend=True):
        self.http = is_http_backend
        self.websocket = is_websocket_backend
        self.name = name
        self.backends: List[BackendTarget] = []
        self.extras: Dict[str, Any] = {}

    def update_extras(self, extras: Dict[str, Any]):
        for x in extras:
            if x == "injected_by_backend" and isinstance(extras[x], dict):
                existing = self.extras.setdefault("injected_by_backend", {})
                for backend_id, directives in extras[x].items():
                    normalized = directives if isinstance(directives, list) else [directives]
                    existing[backend_id] = list(dict.fromkeys(normalized))
                self._sync_injected_from_backend_map()
                continue
            if x in self.extras:
                data = self.extras[x]
                if type(data) in (dict, set):
                    self.extras[x].update(extras[x])
                elif isinstance(data, list):
                    new_values = extras[x] if isinstance(extras[x], list) else [extras[x]]
                    existing = set(data)
                    for value in new_values:
                        if value not in existing:
                            data.append(value)
                            existing.add(value)
                else:
                    self.extras[x] = extras[x]
            else:
                self.extras[x] = extras[x]

    def _sync_injected_from_backend_map(self):
        backend_map = self.extras.get("injected_by_backend")
        if not isinstance(backend_map, dict):
            return

        merged_by_key: dict[str, dict[str, Any]] = {}
        passthrough: list[str] = []
        passthrough_seen = set()

        for backend_id, directives in backend_map.items():
            if not isinstance(directives, list):
                directives = [directives]
            for directive in directives:
                key, value = _parse_injected_directive(directive)
                if key is None:
                    continue

                if key not in _SCALAR_DIRECTIVES:
                    formatted = _format_injected_directive(key, value)
                    if formatted not in passthrough_seen:
                        passthrough_seen.add(formatted)
                        passthrough.append(formatted)
                    continue

                if key not in merged_by_key:
                    merged_by_key[key] = {"key": key, "value": value, "backend_id": backend_id}
                    continue

                existing = merged_by_key[key]
                if existing["value"] == value:
                    continue

                chosen = self._choose_injected_directive(
                    existing, {"key": key, "value": value, "backend_id": backend_id}
                )
                self._warn_conflicting_injected_directive(existing, backend_id, key, value, chosen["value"])
                merged_by_key[key] = chosen

        for directive in (_format_injected_directive(x["key"], x["value"]) for x in merged_by_key.values()):
            if directive not in passthrough_seen:
                passthrough_seen.add(directive)
                passthrough.append(directive)
        self.extras["injected"] = passthrough

    def _choose_injected_directive(self, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        key = incoming["key"]
        if key in _BODY_SIZE_DIRECTIVES:
            existing_value = _parse_nginx_size(existing["value"])
            incoming_value = _parse_nginx_size(incoming["value"])
            if existing_value is not None and incoming_value is not None:
                return incoming if incoming_value > existing_value else existing
            return incoming

        if key in _PROXY_TIMEOUT_DIRECTIVES:
            existing_value = _parse_nginx_timeout(existing["value"])
            incoming_value = _parse_nginx_timeout(incoming["value"])
            if existing_value is not None and incoming_value is not None:
                return incoming if incoming_value > existing_value else existing
            return incoming

        return incoming

    def _warn_conflicting_injected_directive(
        self,
        existing: dict[str, Any],
        incoming_backend_id: str,
        key: str,
        incoming_value: str | None,
        chosen_value: str | None,
    ):
        print(
            "[WARN] Conflicting nginx location directive "
            f"location={self.name if self.name else '/'} "
            f"key={key} "
            f"existing_backend={existing['backend_id']} existing_value={existing['value']} "
            f"incoming_backend={incoming_backend_id} incoming_value={incoming_value} "
            f"chosen_value={chosen_value}"
        )

    def remove_backend_extras(self, backend_id: str):
        backend_map = self.extras.get("injected_by_backend")
        if not isinstance(backend_map, dict):
            return
        if backend_id in backend_map:
            del backend_map[backend_id]
            self._sync_injected_from_backend_map()
            if not backend_map:
                del self.extras["injected_by_backend"]
            if not self.extras.get("injected"):
                self.extras.pop("injected", None)

    def add(self, container: BackendTarget):
        if not any(c.id == container.id for c in self.backends):
            self.backends.append(container)

    def isempty(self):
        return len(self.backends) == 0

    def remove(self, container: Union[BackendTarget, str]):
        container_id = container.id if isinstance(container, BackendTarget) else container
        for i, c in enumerate(self.backends):
            if c.id == container_id:
                del self.backends[i]
                return c
        return False

    def __eq__(self, other) -> bool:
        if type(other) is Location:
            return other.name == self.name
        return False

    def __repr__(self):
        return str({"name": self.name, "backends": self.backends, "websocket": self.websocket})
