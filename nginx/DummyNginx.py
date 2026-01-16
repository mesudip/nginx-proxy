import os
import pathlib
import difflib


class DummyNginx:

    def __init__(self, config_file_path, challenge_dir="/tmp/acme-challenges/"):
        self.challenge_dir = challenge_dir
        self.config_file_path = config_file_path
        if os.path.exists(config_file_path):
            with open(config_file_path) as file:
                self.current_config = file.read()
        else:
            self.current_config = ""
        if not os.path.exists(os.path.dirname(config_file_path)):
            pathlib.Path(os.path.dirname(config_file_path)).mkdir(parents=True)
        if not os.path.exists(challenge_dir):
            pathlib.Path(self.challenge_dir).mkdir(parents=True)
        self.last_diff = ""

    def start(self) -> bool:
        print("DummyNginx: Start")
        return True

    def stop(self) -> bool:
        print("DummyNginx: Stopping")
        return True

    def config_test(self) -> bool:
        """
        Test the current nginx configuration to determine whether or not it fails
        :return: true if config test is successful otherwise false
        """
        return True

    def verify_domain(self, domain):
        return False

    def forced_update(self, config_str):
        print("DummyNginx: Forced update (config written to file)")
        if config_str != self.current_config:
            diff = difflib.unified_diff(
                self.current_config.splitlines(keepends=True),
                config_str.splitlines(keepends=True),
                fromfile="old_config",
                tofile="new_config",
            )
            print("DummyNginx: Config Diff:\n" + "".join(diff))
        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        self.current_config = config_str
        return True

    def update_config(self, config_str,force=False) -> bool:
        """
        Change the nginx configuration.
        :param config_str: string containing configuration to be written into config file
        :return: true if the new config was used false if error or if the new configuration is same as previous
        """
        if config_str == self.current_config:
            print("DummyNginx: Configuration not changed, skipping update")
            return False

        print("DummyNginx: Normal update (config written to file)")
        diff = difflib.unified_diff(
            self.current_config.splitlines(keepends=True),
            config_str.splitlines(keepends=True),
            fromfile="old_config",
            tofile="new_config",
        )
        self.last_diff = "".join(diff)
        print("DummyNginx: Config Diff:\n", self.last_diff, sep="")

        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        self.current_config = config_str
        return True

    def reload(self, return_error=False):
        """
        Reload nginx so that new configurations are applied.
        :return: true if nginx reload was successful false otherwise
        """
        print("DummyNginx: Reload (no actual reload performed)")
        if return_error:
            return True, None
        return True

    def force_start(self, config_str) -> bool:
        print("DummyNginx: Force start (config written to file)")
        if config_str != self.current_config:
            diff = difflib.unified_diff(
                self.current_config.splitlines(keepends=True),
                config_str.splitlines(keepends=True),
                fromfile="old_config",
                tofile="new_config",
            )
            print("DummyNginx: Config Diff:\n" + "".join(diff))
        with open(self.config_file_path, "w") as file:
            file.write(config_str)
        self.current_config = config_str
        return True

    def wait(self):
        print("DummyNginx: Wait (no actual wait performed)")
        pass

    def setup(self, config_str) -> bool:
        print("DummyNginx: Setup (config written to file)")
        return self.forced_update(config_str)
