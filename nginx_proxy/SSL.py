import datetime
import logging
import os
import shutil
import sys
import time
from os.path import join

import OpenSSL
from OpenSSL import crypto
from certapi import CertAuthority, FileSystemChallengeStore
from certapi.custom_certauthority import CertificateIssuer
from certapi.crypto_classes import ECDSAKey
from nginx.Nginx import Nginx
import certapi
from certapi.crypto import gen_key_ed25519
from certapi.cloudflare_challenge_store import CloudflareChallengeStore

class SSL:

    def __init__(self, ssl_path, nginx: Nginx):
        self.ssl_path = ssl_path
        self.nginx = nginx 
        self.blacklist={}
        x = os.environ.get("LETSENCRYPT_API")
        if x is not None:
            if x.startswith("https://"):
                self.api_url = x
            else:
                self.api_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
        else:
            self.api_url = "https://acme-v02.api.letsencrypt.org/directory"
        self.challenge_store=FileSystemChallengeStore(nginx.challenge_dir)
        self.key_store=certapi.FilesystemKeyStore(ssl_path)

        dns_stores = []
        if os.getenv("CLOUDFLARE_API_TOKEN") is not None:
            dns_stores.append(CloudflareChallengeStore())
        self.cert_authority = CertAuthority(self.challenge_store,
                                                self.key_store,acme_url=self.api_url,dns_stores=[])
        self_root_key=self.key_store.find_key("self-sign.root")
        if self_root_key is None:
            self_root_key= self.key_store.gen_key("self-sign.root")
        self.self_signer=CertificateIssuer(ECDSAKey(self_root_key))


    def cert_file(self, domain):
        return os.path.join(self.key_store.certs_dir, domain + ".crt")

    def private_file(self, domain):
        return os.path.join(self.key_store.keys_dir, domain + ".key")

    def selfsigned_cert_file(self, domain):
        return os.path.join(self.key_store.certs_dir, domain + ".selfsigned.cert")

    def selfsigned_private_file(self, domain):
        return os.path.join(self.key_store.keys_dir, domain + "selfsgned.key")

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

        open(join(self.key_store.certs_dir, CERT_FILE), "wb").write(
            crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        open(join( self.key_store.keys_dir, KEY_FILE), "wb").write(
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
        self.key_store.find_cert(domain)
        return os.path.exists(os.path.join( self.key_store.certs_dir, domain + ".crt")) \
               and os.path.exists(os.path.join( self.key_store.keys_dir, domain + ".key"))

    def cert_exists_wildcard(self, domain):
        return self.wildcard_domain_name(domain) is not None

    def wildcard_domain_name(self, domain,wild_char='*'):
        slices = domain.split('.')
        if len(slices) > 2:
            return wild_char + "." + ('.'.join(slices[1:len(slices)]))
        return None

    def cert_exists_self_signed(self, domain) -> bool:
        return self.cert_exists((domain + ".selfsigned"))

    def reuse(self, domain1, domain2):
        shutil.copy2(os.path.join(self.key_store.certs_dir, domain1 + ".crt"),
                     os.path.join( self.key_store.certs_dir, domain2 + ".crt"))
        shutil.copy2(os.path.join(self.key_store.keys_dir, domain1 + ".key"),
                     os.path.join( self.key_store.keys_dir, domain2 + ".key"))

    def register_certificate(self, req_domain, no_self_check=False, ignore_existing=False): # todo support ignore_existing
        domain = [req_domain] if type(req_domain) is str else req_domain
        domain = [d for d in domain if
                  '.' in d]  # when the domain doesn't have '.' it shouldn't be requested for letsencrypt certificate
        missing_domains = domain if ignore_existing else [x for x in domain if not self.cert_exists(x)]
        verified_domains = domain if no_self_check else self.nginx.verify_domain(missing_domains)

        if len(verified_domains):
            (certs, _) = self.cert_authority.obtainCert(
                verified_domains)  ## this will by default check the existing certs. TODO add override option
            return verified_domains
        elif len(missing_domains):
            print("[SSL-Register]  All requested domains self-verification failed" )
        elif len(domain):
            print("[SSL-Register] Certificates already exists: "+str(domain))
        return verified_domains

    def register_certificate_wildcard(self, domain, no_self_check=False, ignore_existing=False):
        try:
            self.cert_authority.obtainCert(domain)
            return [domain]

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            self.register_certificate_self_sign(domain)
            print("Canont acquire certificate :" + e.__class__.__name__ + ' -> ' + str(e), file=sys.stderr)
            return []



    def is_blacklisted(self, domain):
        # Check if a domain is blacklisted
        if domain in self.blacklist:
            if time.time() < self.blacklist[domain]:
                return True
            else:
                del self.blacklist[domain]  # Remove from blacklist if timeout has passed
        return False

    def add_to_blacklist(self, domain, duration):
        # Add a domain to the blacklist for a specified duration
        self.blacklist[domain] = time.time() + duration

    def register_certificate_or_selfsign(self, domain, no_self_check=False, ignore_existing=False):
        print("[CertificateOrSelfSign] Checking domains:", ', '.join(domain))
        blacklisted=[]
        obtained_certificates = []

        for i in range(0, len(domain), 50):
            sub_list = domain[i:i + 50]
            # Filter out blacklisted domains from the sublist
            filtered_sub_list = [d for d in sub_list if not self.is_blacklisted(d)]

            if len(filtered_sub_list) < len(sub_list):
                existing_blacklist = list(set(sub_list) - set(filtered_sub_list))
                blacklisted.extend(existing_blacklist)
                print("[Blacklist] ignoring previously failed domain for 3 mins:",', '.join(existing_blacklist) )

            # Proceed only with the filtered sublist
            obtained = self.register_certificate(filtered_sub_list, no_self_check=no_self_check,ignore_existing=ignore_existing) if filtered_sub_list else []

            if obtained:
                domain1 = obtained[0]
                for x in obtained[1:]:
                    self.reuse(domain1, x)
                obtained_certificates.extend(obtained)
        obtained_set = set(obtained_certificates).union(set(self.blacklist.keys()))
        self_signed = [x for x in domain if x not in obtained_set]
        if self_signed:
            # Add the self-signed domains to the blacklist
            for domain in self_signed:
                self.add_to_blacklist(domain, 220)
            print("[Self Signing certificates]", self_signed)
            self.register_certificate_self_sign(self_signed)

        return obtained_certificates

    def register_certificate_self_sign(self, domain):
        if type(domain) is str:
            domain=[domain]
        for d in domain:
            if not self.key_store.find_cert(d+".selfsigned"):
                (key, cert) = self.self_signer.create_key_and_cert(d,key_type="ecdsa")
                key_id=self.key_store.save_key(key.key,d+".selfsigned")
                self.key_store.save_cert(key_id,cert,[d],name=d+".selfsigned")