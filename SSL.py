from acme_nginx.AcmeV2 import AcmeV2
import os
import logging


class SSL:
    def __init__(self, ssl_path, vhost_path):
        self.ssl_path = ssl_path
        self.vhost_path = vhost_path

    def cert_exists(self, domain) -> bool:
        directory = os.path.join(self.ssl_path, domain)
        if os.path.exists(directory):
            if os.path.exists(os.path.join(directory, domain + ".cert")) \
                    and os.path.exists(os.path.join(directory, domain + ".key")) \
                    and os.path.exists(os.path.join(directory, domain, "-account.key")):
                return True
        return False

    def registerCertificate(self, domain):
        if self.cert_exists(domain):
            return "Skipped Requesting certificates as they are already present"
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
        acme = AcmeV2(
            api_url="https://acme-v02.api.letsencrypt.org/directory",
            logger=logging.getLogger("acme"),
            domains=[domain],
            account_key=os.path.join(self.ssl_path, domain + "-account.key"),
            domain_key=os.path.join(self.ssl_path, domain + ".key"),
            vhost=self.vhost_path,
            cert_path=os.path.join(self.ssl_path, domain + ".crt"),
            debug=True,
            dns_provider=None,
            skip_nginx_reload=False
        )

        directory = acme.register_account()
        acme.solve_http_challenge(directory)
