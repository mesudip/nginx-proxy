import sys
import subprocess
import difflib


class Nginx:
    command_config_test = ["nginx", "-t"]
    command_reload = ["nginx", "-s", "reload"]
    command_start = ["nginx"]

    def __init__(self, config_file_path):
        self.config_file_path = config_file_path
        with open(config_file_path) as file:
            self.last_working_config = file.read()

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
        else:
            print(diff)
            self.last_working_config = config_str

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
