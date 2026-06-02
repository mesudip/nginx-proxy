import os
import re
import sys

from nginx_proxy.BackendTarget import BackendTarget, InvalidHostConfiguration
from nginx_proxy.Host import Host
from nginx_proxy.ProxyConfigData import ProxyConfigData
from nginx_proxy.pre_processors.virtual_host_processor import _validate_external_host

_SAFE_STATIC_SITE_ROOT = re.compile(r"^[A-Za-z0-9_./-]+$")


def _is_safe_static_site_root(root: str) -> bool:
    return bool(_SAFE_STATIC_SITE_ROOT.fullmatch(root))


def process_static_sites(static_site_root: str = "/static") -> ProxyConfigData:
    hosts = ProxyConfigData()
    root = static_site_root.rstrip("/") or "/"

    if not _is_safe_static_site_root(root):
        print(
            "[static-site] ERROR: STATIC_SITE_ROOT contains spaces or unsupported characters, "
            f"ignoring all static sites: {root}",
            file=sys.stderr,
        )
        return hosts

    try:
        root_exists = os.path.exists(root)
        root_is_dir = os.path.isdir(root) if root_exists else False
    except OSError as e:
        print(f"[static-site] WARNING: Could not inspect root {root}, skipping static sites: {e}", file=sys.stderr)
        return hosts

    if not root_exists:
        print(f"[static-site] Root does not exist, skipping: {root}")
        return hosts
    if not root_is_dir:
        print(f"[static-site] Root is not a directory, skipping: {root}")
        return hosts

    try:
        with os.scandir(root) as entries:
            root_entries = sorted(entries, key=lambda item: item.name)
    except OSError as e:
        print(f"[static-site] WARNING: Could not scan root {root}, skipping static sites: {e}", file=sys.stderr)
        return hosts

    for entry in root_entries:
        try:
            if not entry.is_dir(follow_symlinks=True):
                continue
        except OSError as e:
            print(f"[static-site] WARNING: Could not inspect {entry.path}, skipping: {e}", file=sys.stderr)
            continue

        domain = entry.name.strip()
        host = Host(domain, 443, scheme={"https"})
        try:
            _validate_external_host(host)
        except InvalidHostConfiguration as e:
            reason = e.reason
            print(f"[static-site] Ignoring invalid domain directory: {domain}: {reason}")
            continue

        current_path = os.path.join(root, domain, "current")
        try:
            current_is_dir = os.path.isdir(current_path)
        except OSError as e:
            print(f"[static-site] WARNING: Could not inspect {current_path}, skipping: {e}", file=sys.stderr)
            continue
        if not current_is_dir:
            print(f"[static-site] Ignoring {domain}: missing directory or symlink target {current_path}")
            continue

        backend = BackendTarget(
            id=f"static-site:{domain}",
            name=domain,
            path=current_path,
            backend_type="static_site",
        )
        host.add_container("/", backend, websocket=False, http=True)
        hosts.add_host(host)
        print(f"[static-site] Hosting {domain} from {current_path}")

    return hosts
