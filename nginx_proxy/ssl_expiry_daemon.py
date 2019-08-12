import asyncio
import datetime

import aiodocker

from nginx_proxy.SSL import SSL

docker = aiodocker.Docker()


class CertificateManager:
    def __init__(self, ssl: SSL):
        self.ssl = ssl
        self.certificates = {}
        self.callback = None

    def thread_function(self):
        for c in self.certificates:
            self.ssl.expiry_time()
        SSL.SSL.cert_exists()

    def add_certificate(self, domain: str, expiry_time):
        pass

    def remove_certificate(self, domain: str):
        pass

    def __remaining_days(self, expiry_date):
        return

    def is_valid(self, domain):
        """
        :return: true if certificate exists and is not expired.
        """
        if domain in self.certificates:
            if (self.certificates[domain] - datetime.datetime.now()).days > 2:
                return True
        return False

    def register_callback(self, callback):
        self.callback = callback


async def eventProcessor():
    a = await docker.events.run()
    print(a)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(eventProcessor())
    loop.close()

# class ssl_daemon():
#     add_ssl(hostname,expiry):
#     remove_ssl(hostname,expiry  ):
