import time
from os import getenv
from certapi.challenge_solver.dns.cloudflare.cloudflare_client import Cloudflare
from certapi import CertApiException
from urllib.parse import urlencode

# Assuming CertApiException and HttpClientBase are defined elsewhere
# class CertApiException(Exception): pass
# class HttpClientBase: ... (with _get, _post, _delete, _put methods)


class CloudflareTunnelManager:
    def __init__(self, api_key: str = None):
        self.cloudflare = Cloudflare(api_key)
        self.account_id = self._get_account_id()
        self.tunnel_name = "nginx-multi-tunnel"
        tunnels = self.list_tunnels(name=self.tunnel_name)
        if tunnels:
            self.tunnel_id = tunnels[0]["id"]
        else:
            tunnel = self.create_tunnel(name=self.tunnel_name, config_src="cloudflare")
            self.tunnel_id = tunnel["id"]

    def _get_account_id(self):
        zones = self.cloudflare._get_zones()
        if not zones:
            raise CertApiException("No zones found, cannot determine account ID", step="Cloudflare._get_account_id")
        return zones[0]["account"]["id"]

    def list_tunnels(self, name: str = None, uuid: str = None, is_deleted: bool = False):
        api_url = f"{self.cloudflare.api_base_url}/accounts/{self.account_id}/cfd_tunnel"
        params = {}
        if name:
            params["name"] = name
        if uuid:
            params["uuid"] = uuid
        if is_deleted:
            params["is_deleted"] = "true"
        response = self.cloudflare._get(api_url, params=params, step="Cloudflare List Tunnels")
        return response.json()["result"]

    def create_tunnel(self, name: str, secret: str = None, config_src: str = "local"):
        api_url = f"{self.cloudflare.api_base_url}/accounts/{self.account_id}/cfd_tunnel"
        body = {"name": name, "config_src": config_src}
        if secret:
            body["tunnel_secret"] = secret
        response = self.cloudflare._post(api_url, "Cloudflare Create Tunnel", json_data=body)
        return response.json()["result"]

    def delete_tunnel(self, tunnel_id: str):
        api_url = f"{self.cloudflare.api_base_url}/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}"
        self.cloudflare._delete(api_url, step="Cloudflare Delete Tunnel")

    def get_config(self):
        api_url = (
            f"{self.cloudflare.api_base_url}/accounts/{self.account_id}/cfd_tunnel/{self.tunnel_id}/configurations"
        )
        response = self.cloudflare._get(api_url, step="Cloudflare Get Tunnel Config")
        return response.json()["result"]["config"]

    def update_config(self, config):
        api_url = (
            f"{self.cloudflare.api_base_url}/accounts/{self.account_id}/cfd_tunnel/{self.tunnel_id}/configurations"
        )
        return self.cloudflare._req_with_retry(
            "PUT", api_url, json_data={"config": config}, step="Cloudflare Update Tunnel Config"
        )

    def _list_dns_records(self, domain: str, type: str = None, name: str = None) -> list:
        zone_id = self.cloudflare._determine_zone_id(domain)
        params = {}
        if type:
            params["type"] = type
        if name:
            params["name"] = name
        query_string = urlencode(params)
        api_url = f"{self.cloudflare.api_base_url}/zones/{zone_id}/dns_records" + (
            f"?{query_string}" if query_string else ""
        )
        response = self.cloudflare._get(api_url, step="Cloudflare List DNS Records")
        result = response.json()
        if not result.get("success"):
            raise CertApiException(
                "Unknown error listing DNS records",
                detail=result.get("errors", "Unknown error listing DNS records"),
                step="Cloudflare List DNS Records",
            )
        return result["result"]

    def _create_dns_record(self, domain: str, type: str, name: str, content: str, ttl: int = 1, proxied: bool = True):
        zone_id = self.cloudflare._determine_zone_id(domain)
        api_url = f"{self.cloudflare.api_base_url}/zones/{zone_id}/dns_records"
        request_data = {
            "type": type,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
        }
        response = self.cloudflare._post(api_url, json_data=request_data, step="Cloudflare Create DNS Record")
        result = response.json()
        if not result.get("success"):
            raise CertApiException(
                "Unknown error creating DNS record",
                detail=result.get("errors", "Unknown error creating DNS record"),
                step="Cloudflare Create DNS Record",
            )
        return result["result"]["id"]

    def add_tunnel(self, hostname: str):
        # Check if already exists
        existing_tunnel_id = self._get_tunnel_id_by_hostname(hostname)
        if existing_tunnel_id:
            if existing_tunnel_id != self.tunnel_id:
                raise ValueError(f"Hostname '{hostname}' points to a different tunnel: {existing_tunnel_id}")
            else:
                pass
        # Create the proxied CNAME DNS record
        tunnel_cname = f"{self.tunnel_id}.cfargotunnel.com"
        self._create_dns_record(domain=hostname, type="CNAME", name=hostname, content=tunnel_cname, proxied=True, ttl=1)

        # Update tunnel config
        config = self.get_config()
        print("config", config)
        ingress = config.get("ingress", [])
        catch_all = {"service": "http_status:404"}
        has_catch_all = ingress and ingress[-1] == catch_all
        if has_catch_all:
            ingress = ingress[:-1]

        new_rule = {
            "hostname": hostname,
            "service": "http://localhost:80",
            "originRequest": {"httpHostHeader": hostname},
        }
        ingress.append(new_rule)

        if has_catch_all or len(ingress) > 1:
            ingress.append(catch_all)
        else:
            ingress.append(catch_all)  # Always add catch-all

        new_config = {"ingress": ingress}
        if "warp-routing" in config:
            new_config["warp-routing"] = config["warp-routing"]

        self.update_config(new_config)

        # Return tunnel details
        return {
            "tunnel_id": self.tunnel_id,
            "tunnel_name": self.tunnel_name,
            "run_command": f"cloudflared tunnel run --name {self.tunnel_name}",
        }

    def remove_tunnel(self, hostname: str):
        existing_tunnel_id = self._get_tunnel_id_by_hostname(hostname)
        if not existing_tunnel_id or existing_tunnel_id != self.tunnel_id:
            raise ValueError(f"No managed tunnel found for hostname '{hostname}'")

        # Delete the DNS record
        records = self._list_dns_records(domain=hostname, type="CNAME", name=hostname)
        for record in records:
            if record["content"] == f"{self.tunnel_id}.cfargotunnel.com":
                self.cloudflare.delete_record(record=record["id"], domain=hostname)
                break

        # Update tunnel config
        config = self.get_config()
        ingress = config.get("ingress", [])
        new_ingress = [rule for rule in ingress if rule.get("hostname") != hostname]
        new_config = {"ingress": new_ingress}
        if "warp-routing" in config:
            new_config["warp-routing"] = config["warp-routing"]

        self.update_config(new_config)

    def get_tunnels(self):
        """Retrieve a dict of hostname to tunnel_id for all managed tunnels"""
        config = self.get_config()
        ingress = config.get("ingress", [])
        tunnels = {rule["hostname"]: self.tunnel_id for rule in ingress if "hostname" in rule}
        return tunnels

    def _get_tunnel_id_by_hostname(self, hostname: str):
        records = self._list_dns_records(domain=hostname, type="CNAME", name=hostname)
        for record in records:
            if (
                record["name"] == hostname
                and record["type"] == "CNAME"
                and record["content"].endswith(".cfargotunnel.com")
            ):
                return record["content"].split(".")[0]
        return None


if __name__ == "__main__":
    manager = CloudflareTunnelManager("eloAG9YYLzhMpWi99_iLTxsc7Bou7ERP5CJsCZqu")
    manager.add_tunnel("postgres.bhattarai.me")
