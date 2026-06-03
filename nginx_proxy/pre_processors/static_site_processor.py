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


def _is_path_inside_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


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

    root_realpath = os.path.realpath(root)

    try:
        with os.scandir(root) as entries:
            root_entries = []
            for entry in sorted(entries, key=lambda item: item.name):
                try:
                    if entry.is_dir(follow_symlinks=True):
                        root_entries.append((entry.name.strip(), entry.path))
                except OSError as e:
                    print(f"[static-site] WARNING: Could not inspect {entry.path}, skipping: {e}", file=sys.stderr)
    except OSError as e:
        print(f"[static-site] WARNING: Could not scan root {root}, skipping static sites: {e}", file=sys.stderr)
        return hosts

    for domain, domain_path in root_entries:
        host = Host(domain, 443, scheme={"https"})
        try:
            _validate_external_host(host)
        except InvalidHostConfiguration as e:
            reason = e.reason
            print(f"[static-site] Ignoring invalid domain directory: {domain}: {reason}")
            continue

        current_path = os.path.join(domain_path, "current")
        try:
            current_is_dir = os.path.isdir(current_path)
        except OSError as e:
            print(f"[static-site] WARNING: Could not inspect {current_path}, skipping: {e}", file=sys.stderr)
            continue
        if not current_is_dir:
            print(f"[static-site] Ignoring {domain}: missing directory or symlink target {current_path}")
            continue
        current_realpath = os.path.realpath(current_path)
        if not _is_path_inside_root(current_realpath, root_realpath):
            print(
                "[static-site] WARNING: Ignoring "
                f"{domain}: current symlink target escapes STATIC_SITE_ROOT ({current_path} -> {current_realpath})",
                file=sys.stderr,
            )
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


def process_default_ssl_domains(default_ssl_domains: list[str], site_root: str) -> ProxyConfigData:
    hosts = ProxyConfigData()
    root = site_root.rstrip("/") or "/"

    for domain in default_ssl_domains:
        domain = domain.strip()
        if not domain:
            continue

        host = Host(domain, 443, scheme={"https"})
        try:
            _validate_external_host(host)
        except InvalidHostConfiguration as e:
            print(f"[default-ssl-domain] Ignoring invalid domain: {domain}: {e.reason}")
            continue

        backend = BackendTarget(
            id=f"default-ssl-domain:{domain}",
            name=domain,
            path=root,
            backend_type="static_site",
        )
        host.add_container("/", backend, websocket=False, http=True)
        hosts.add_host(host)
        print(f"[default-ssl-domain] Hosting {domain} from {root}")

    return hosts
