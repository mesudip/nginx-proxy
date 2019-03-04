from certbot_nginx.configurator import NginxConfigurator
from acme_nginx.AcmeV2 import AcmeV2
import os
import logging

class SSl:
    def __init(self,ssl_path,vhost_path):
        self.ssl_path=ssl_path
        self.vhost_path=vhost_path
    def registerCertificate(self,domain):
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
        acme=AcmeV2(
                api_url="https://acme-v02.api.letsencrypt.org/directory",
                logger=logging.getLogger("acme"),
                domains=["www.example.com"],
                account_key=os.path.join(self.ssl_path,"letsencrypt-account.key"),
                domain_key=os.path.join(self.ssl_path,"letsencrypt-domain.key"),
                vhost=self.vhost_path,
                cert_path=os.path.join(self.ssl_path,"letsencrypt-domain.crt"),
                debug=True,
                dns_provider=None,
                skip_nginx_reload=False
            )

        directory=acme.register_account()
        print(directory)
        acme.solve_http_challenge(directory)

        nginx=NginxConfigurator()
        nginx.get_chall_pref()
        nginx.perform()
        nginx.cleanup()
