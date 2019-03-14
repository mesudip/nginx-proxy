from acme_nginx.AcmeV2 import AcmeV2
import os
import logging


class SSL:
    def __init__(self, ssl_path, vhost_path):
        self.ssl_path = ssl_path
        self.vhost_path = vhost_path
        try:
            os.mkdir(os.path.join(ssl_path, "account"))
            os.mkdir(os.path.join(ssl_path, "private"))
            os.mkdir(os.path.join(ssl_path, "certs"))
        except FileExistsError as e:
            pass

    def cert_exists(self, domain) -> bool:
        return os.path.exists(os.path.join(self.ssl_path, "certs", domain + ".cert")) \
               and os.path.exists(os.path.join(self.ssl_path, "private", domain + ".key")) \
               and os.path.exists(os.path.join(self.ssl_path, "account", domain, ".account.key"))

    def register_certificate(self, domain):
        if self.cert_exists(domain):
            print("Skipped Requesting certificates as they are already present")
        else:
            logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
            acme = AcmeV2(
                api_url="https://acme-v02.api.letsencrypt.org/directory",
                logger=logging.getLogger("acme"),
                domains=[domain],
                account_key=os.path.join(self.ssl_path, "accounts", domain + ".account.key"),
                domain_key=os.path.join(self.ssl_path, "private", domain + ".key"),
                vhost=self.vhost_path,
                cert_path=os.path.join(self.ssl_path, "certs", domain + ".crt"),
                debug=True,
                dns_provider=None,
                skip_nginx_reload=False
            )

            directory = acme.register_account()
            acme.solve_http_challenge(directory)
