import json
import time
from os import getenv
import traceback
from urllib.request import urlopen, Request


class Cloudflare(object):
    name='cloudflare'
    def __init__(self):
        self.token = getenv('CLOUDFLARE_API_TOKEN')
        self.account_id=getenv('CLOUDFLARE_ACCOUNT_ID')
        self.api = "https://api.cloudflare.com/client/v4"
        if not self.token:
            raise Exception('CLOUDFLARE_API_TOKEN not found in environment')

        self._zones_cache = None
        self._zones_cache_time = 0  # Unix timestamp of last cache update

    def check_token(self):
        if self.account_id:
            api_url = "{0}/accounts/{1}/tokens/verify".format(self.api, self.account_id)
            request_headers = self._cloudflare_headers()
            try:
                response = urlopen(Request(api_url, headers=request_headers))
                if response.getcode() == 200:
                    result = json.loads(response.read().decode('utf8'))
                    if result.get('success') and result.get('result', {}).get('status') == 'active':
                        print("Cloudflare API Token is valid and active.")
                        return True
                    else:
                        print(f"Cloudflare API Token verification failed: {result.get('messages', result.get('errors'))}")
                        return False
                else:
                    print(f"Cloudflare API Token verification failed with status code: {response.getcode()}")
                    return False
            except Exception as e:
                print(f"Error during Cloudflare API Token verification: {e}")
                return False
        else:
            print("CLOUDFLARE_ACCOUNT_ID not set. Cannot verify token without account ID.")
            return False

    def _cloudflare_headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer "+self.token
        }
    def _get_zones(self):
        """ Fetch and cache Cloudflare zones """
        # Cache for 1 day (86400 seconds)
        if self._zones_cache and (time.time() - self._zones_cache_time) < 86400:
            return self._zones_cache

        request_headers = self._cloudflare_headers()
        api_url = "{0}/zones".format(self.api)
        response = urlopen(Request(api_url, headers=request_headers))
        if response.getcode() != 200:
            raise Exception(json.loads(response.read().decode('utf8')))
        
        zones = json.loads(response.read().decode('utf8'))['result']
        self._zones_cache = zones
        self._zones_cache_time = time.time()
        return zones

    def _get_zone_id(self, domain):
        """ Determine Cloudflare Zone ID for a given domain """
        zones = self._get_zones()
        for zone in zones:
            if zone['name'] == domain:
                return zone['id']
        raise Exception("No Cloudflare zone found for domain: {0}".format(domain))

    def determine_domain(self, domain):
        """ Determine registered domain in API """
        # For Cloudflare, we need the base domain to get the zone ID
        # The domain passed here might be a subdomain or wildcard, e.g., 'sub.example.com' or '*.example.com'
        # We need to find the root domain (e.g., 'example.com') that is registered as a Cloudflare zone.
        parts = domain.split('.')
        err=None
        for i in range(len(parts)):
            potential_domain = ".".join(parts[i:])
            try:
                self._get_zone_id(potential_domain)
                return potential_domain
            except Exception as e:
                err=e
                continue
        if err:
            raise err
        else:
            raise Exception("Could not determine Cloudflare registered domain for: {0}".format(domain))

    def create_record(self, name, data, domain):
        """
        Create DNS record
        Params:
            name, string, record name (e.g., _acme-challenge.example.com)
            data, string, record data (e.g., ACME challenge token)
            domain, string, dns domain (e.g., example.com)
        Return:
            record_id, string, created record id
        """
        registered_domain = self.determine_domain(domain)
        zone_id = self._get_zone_id(registered_domain)
        api_url = "{0}/zones/{1}/dns_records".format(self.api, zone_id)
        request_headers = self._cloudflare_headers()
        request_data = {
            "type": "TXT",
            "name": name,
            "content": data,
            "ttl": 120,  # Cloudflare minimum TTL for TXT is 120 seconds
            "proxied": False
        }
        response = urlopen(Request(
            api_url,
            data=json.dumps(request_data).encode('utf8'),
            headers=request_headers)
        )
        
        if response.getcode() != 200:
            raise Exception(json.loads(response.read().decode('utf8')))
        result=response.read().decode('utf8')
        print("Cloudflare create record",name,result)
        return json.loads(result)['result']['id']

    def delete_record(self, record, domain):
        """
        Delete DNS record
        Params:
            record, string, record id number
            domain, string, dns domain
        """
        registered_domain = self.determine_domain(domain)
        zone_id = self._get_zone_id(registered_domain)
        api_url = "{0}/zones/{1}/dns_records/{2}".format(self.api, zone_id, record)
        request_headers = self._cloudflare_headers()
        request = Request(api_url, headers=request_headers)
        request.get_method = lambda: 'DELETE'
        response = urlopen(request)
        result=response.read().decode('utf8')
        print(f"Delete dns record [{response.getcode()}]",result)
        if response.getcode() != 200:
            raise Exception(json.loads(response.read().decode('utf8')))
