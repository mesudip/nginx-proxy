class DummyNginx:
    
    def __init__(self, config_file_path=None,challenge_dir="./.run_data/challenge"):
        self.last_working_config=""
        self.challenge_dir=challenge_dir
        pass

    def start(self) -> bool:
        return True

    def config_test(self) -> bool:
        """
        Test the current nginx configuration to determine whether or not it fails
        :return: true if config test is successful otherwise false
        """
        return True

    def verify_domain(self, domain):
        return []

    def forced_update(self, config_str):
        print("Forced update")
        print(config_str)
        return True

    def update_config(self, config_str) -> bool:
        """
        Change the nginx configuration.
        :param config_str: string containing configuration to be written into config file
        :return: true if the new config was used false if error or if the new configuration is same as previous
        """
        print("Normal update")
        print(config_str)
        return True

    def reload(self) -> bool:
        """
        Reload nginx so that new configurations are applied.
        :return: true if nginx reload was successful false otherwise
        """
        return True
    
    def force_start(self, config_str) -> bool:
        return True

    def wait(self):
        pass