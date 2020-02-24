import crypt
import os
from pathlib import Path
from random import random
from typing import List, Dict

from nginx_proxy.Host import Host


class BasicAuthProcessor():
    def __init__(self, basic_auth_dir: str = "/etc/nginx/basic_auth"):
        self.cache: Dict[str:hash] = {}
        self.basic_auth_dir = basic_auth_dir
        if not os.path.exists(basic_auth_dir):
            Path(basic_auth_dir).mkdir(parents=True)
        # self.certificate_expiry_thread = threading.Thread(target=self.check_certificate_expiry)
        #         self.certificate_expiry_thread.start()

    @staticmethod
    def salt():
        """Returns a string of 2 randome letters"""
        letters = 'abcdefghijklmnopqrstuvwxyz' \
                  'ABCDEFGHIJKLMNOPQRSTUVWXYZ' \
                  '0123456789/.'
        return random.choice(letters) + random.choice(letters)

    def process_basic_auth(self, hosts: List[Host]):
        for host in hosts:
            for location in host.locations.values():
                if 'security' in location.extras:
                    securities: Dict[str, str] = location.extras['security']
                    data = [user + ':' + crypt.crypt(securities[user]) for user in securities]
                    folder = os.path.join(self.basic_auth_dir, host.hostname)
                    if not os.path.exists(folder):
                        os.mkdir(folder)
                    file = os.path.join(folder, location.name.replace('/', '_'))
                    location.basic_auth_file = file
                    with open(file, "w") as openfile:
                        openfile.write('\n'.join(data))
