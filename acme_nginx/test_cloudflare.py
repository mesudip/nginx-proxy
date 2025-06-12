import os
from acme_nginx.Cloudflare import Cloudflare

def run_cloudflare_tests():
    print("--- Cloudflare API Test Script ---")

    # Ensure environment variables are set
    if not os.getenv('CLOUDFLARE_API_TOKEN'):
        print("Error: CLOUDFLARE_API_TOKEN environment variable not set.")
        print("Please set it before running the test.")
        return
    if not os.getenv('CLOUDFLARE_ACCOUNT_ID'):
        print("Warning: CLOUDFLARE_ACCOUNT_ID environment variable not set.")
        print("Some tests (like token verification) might not work without it.")
        # Continue, as other functions like listing zones might still work

    try:
        cf = Cloudflare()

        # 1. Check token
        print("\n1. Checking Cloudflare API Token...")
        if cf.check_token():
            print("Token check successful.")
        else:
            print("Token check failed or account ID not set. Please check your environment variables.")
            # Depending on the failure, we might want to exit here or continue with other tests
            # For now, let's continue to see if other operations work (e.g., if only account_id is missing)

        # 2. List zones and print domains/zones
        print("\n2. Listing Cloudflare Zones...")
        zones = cf._get_zones() # Accessing internal method for testing purposes
        if zones:
            print(f"Found {len(zones)} zones:")
            for zone in zones:
                print(f"  Domain: {zone['name']}, Zone ID: {zone['id']}")
        else:
            print("No zones found or an error occurred while fetching zones.")

        # 3. Try adding and deleting a record
        print("\n3. Attempting to add and delete a test TXT record...")
        # IMPORTANT: Replace with a domain you own and manage in Cloudflare for actual testing
        # and ensure it's a domain that won't cause issues with a temporary TXT record.
        # For a real test, you might need to dynamically determine a domain from your zones.
        test_domain = os.getenv('CLOUDFLARE_TEST_DOMAIN') # e.g., "example.com"
        if not test_domain:
            print("Skipping record add/delete test: CLOUDFLARE_TEST_DOMAIN environment variable not set.")
            print("Please set CLOUDFLARE_TEST_DOMAIN to a domain you manage in Cloudflare to run this test.")
            return

        test_record_name = f"_acme-challenge.test.{test_domain}"
        test_record_data = "test_data_12345"
        record_id = None

        try:
            print(f"  Adding TXT record '{test_record_name}' with data '{test_record_data}' to domain '{test_domain}'...")
            record_id = cf.create_record(test_record_name, test_record_data, test_domain)
            print(f"  Record added successfully. Record ID: {record_id}")

            print(f"  Deleting record ID: {record_id} from domain '{test_domain}'...")
            cf.delete_record(record_id, test_domain)
            print("  Record deleted successfully.")

        except Exception as e:
            print(f"  Error during record add/delete test: {e}")
            if record_id:
                print(f"  Attempting to clean up record {record_id} if it was created...")
                try:
                    cf.delete_record(record_id, test_domain)
                    print("  Cleanup successful.")
                except Exception as cleanup_e:
                    print(f"  Cleanup failed: {cleanup_e}")

    except Exception as e:
        print(f"An unexpected error occurred during Cloudflare tests: {e}")

if __name__ == "__main__":
    run_cloudflare_tests()
