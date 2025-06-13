import os
from pathlib import Path
from typing import List, Dict
import bcrypt

from nginx_proxy.Host import Host


class BasicAuthProcessor:
    def __init__(self, basic_auth_dir: str = "/etc/nginx/basic_auth"):
        self.cache: Dict[str, str] = {}
        self.basic_auth_dir = basic_auth_dir
        if not os.path.exists(basic_auth_dir):
            Path(basic_auth_dir).mkdir(parents=True)

    @staticmethod
    def hash_password_bcrypt(password: str) -> str:
        """Return bcrypt-hashed password in htpasswd-compatible format."""
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        # NGINX/Apache require $2y$ prefix (not $2b$), so we replace it
        return hashed.decode("utf-8").replace("$2b$", "$2y$")

    def generate_htpasswd_file(self, folder: str, file: str, securities: Dict[str, str]) -> str:
        folder_path = os.path.join(self.basic_auth_dir, folder)
        os.makedirs(folder_path, exist_ok=True)

        file_path = os.path.join(folder_path, file)
        with open(file_path, "w") as f:
            for user, password in securities.items():
                hashed = self.hash_password_bcrypt(password)
                f.write(f"{user}:{hashed}\n")
        return file_path

    def process_basic_auth(self, hosts: List[Host]):
        for host in hosts:
            if "security" in host.extras:
                host.extras["security_file"] = self.generate_htpasswd_file(host.hostname, "_", host.extras["security"])

            for location in host.locations.values():
                if "security" in location.extras:
                    location.extras["security_file"] = self.generate_htpasswd_file(
                        host.hostname, location.name.replace("/", "_"), location.extras["security"]
                    )
