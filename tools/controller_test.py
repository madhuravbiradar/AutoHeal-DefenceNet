import requests
from getpass import getpass

BASE_URL = "http://localhost:58000/api/v1"

# Enter your controller login details privately
username = input("Controller username: ")
password = getpass("Controller password: ")

try:
    # Request an authentication ticket
    ticket_response = requests.post(
        f"{BASE_URL}/ticket",
        json={
            "username": username,
            "password": password
        },
        timeout=10
    )

    print("Ticket response status:", ticket_response.status_code)

    ticket_data = ticket_response.json()
    ticket = ticket_data.get("response", {}).get("serviceTicket")

    if not ticket:
        print("Could not obtain ticket:")
        print(ticket_data)
    else:
        print("Authentication ticket obtained!")

        # Test an authenticated API request
        response = requests.get(
            f"{BASE_URL}/host",
            headers={"X-Auth-Token": ticket},
            timeout=10
        )

        print("Host API status:", response.status_code)
        print("Host API response:")
        print(response.text[:1000])

except requests.exceptions.RequestException as e:
    print("Connection error:", e)

# Test Packet Tracer topology API
endpoints = [
    "/topology/physical-topology",
    "/network-device"
]

for endpoint in endpoints:
    print("\nTesting:", endpoint)

    response = requests.get(
        BASE_URL + endpoint,
        headers={"X-Auth-Token": ticket},
        timeout=10
    )

    print("Status:", response.status_code)
    print("Response:")
    print(response.text[:2000])
# Check existing discovery jobs
response = requests.get(
    f"{BASE_URL}/discovery",
    headers={"X-Auth-Token": ticket},
    timeout=10
)

print("\nDiscovery API status:", response.status_code)
print("Discovery information:")
print(response.text[:2000])    
    