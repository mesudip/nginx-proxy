import datetime
import logging
import os
import shutil
import sys
import time
from os.path import join
from typing import List

import OpenSSL
from OpenSSL import crypto
from nginx.Nginx import Nginx
import certapi
from certapi import CloudflareChallengeSolver,AcmeCertManager,FileSystemKeystore
from certapi.issuers import AcmeCertIssuer, SelfCertIssuer
from certapi.http.types import IssuedCert
from nginx.NginxChallengeSolver import NginxChallengeSolver
import traceback as tb
from nginx_proxy.utils.Blacklist import Blacklist


class SSL:

    def __init__(self, ssl_path, nginx: Nginx):
        self.ssl_path = ssl_path
        self.nginx = nginx
        self.blacklist = Blacklist()
        x = os.environ.get("LETSENCRYPT_API")
        if x is not None:
            if x.startswith("https://") or x.startswith("http://"):
                self.api_url = x
            else:
                self.api_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
        else:
            self.api_url = "https://acme-v02.api.letsencrypt.org/directory"
        
        
        self.challenge_store = NginxChallengeSolver(nginx.challenge_dir, nginx)
        self.key_store = FileSystemKeystore(ssl_path, keys_dir_name="private")
        acme_key=self.key_store._get_or_generate_key("acme_account","rsa")[0]
        print(acme_key)
        cert_issuer=AcmeCertIssuer(acme_key,self.challenge_store,acme_url=self.api_url)


        all_stores = [self.challenge_store]
        if os.getenv("CLOUDFLARE_API_KEY") is not None:
            cloudflare=CloudflareChallengeSolver(os.getenv("CLOUDFLARE_API_KEY").strip())
            all_stores.append(cloudflare)
            print("Sireto.dev supported",cloudflare.supports_domain('sireto.dev'))


        self.cert_manager = AcmeCertManager(
            self.key_store,cert_issuer ,all_stores
        )
        self.cert_manager.setup()
        self.self_signer=SelfCertIssuer(acme_key,"NP","Bagmati","Buddhanagar","nginx-proxy","local.nginx-proxy.com")


    def register_certificate(
        self, req_domain
    )-> List[IssuedCert]:  
        domain = [req_domain] if type(req_domain) is str else req_domain
        result= self.cert_manager.issue_certificate(
                domain,key_type="ecdsa"
            ) 
        return result.issued + result.existing



    def register_certificate_or_selfsign(self, domain, no_self_check=False, ignore_existing=False)->List[IssuedCert]:
        print("[CertificateOrSelfSign] Checking domains:", ", ".join(domain))
        obtained_certificates = []
        

        for i in range(0, len(domain), 50):
            sub_list = domain[i : i + 50]
            # Filter out blacklisted domains from the sublist
            valid_list,blacklisted = self.blacklist.partition(sub_list)

            if len(blacklisted)  >0 :
                print("[Blacklist] ignoring previously failed domain for 3 mins:", ", ".join(blacklisted))

            # Proceed only with the filtered sublist
            obtained:  List[IssuedCert] = (
                self.register_certificate(
                    valid_list,
                )
                if valid_list
                else []
            )

            if obtained:
                obtained_certificates.extend(domain for x in obtained for domain in x.domains)
        processed_set = set(obtained_certificates).union(set(blacklisted))
        self_signed = [x for x in domain if x not in processed_set]
        if self_signed:
            # Add the self-signed domains to the blacklist
            for domain in self_signed:
                self.blacklist.add(domain) # Blacklist class doesn't take duration, it's a simple blacklist
            print("[Self Signing certificates]", self_signed)
            self.register_certificate_self_sign(self_signed)

        return obtained

    def register_certificate_self_sign(self, domain):
        if type(domain) is str:
            domain = [domain]
        for d in domain:
            if not self.key_store.find_key_by_name(d + ".selfsigned"):
                (key, cert) = self.self_signer.generate_key_and_cert_for_domain(d, key_type="ecdsa")
                key_id = self.key_store.save_key(key, d + ".selfsigned")
                self.key_store.save_cert(key_id, cert, [d], name=d + ".selfsigned")
