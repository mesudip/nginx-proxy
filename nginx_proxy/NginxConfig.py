"""
Handles nginx.conf configuration from environment variables.

Environment variables supported (all prefixed with NGINX_):
- NGINX_WORKER_PROCESSES: Number of worker processes (default: "auto")
- NGINX_WORKER_CONNECTIONS: Number of worker connections (default: 65535)
"""

import os
from jinja2 import Template


# Default values for nginx.conf settings
NGINX_DEFAULTS = {
    "worker_processes": "auto",
    "worker_connections": 65535,
}


def get_nginx_config():
    """
    Load nginx configuration from environment variables.
    Environment variables are prefixed with NGINX_ and converted to lowercase keys.

    Supported variables:
    - NGINX_WORKER_PROCESSES: Number of worker processes (default: "auto")
    - NGINX_WORKER_CONNECTIONS: Number of worker connections (default: 65535)

    Returns:
        dict: Configuration dictionary with nginx settings
    """
    config = NGINX_DEFAULTS.copy()

    # NGINX_WORKER_PROCESSES - can be a number or "auto"
    worker_processes = os.getenv("NGINX_WORKER_PROCESSES", "").strip()
    if worker_processes:
        config["worker_processes"] = worker_processes

    # NGINX_WORKER_CONNECTIONS - must be a positive integer
    worker_connections = os.getenv("NGINX_WORKER_CONNECTIONS", "65535").strip()
    if worker_connections:
        try:
            config["worker_connections"] = int(worker_connections)
        except ValueError:
            print(
                f"[WARNING] Invalid NGINX_WORKER_CONNECTIONS value: {worker_connections}, using default: {NGINX_DEFAULTS['worker_connections']}"
            )

    return config


def render_nginx_conf(template_path: str, output_path: str, extra_config: dict = None) -> bool:
    """
    Render nginx.conf from a Jinja2 template using environment variables.

    Args:
        template_path: Path to nginx.conf.jinja2 template
        output_path: Path to write the rendered nginx.conf
        extra_config: Additional configuration variables for the template

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        config = get_nginx_config()
        if extra_config:
            config.update(extra_config)

        with open(template_path, "r") as f:
            template = Template(f.read())

        rendered = template.render(**config)

        with open(output_path, "w") as f:
            f.write(rendered)

        print(
            f"[nginx.conf] Rendered with: worker_processes={config['worker_processes']}, worker_connections={config['worker_connections']}"
        )
        return True

    except FileNotFoundError:
        print(f"[ERROR] nginx.conf template not found: {template_path}")
        return False
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f"[ERROR] Failed to render nginx.conf: {e}")
        return False
