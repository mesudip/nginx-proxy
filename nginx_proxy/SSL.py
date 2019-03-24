from acme_nginx.AcmeV2 import AcmeV2
import os
import logging
import OpenSSL
import datetime


class SSL:
    def __init__(self, ssl_path, vhost_path):
        self.ssl_path = ssl_path
        self.vhost_path = vhost_path
        try:
            os.mkdir(os.path.join(ssl_path, "accounts"))
            os.mkdir(os.path.join(ssl_path, "private"))
            os.mkdir(os.path.join(ssl_path, "certs"))
        except FileExistsError as e:
            pass

    def expiry_time(self, domain) -> datetime:
        path = os.path.join(self.ssl_path, "certs", domain + ".crt")
        if self.cert_exists(domain):
            with open(path) as file:
                x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, file.read())
                return datetime.datetime.strptime(x509.get_notAfter().decode(), "%Y%m%d%H%M%SZ")
        return datetime.timedelta(days=0)

    def cert_exists(self, domain) -> bool:
        if os.path.exists(os.path.join(self.ssl_path, "certs", domain + ".crt")) \
                and os.path.exists(os.path.join(self.ssl_path, "private", domain + ".key")) \
                and os.path.exists(os.path.join(self.ssl_path, "accounts", domain + ".account.key")):
            return True

    def register_certificate(self, domain):
        if self.cert_exists(domain):
            print("Skipped Requesting certificates as they are already present")
        else:
            logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
            acme = AcmeV2(
                api_url="https://acme-staging-v02.api.letsencrypt.org/directory",
                logger=logging.getLogger("acme"),
                domains=[domain],
                account_key=os.path.join(self.ssl_path, "accounts", domain + ".account.key"),
                domain_key=os.path.join(self.ssl_path, "private", domain + ".key"),
                vhost=self.vhost_path,
                cert_path=os.path.join(self.ssl_path, "certs", domain + ".crt"),
                debug=False,
                dns_provider=None,
                skip_nginx_reload=False
            )

            directory = acme.register_account()
            print("content in registration detail", directory);
            acme.solve_http_challenge(directory)
