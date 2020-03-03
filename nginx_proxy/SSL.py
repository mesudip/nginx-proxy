import datetime
import logging
import os
import shutil
from os.path import join

import OpenSSL
from OpenSSL import crypto

from acme_nginx.AcmeV2 import AcmeV2
from nginx.Nginx import Nginx


class SSL:

    def __init__(self, ssl_path, nginx: Nginx):
        self.ssl_path = ssl_path
        self.nginx = nginx
        x = os.environ.get("LETSENCRYPT_API")
        if x is not None:
            if x.startswith("https://"):
                self.api_url = x
            else:
                self.api_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
        else:
            self.api_url = "https://acme-v02.api.letsencrypt.org/directory"
        print("Using letsencrypt  url :", self.api_url)

        try:
            os.mkdir(os.path.join(ssl_path, "accounts"))
            os.mkdir(os.path.join(ssl_path, "private"))
            os.mkdir(os.path.join(ssl_path, "certs"))
        except FileExistsError as e:
            pass

    def cert_file(self, domain):
        return os.path.join(self.ssl_path, "certs", domain + ".crt")

    def private_file(self, domain):
        return os.path.join(self.ssl_path, "private", domain + ".key")

    def selfsigned_cert_file(self, domain):
        return os.path.join(self.ssl_path, "certs", domain + ".selfsigned.cert")

    def selfsigned_private_file(self, domain):
        return os.path.join(self.ssl_path, "private", domain + "selfsgned.key")

    def self_sign(self, domain):
        CERT_FILE = domain + ".selfsigned.crt"
        KEY_FILE = domain + ".selfsigned.key"
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 1024)

        # create a self-signed cert
        cert = crypto.X509()
        cert.get_subject().C = "US"
        cert.get_subject().ST = "Subject_st"
        cert.get_subject().L = "Subject_l"
        cert.get_subject().O = "Nginx-Proxy - mesudip/nginx-proxy"
        # cert.get_subject().OU = "my organization"
        cert.get_subject().CN = domain
        cert.set_serial_number(1000)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha256')

        open(join(self.ssl_path, "certs", CERT_FILE), "wb").write(
            crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        open(join(self.ssl_path, "private", KEY_FILE), "wb").write(
            crypto.dump_privatekey(crypto.FILETYPE_PEM, k))

    def expiry_time(self, domain) -> datetime:

        if self.cert_exists(domain):
            with open(self.cert_file(domain)) as file:
                x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, file.read())
                return datetime.datetime.strptime(x509.get_notAfter().decode(), "%Y%m%d%H%M%SZ")
        return datetime.datetime.now()

    def expiry_days_remain(self, domain) -> int:
        return (self.expiry_time(domain) - datetime.datetime.now()).days

    def cert_exists(self, domain):
        return os.path.exists(os.path.join(self.ssl_path, "certs", domain + ".crt")) \
               and os.path.exists(os.path.join(self.ssl_path, "private", domain + ".key"))

    def cert_exists_wildcard(self, domain):
        return self.wildcard_domain_name(domain) is not None

    def wildcard_domain_name(self, domain):
        slices = domain.split('.')
        if len(slices) > 2:
            return '*.' + ('.'.join(slices[1:len(slices)]))
        return None

    def cert_exists_self_signed(self, domain) -> bool:
        return self.cert_exists((domain + ".selfsigned"))

    def reuse(self, domain1, domain2):
        shutil.copy2(os.path.join(self.ssl_path, "certs", domain1 + ".crt"),
                     os.path.join(self.ssl_path, "certs", domain2 + ".crt"))
        shutil.copy2(os.path.join(self.ssl_path, "private", domain1 + ".key"),
                     os.path.join(self.ssl_path, "private", domain2 + ".key"))
        shutil.copy2(os.path.join(self.ssl_path, "accounts", domain1 + ".account.key"),
                     os.path.join(self.ssl_path, "accounts", domain2 + ".account.key"))

    def register_certificate(self, domain, no_self_check=False, ignore_existing=False):
        if type(domain) is str:
            domain = [domain]
        domain = [d for d in domain if
                  '.' in d]  # when the domain doesn't have '.' it shouldn't be requested for letsencrypt certificate
        verified_domain = domain if no_self_check else self.nginx.verify_domain(domain)
        domain = verified_domain if ignore_existing else [x for x in verified_domain if not self.cert_exists(x)]
        if len(domain):
            logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
            acme = AcmeV2(
                api_url=self.api_url,
                logger=logging.getLogger("acme"),
                domains=domain,
                account_key=os.path.join(self.ssl_path, "accounts", domain[0] + ".account.key"),
                domain_key=os.path.join(self.ssl_path, "private", domain[0] + ".key"),
                cert_path=os.path.join(self.ssl_path, "certs", domain[0] + ".crt"),
                debug=False,
                dns_provider=None,
                skip_nginx_reload=False,
                challenge_dir=self.nginx.challenge_dir
            )

            directory = acme.register_account()
            acme.solve_http_challenge(directory)
            return domain
        else:
            return verified_domain

    def register_certificate_or_selfsign(self, domain, no_self_check=False, ignore_existing=False):
        print("[CertificateOrSelfSign] Adding domains:", domain)
        obtained_certificates = []
        for i in range(0, len(domain), 50):
            # only fifty at a time.
            sub_list = domain[i:i + 50]
            obtained = self.register_certificate(sub_list, no_self_check=no_self_check, ignore_existing=ignore_existing)
            if len(obtained):
                domain1 = obtained[0]
                for x in obtained[1:]:
                    self.reuse(domain1, x)
                obtained_certificates.extend(obtained)
        obtained_set = set(obtained_certificates)
        self_signed = [x for x in domain if x not in obtained_set]
        if len(self_signed):
            print("[Self Signing certificates]", self_signed)
        self.register_certificate_self_sign(self_signed)
        return obtained_certificates

    def register_certificate_self_sign(self, domain):
        if type(domain) is str:
            self.self_sign(domain)
        else:
            for d in domain:
                if not self.cert_exists_self_signed(d):
                    self.self_sign(d)
