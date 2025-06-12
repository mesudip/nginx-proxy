import hashlib
import json
import re
import sys
import time
from urllib.request import urlopen, Request  # Python 3

from .Acme import Acme
from .DigitalOcean import DigitalOcean
from .Cloudflare import Cloudflare


class AcmeV2(Acme):
    def register_account(self):
        """
        Generate new 2049 bit account key, domain key if not generated
        Register account key with ACME
        Return:
             dict, directory data from acme server
        """
        try:
            self.log.info('trying to create account key {0}'.format(self.account_key))
            self.create_key(self.account_key)
        except (KeyboardInterrupt, SystemExit) as e:
            raise e
        except Exception as e:
            self.log.error('creating key {0} {1}'.format(type(e).__name__, e))
            sys.exit(1)
        # directory is needed later for order placement
        directory = urlopen(Request(
            self.api_url,
            headers={"Content-Type": "application/jose+json"})
        ).read().decode('utf8')
        directory = json.loads(directory)
        directory['_kid'] = None
        self.log.info('trying to register acmev2 account')
        payload = {"termsOfServiceAgreed": True}
        code, result, headers = self._send_signed_request(
            url=directory['newAccount'],
            payload=payload,
            directory=directory
        )
        if code == 201:
            self.log.info('registered!')
        elif code == 200:
            self.log.info('already registered')
        else:
            self.log.error('error registering: {0} {1} {2}'.format(code, result, headers))
            sys.exit(1)
        directory['_kid'] = headers['Location']
        try:
            self.log.info('trying to create domain key')
            self.create_key(self.domain_key)
        except (KeyboardInterrupt, SystemExit) as e:
            raise e
        except Exception as e:
            self.log.error('creating key {0} {1}'.format(type(e).__name__, e))
            sys.exit(1)
        return directory

    def _sign_certificate(self, order, directory):
        """ Sign certificate """
        self.log.info('signing certificate')
        csr = self.create_csr()
        payload = {"csr": self._b64(csr)}
        time.sleep(4)
        code, result, headers = self._send_signed_request(url=order['finalize'], payload=payload, directory=directory)
        self.log.debug('{0}, {1}, {2}'.format(code, result, headers))
        if code > 399:
            self.log.error("error signing certificate: {0} {1}".format(code, result))
            self._reload_nginx()
            return False

        order_url = headers['Location']
        max_attempts = 4
        attempts = 0
        certificate_url = None

        while attempts < max_attempts:
            self.log.info(f'polling order status (attempt {attempts + 1}/{max_attempts})')
            try:
                order_result = urlopen(order_url).read().decode('utf8')
            except Exception as e:
                self.log.error(f"error polling order status: {type(e).__name__} {e}")
                self._reload_nginx()
                return False
            
            order_json = json.loads(order_result)
            self.log.debug(f"Order status: {order_json['status']}")

            if order_json['status'] == 'valid':
                certificate_url = order_json.get('certificate')
                if certificate_url:
                    self.log.info('certificate is valid and URL found!')
                    break
                else:
                    self.log.error('order status is valid but no certificate URL found.')
                    self._reload_nginx()
                    return False
            elif order_json['status'] == 'pending' or order_json['status'] == 'processing':
                self.log.info('order still pending or processing, waiting...')
                time.sleep(5)
            else:
                self.log.error(f"unexpected order status: {order_json['status']}")
                self._reload_nginx()
                return False
            attempts += 1
        
        if not certificate_url:
            self.log.error('failed to get valid certificate URL after multiple attempts.')
            self._reload_nginx()
            return False

        self.log.info('downloading certificate')
        certificate_pem = urlopen(certificate_url).read().decode('utf8')
        self.log.info('writing result file in {0}'.format(self.cert_path))
        try:
            with open(self.cert_path, 'w') as fd:
                fd.write(certificate_pem)
        except (KeyboardInterrupt, SystemExit) as e:
            raise e
        except Exception as e:
            self.log.error('error writing cert: {0} {1}'.format(type(e).__name__, e))
            return False
        if not self.skip_nginx_reload:
            self._reload_nginx()
        return True

    def solve_http_challenge(self, directory):
        """
        Solve HTTP challenge
        Params:
            directory, dict, directory data from acme server
        """
        self.log.info('acmev2 http challenge')
        self.log.info('preparing new order')
        order_payload = {"identifiers": [{"type": "dns", "value": d} for d in self.domains]}
        code, order, _ = self._send_signed_request(
            url=directory['newOrder'],
            payload=order_payload,
            directory=directory
        )
        if code > 299 or code < 200:
            self.log.error("Unexpected response: " + str(code) + " ->" + str(order))
            return False
        self.log.debug(order)
        order = json.loads(order)
        self.log.info('order created')
        for url in order['authorizations']:
            auth = json.loads(urlopen(url).read().decode('utf8'))
            self.log.debug(json.dumps(auth))
            domain = auth['identifier']['value']
            self.log.info('verifying domain {0}'.format(domain))
            challenge = self._get_challenge(auth['challenges'], "http-01")
            token = re.sub(r"[^A-Za-z0-9_\-]", "_", challenge['token'])
            thumbprint = self._thumbprint()
            self.log.info('adding nginx virtual host and completing challenge')
            try:
                self._write_challenge(token, thumbprint)
            except (KeyboardInterrupt, SystemExit) as e:
                raise e
            except Exception as e:
                self.log.error('error adding virtual host {0} {1}'.format(type(e).__name__, e))
                return False
            self.log.info('asking acme server to verify challenge')
            code, result, _ = self._send_signed_request(url=challenge['url'], directory=directory)
            try:
                if code > 399:
                    self.log.error("error triggering challenge: {0} {1}".format(code, result))
                    pass
                if not self._verify_challenge(url, domain):
                    pass
            finally:
                self._cleanup(['{0}/{1}'.format(self.challenge_dir, token)])
        return self._sign_certificate(order, directory)

    def solve_dns_challenge(self, directory, client):
        """
        Solve DNS challengesys.exit(1)
        Params:
            directory, dict, directory data from acme server
            client, object, dns provider client implementation
        """
        self.log.info('acmev2 dns challenge')
        self.log.info('preparing new order')
        order_payload = {"identifiers": [{"type": "dns", "value": d} for d in self.domains]}
        code, order, _ = self._send_signed_request(
            url=directory['newOrder'],
            payload=order_payload,
            directory=directory
        )
        self.log.debug(order)
        order = json.loads(order)
        self.log.info('order created')
        for url in order['authorizations']:
            auth = json.loads(urlopen(url).read().decode('utf8'))
            self.log.debug(json.dumps(auth))
            domain = auth['identifier']['value']
            self.log.info('verifying domain {0}'.format(domain))
            challenge = self._get_challenge(auth['challenges'], "dns-01")
            token = re.sub(r"[^A-Za-z0-9_\-]", "_", challenge['token'])
            thumbprint = self._thumbprint()
            keyauthorization = "{0}.{1}".format(token, thumbprint)
            txt_record = self._b64(hashlib.sha256(keyauthorization.encode('utf8')).digest())
            self.log.info('creating TXT dns record _acme-challenge.{0} IN TXT {1}'.format(domain, txt_record))
            try:
                record = client.create_record(
                    domain=domain,
                    name='_acme-challenge.{0}.'.format(domain.lstrip('*.').rstrip('.')),
                    data=txt_record)
            except (KeyboardInterrupt, SystemExit) as e:
                raise e
            except Exception as e:
                self.log.error('error creating dns record')
                self.log.error(e)
                sys.exit(1)
            try:
                time.sleep(6)
                self.log.info('asking acme server to verify challenge')
                payload = {"keyAuthorization": keyauthorization}
                code, result, headers = self._send_signed_request(
                    url=challenge['url'],
                    payload=payload,
                    directory=directory
                )
                if code > 399:
                    self.log.error("error triggering challenge: {0} {1}".format(code, result))
                    raise Exception(result)
                if not self._verify_challenge(url, domain):
                    sys.exit(1)
            finally:
                try:
                    if not self.debug:
                        self.log.info('delete dns record')
                        client.delete_record(domain=domain, record=record)
                except (KeyboardInterrupt, SystemExit) as e:
                    raise e
                except Exception as e:
                    self.log.error('error deleting dns record')
                    self.log.error(e)
        return self._sign_certificate(order, directory)

    def get_certificate(self):
        directory = self.register_account() 
        if self.dns_provider:
            if self.dns_provider == 'digitalocean':
                dns_client = DigitalOcean()
            elif self.dns_provider == 'cloudflare':
                dns_client = Cloudflare()
            else:
                raise Exception('Unknown DNS provider: {0}'.format(self.dns_provider))
            self.solve_dns_challenge(directory, dns_client)
        else:
            self.solve_http_challenge(directory)
