from certapi import FilesystemChallengeSolver
from nginx.Nginx import Nginx


class NginxChallengeSolver(FilesystemChallengeSolver):
    """
    Nginx-specific implementation of the ChallengeStore, extending FileSystemChallengeStore.
    """

    def __init__(self, directory: str, nginx: Nginx):
        super().__init__(directory)
        self.nginx = nginx

    def supports_domain(self, domain: str) -> bool:
        if "*" in domain:
            return False
        return self.nginx.verify_domain(domain)
