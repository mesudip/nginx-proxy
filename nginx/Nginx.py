import collections
import string
import sys
import subprocess
import difflib
from random import random
import requests


class Nginx:
    command_config_test = ["nginx", "-t"]
    command_reload = ["nginx", "-s", "reload"]
    command_start = ["nginx"]

    def __init__(self, config_file_path):
        self.config_file_path = config_file_path
        with open(config_file_path) as file:
            self.last_working_config = file.read()
        self.config_stack = [self.last_working_config]

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
            return True

        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        if not self.reload():
            with open(self.config_file_path, "w") as file:
                file.write(self.config_stack[-1])
            return False
        else:
            self.config_stack.append(config_str)
            return True

    def pop_config(self, config_str):
        with open(self.config_file_path, "w") as file:
            file.write(self.config_stack.pop())
        return self.reload()

    def forced_update(self, config_str):
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
        diff = str.join("\n", difflib.unified_diff(self.last_working_config.splitlines(),
                                                   config_str.splitlines(),
                                                   fromfile='Old Config',
                                                   tofile='New Config',
                                                   lineterm='\n'))
        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        if not self.reload():
            print(diff, file=sys.stderr)
            print("ERROR: Above change made nginx to fail. Thus it's rolled back", file=sys.stderr)

            with open(self.config_file_path, "w") as file:
                file.write(self.last_working_config)
            return False
        else:
            print(diff)
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

    def verify_domain(self, domain: list or str):
        if type(domain) is str:
            domain = [domain]
        r1 = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        r2 = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        config = '''{
                listen 80 ;
                server_name %s;
                location %s {
                    redirect http://$host/%s
                }
            }''' % (r1, r2, " ".join(domain))
        if self.push_config(config):
            success = []
            for d in domain:
                response = requests.get("http://{}/{}".format(domain, r1), allow_redirects=False)
                if (response.is_permanent_redirect):
                    if ("Location" in response.headers):
                        if response.headers.get("Location").split("/")[-1] == r2:
                            success.append(d)
                            continue
                print("[ERROR] Domain is not owned by this machine :" + d, file=sys.stderr)
        else:
            return False
        self.pop_config()
        if len(success) == len(domain):
            return True
        return success
