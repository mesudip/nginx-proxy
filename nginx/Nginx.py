import difflib
import os
import pathlib
import random
import re
import string
import subprocess
import sys
import time
from os import path
from typing import Union, Tuple
import socket

import requests

from nginx import Url


def write_file(file_path: str, content: str):
    """Utility function to write content to a file."""
    with open(file_path, "w") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())  # Ensure data is written to disk


class Nginx:
    command_config_test = ["nginx", "-t"]
    command_stop = ["nginx", "-s", "quit"]
    command_reload = ["nginx", "-s", "reload"]
    command_start = ["nginx"]

    def __init__(self, config_file_path, challenge_dir="/etc/nginx/challenges/"):
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

    def start(self):
        start_result = subprocess.run(Nginx.command_start)
        # if start_result.returncode != 0:
        #     print(start_result.stderr, file=sys.stderr)
        return start_result.returncode == 0

    def stop(self) -> bool:
        start_result = subprocess.run(Nginx.command_stop, stderr=subprocess.PIPE)
        if start_result.returncode != 0:
            print(start_result.stderr, file=sys.stderr)
        return start_result.returncode == 0

    def config_test(self) -> bool:
        """
        Test the current nginx configuration to determine whether or not it fails
        :return: true if config test is successful otherwise false
        """
        test_result = subprocess.run(Nginx.command_config_test, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if test_result.returncode != 0:
            print("Nginx configtest failed!", file=sys.stderr)
            self.last_error = test_result.stderr.decode("utf-8")
            print(self.last_error, file=sys.stderr)
            return False
        return True

    def push_config(self, config_str):

        if config_str == self.last_working_config:
            self.config_stack.append(config_str)
            return self.reload()

        write_file(self.config_file_path, config_str)
        if not self.reload():
            write_file(self.config_file_path, self.config_stack[-1])
            self.reload()
            return False
        else:
            self.config_stack.append(config_str)
            return True

    def pop_config(self):
        write_file(self.config_file_path, self.config_stack.pop())
        return self.reload()

    def force_start(self, config_str) -> bool:
        """
        Simply reload the nginx with the configuration, don't check whether or not configuration is changed or not.
        If change causes nginx to fail, revert to last working config.
        :param config_str:
        :return:
        """
        write_file(self.config_file_path, config_str)
        if not self.start():
            write_file(self.config_file_path, self.last_working_config)
            return False
        else:
            self.last_working_config = config_str
            return True

    def _parse_error_line(self, error_msg):
        if not error_msg:
            return None
        # Try to find the specific error line for the config file we are managing
        config_filename = os.path.basename(self.config_file_path)
        escaped_filename = re.escape(config_filename)
        
        # Search for: filename:line_number
        match = re.search(f"{escaped_filename}:(\\d+)", error_msg)
        if match:
            return int(match.group(1))
            
        # Fallback: Search for any line number pattern usually at end of line in Nginx errors
        lines = error_msg.splitlines()
        for line in lines:
            if "emerg" in line or "error" in line:
                 match = re.search(r':(\d+)(?:\s|$)', line)
                 if match:
                     return int(match.group(1))
        return None

    def _print_error_context(self, config_str, line_no):
        lines = config_str.splitlines()
        total_lines = len(lines)
        if line_no > total_lines or line_no < 1:
            print(f"Error reported at line {line_no}, but config only has {total_lines} lines.", file=sys.stderr)
            return False

        print(f"Error Location in Config (Line {line_no}):", file=sys.stderr)
        
        start_idx = max(0, line_no - 6) 
        end_idx = min(total_lines, line_no + 5)
        
        for i in range(start_idx, end_idx):
            current_line = i + 1
            marker = ">>" if current_line == line_no else "  "
            content = lines[i]
            print(f"{marker} {current_line:4}: {content}", file=sys.stderr)
        return True

    def _print_diff_with_line_numbers(self, old_config, new_config):
        diff_iter = difflib.unified_diff(
            old_config.splitlines(),
            new_config.splitlines(),
            fromfile="Old Config",
            tofile="New Config",
            lineterm="",
        )

        header_regex = re.compile(r"@@\s-([0-9]+)(?:,[0-9]+)?\s\+([0-9]+)(?:,[0-9]+)?\s@@")
        old_line_no = None
        new_line_no = None

        for line in diff_iter:
            if line.startswith("---") or line.startswith("+++"):
                print(line, file=sys.stderr)
                continue

            if line.startswith("@@"):
                print(line, file=sys.stderr)
                match = header_regex.match(line)
                if match:
                    old_line_no = int(match.group(1))
                    new_line_no = int(match.group(2))
                continue

            if line.startswith("-"):
                line_number = old_line_no if old_line_no is not None else "?"
                print(f"- [old {line_number}] {line[1:]}", file=sys.stderr)
                if old_line_no is not None:
                    old_line_no += 1
                continue

            if line.startswith("+"):
                line_number = new_line_no if new_line_no is not None else "?"
                print(f"+ [new {line_number}] {line[1:]}", file=sys.stderr)
                if new_line_no is not None:
                    new_line_no += 1
                continue

            if line.startswith(" "):
                old_label = old_line_no if old_line_no is not None else "?"
                new_label = new_line_no if new_line_no is not None else "?"
                print(f"  [old {old_label} | new {new_label}] {line[1:]}", file=sys.stderr)
                if old_line_no is not None:
                    old_line_no += 1
                if new_line_no is not None:
                    new_line_no += 1
                continue

            # Fallback for special diff lines such as "\\ No newline at end of file"
            print(line, file=sys.stderr)

    def update_config(self, config_str, force=False) -> bool:
        """
        Change the nginx configuration.
        :param config_str: string containing configuration to be written into config file
        :param force: Force reload even if the configuration is same as previous
        :return: true if the new config was used false if error or if the new configuration is same as previous
        """

        if config_str == self.last_working_config and not force:
            print("Configuration not changed, skipping nginx reload")
            return False

        write_file(self.config_file_path, config_str)
        result, data = self.reload(return_error=True)

        if not result:
            printed_context = False
            error_line = self._parse_error_line(data)

            if error_line is not None:
                printed_context = self._print_error_context(config_str, error_line)

            if not printed_context:
                self._print_diff_with_line_numbers(self.last_working_config, config_str)

            if data is not None:
                print(data, file=sys.stderr)
            print("ERROR: New change made nginx to fail. Thus it's rolled back", file=sys.stderr)
            write_file(self.config_file_path, self.last_working_config)
            return False
        else:
            print("Nginx Reloaded Successfully")
            self.last_working_config = config_str
            return True

    def reload(self, return_error=False) -> Union[bool, Tuple[bool, Union[str, None]]]:
        """
        Reload nginx so that new configurations are applied.
        :return: true if nginx reload was successful false otherwise
        """
        reload_result = subprocess.run(Nginx.command_reload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if reload_result.returncode != 0:
            if return_error:
                return False, reload_result.stderr.decode("utf-8")
            else:
                print("Nginx reload failed with exit code ", file=sys.stderr)
                print(reload_result.stderr.decode("utf-8"), file=sys.stderr)
                result = False
        else:
            result = True

        if return_error:
            return result, None
        else:
            return result

    def verify_domain(self, _domain: list | str):
        domain = [_domain] if type(_domain) is str else _domain
        ## when not included, one invalid domain in a list of 100 will make all domains to be unverified due to nginx failing to start.
        domain = [x for x in domain if Url.is_valid_hostname(x)]
        success = []
        while True:
            r1 = "".join([random.choice(string.ascii_letters + string.digits) for _ in range(32)])
            file = os.path.join(self.challenge_dir, r1)
            if path.exists(file):
                continue
            r2 = "".join([random.choice(string.ascii_letters + string.digits) for _ in range(256)])
            write_file(file, r2)
            for d in domain:
                try:
                    url = "http://%s/.well-known/acme-challenge/%s" % (d, r1)
                    response = requests.get(url, allow_redirects=False, timeout=3)
                    if response.status_code == 200:
                        if response.content.decode("utf-8") == r2:
                            success.append(d)
                            continue
                    print(
                        "[Error] ["
                        + d
                        + "] Not owned by this machine:"
                        + "Status Code["
                        + str(response.status_code)
                        + "] -> "
                        + url,
                        file=sys.stderr,
                    )
                    continue
                except requests.exceptions.RequestException as e:
                    error = str(e)
                    if error.find("Name does not resolve") > -1:
                        print("[Error] [" + d + "] Domain Name could not be resolved", file=sys.stderr)
                    elif error.find("Connection refused") > -1:
                        print(
                            "[Error] [" + d + "] Connection Refused! The port is filtered or not open.", file=sys.stderr
                        )
                    else:
                        print("[ERROR] [" + d + "] Not owned by this machine : " + str(e))
                    continue
            os.remove(file)
            break
        if type(_domain) is str:
            return len(success) > 0
        else:
            return success

    def wait(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("127.0.0.1", 80))
        while result != 0:
            print("Waiting for nginx process to be ready")
            time.sleep(1)
            result = sock.connect_ex(("127.0.0.1", 80))
        sock.close()
        print("Nginx is alive")

    def setup(self, default_config_content: str) -> bool:
        """
        Sets up the Nginx server, performing initial configuration tests and starting it.
        If the existing configuration is invalid, it attempts to force-start with a default config.
        :param default_config_content: The content of the default Nginx configuration.
        :return: True if Nginx is successfully set up and started, False otherwise.
        """
        if self.config_test():
            print("Config test succeed")
            if (
                len(self.last_working_config) < 50
            ):  # if the config is too short, just force restart, we might not have any consequences.
                print("Writing default config before reloading server.")
                if not self.force_start(default_config_content):
                    print("Nginx failed when reloaded with default config", file=sys.stderr)
                    print("Exiting .....", file=sys.stderr)
                    return False
            elif not self.start():
                print("ERROR: Config test succeded but nginx failed to start", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
                return False
        else:
            print(
                "ERROR: Existing nginx configuration has error, trying to override with default configuration",
                file=sys.stderr,
            )
            if not self.force_start(default_config_content):
                print("Nginx failed when reloaded with default config", file=sys.stderr)
                print("Exiting .....", file=sys.stderr)
                return False
        print("Now waiting for nginx")
        self.wait()
        return True
