import os
import pathlib
import random
import re
import string
import subprocess
import sys
from os import path

import requests


class Nginx:
    command_config_test = ["nginx", "-t"]
    command_reload = ["nginx", "-s", "reload"]
    command_start = ["nginx"]

    def __init__(self, config_file_path, challenge_dir="/tmp/acme-challenges/"):
        self.challenge_dir = challenge_dir
        self.config_file_path = config_file_path
        if path.exists(config_file_path):
            with open(config_file_path) as file:
                self.last_working_config = file.read()
        else:
            self.last_working_config = ""
        self.config_stack = [self.last_working_config]
        if not os.path.exists(challenge_dir):
            pathlib.Path(self.challenge_dir).mkdir(parents=True)

    def start(self) -> bool:
        start_result = subprocess.run(Nginx.command_start, stderr=subprocess.PIPE)
        if start_result.returncode != 0:
            print(start_result.stderr, file=sys.stderr)
        return start_result.returncode == 0

    def config_test(self) -> bool:
        """
        Test the current nginx configuration to determine whether or not it fails
        :return: true if config test is successful otherwise false
        """
        test_result = subprocess.run(Nginx.command_config_test, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if test_result.returncode is not 0:
            print("Nginx configtest failed!", file=sys.stderr)
            self.last_error = test_result.stderr.decode("utf-8")
            print(self.last_error, file=sys.stderr)
            return False
        return True

    def push_config(self, config_str):

        if config_str == self.last_working_config:
            self.config_stack.append(config_str)
            return self.reload()

        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        if not self.reload():
            with open(self.config_file_path, "w") as file:
                file.write(self.config_stack[-1])
            self.reload()
            return False
        else:
            self.config_stack.append(config_str)
            return True

    def pop_config(self):
        with open(self.config_file_path, "w") as file:
            file.write(self.config_stack.pop())
        return self.reload()

    def forced_update(self, config_str):
        """
        Simply reload the nginx with the configuration, don't check whether or not configuration is changed or not.
        If change causes nginx to fail, revert to last working config.
        :param config_str:
        :return:
        """
        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        if not self.start():
            with open(self.config_file_path, "w") as file:
                file.write(self.last_working_config)
            return False
        else:
            self.last_working_config = config_str
            return True

    def update_config(self, config_str) -> bool:
        """
        Change the nginx configuration.
        :param config_str: string containing configuration to be written into config file
        :return: true if the new config was used false if error or if the new configuration is same as previous
        """
        if config_str == self.last_working_config:
            print("Configuration not changed, skipping nginx reload")
            return False
        # diff = str.join("\n", difflib.unified_diff(self.last_working_config.splitlines(),
        #                                            config_str.splitlines(),
        #                                            fromfile='Old Config',
        #                                            tofile='New Config',
        #                                            lineterm='\n'))
        with open(self.config_file_path, "w") as file:
            file.write(config_str)

        if not self.reload():
            print("ERROR: New change made nginx to fail. Thus it's rolled back", file=sys.stderr)
            with open(self.config_file_path, "w") as file:
                file.write(self.last_working_config)
            return False
        else:
            print("Nginx Reloaded Successfully")
            self.last_working_config = config_str
            return True

    def reload(self) -> bool:
        """
        Reload nginx so that new configurations are applied.
        :return: true if nginx reload was successful false otherwise
        """
        if self.config_test():
            reload_result = subprocess.run(Nginx.command_reload, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
            if reload_result.returncode is not 0:
                print("Nginx reload failed with exit code ", file=sys.stderr)
                print(reload_result.stderr.decode("utf-8"), file=sys.stderr)
                return False
            return True
        return False

    def verify_domain(self, _domain: list or str):
        domain = [_domain] if type(_domain) is str else _domain
        ## when not included, one invalid domain in a list of 100 will make all domains to be unverified due to nginx failing to start.
        domain = [x for x in domain if Nginx.is_valid_hostname(x)]
        success = []
        while True:
            r1 = "".join([random.choice(string.ascii_letters + string.digits) for _ in range(32)])
            file = os.path.join(self.challenge_dir, r1)
            if path.exists(file):
                continue
            r2 = "".join([random.choice(string.ascii_letters + string.digits) for _ in range(256)])
            with open(file, mode="wt") as file_descriptor:
                file_descriptor.write(r2)
            for d in domain:
                try:
                    url = "http://%s/.well-known/acme-challenge/%s" % (d, r1)
                    response = requests.get(url, allow_redirects=False, timeout=3)
                    if response.status_code == 200:
                        if response.content.decode("utf-8") == r2:
                            success.append(d)
                            continue
                    print("[Error] [" + d + "] Not owned by this machine:" + "Status Code[" + str(
                        response.status_code) + "] -> " + url, file=sys.stderr)
                    continue
                except requests.exceptions.RequestException as e:
                    if str(e).find("Name does not resolve") > -1:
                        print("[Error] [" + d + "] Domain Name could not be resolved", file=sys.stderr)
                    else:
                        print("[ERROR] Domain is not owned by this machine : Reason: " + str(e))
                    continue
            os.remove(file)
            break
        if type(_domain) is str:
            return len(success) > 0
        else:
            return success

    @staticmethod
    def is_valid_hostname(hostname: str):
        """
        https://stackoverflow.com/a/33214423/2804342
        :return: True if for valid hostname False otherwise
        """
        if hostname[-1] == ".":
            # strip exactly one dot from the right, if present
            hostname = hostname[:-1]
        if len(hostname) > 253:
            return False

        labels = hostname.split(".")

        # the TLD must be not all-numeric
        if re.match(r"[0-9]+$", labels[-1]):
            return False

        allowed = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)
        return all(allowed.match(label) for label in labels)
