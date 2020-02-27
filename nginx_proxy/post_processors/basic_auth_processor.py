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

    def generate_htpasswd_file(self, folder, file, securities):
        data = [user + ':' + crypt.crypt(securities[user]) for user in securities]
        folder = os.path.join(self.basic_auth_dir, folder)
        if not os.path.exists(folder):
            os.mkdir(folder)

        file = os.path.join(folder, file)
        with open(file, "w") as openfile:
            openfile.write('\n'.join(data))
        return file

    def process_basic_auth(self, hosts: List[Host]):
        for host in hosts:
            if 'security' in host.extras:
                host.extras['security_file'] = self.generate_htpasswd_file(host.hostname, '_', host.extras['security'])

            for location in host.locations.values():
                if 'security' in location.extras:
                    location.extras['security_file'] = self.generate_htpasswd_file(host.hostname,
                                                                                   location.name.replace('/', '_'),
                                                                                   location.extras['security'])
