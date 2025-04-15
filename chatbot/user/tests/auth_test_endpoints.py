#!/usr/bin/env python
import requests
import json
import sys
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuration
BASE_URL = "http://localhost:8000/api/v1/auth"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
TIMEOUT = 10  # seconds

def create_session():
    """Create a requests session with retry logic"""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def print_response(response):
    """Print API response nicely formatted."""
    try:
        print(f"Status: {response.status_code}")
        print("Headers:", response.headers)
        print("Response:")
        print(json.dumps(response.json(), indent=2))
    except json.JSONDecodeError:
        print(f"Status: {response.status_code}")
        print("Response: ", response.text)
    print("-" * 50)

def wait_for_condition(condition_func, timeout=10, interval=1):
    """Wait for a condition to be true with timeout"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if condition_func():
            return True
        time.sleep(interval)
    return False

def test_api():
    """Test all authentication API endpoints."""
    session = create_session()
    test_results = {"passed": 0, "failed": 0}
    timestamp = int(time.time())
    test_user = {
        "username": f"testuser_{timestamp}",
        "email": f"test_{timestamp}@example.com",
        "password": "TestPass123!"
    }

    def run_test_step(step_name, func):
        """Helper to run and track test steps"""
        print(f"\n{'='*50}")
        print(f"TEST STEP: {step_name}")
        print(f"{'='*50}")
        try:
            result = func()
            test_results["passed"] += 1
            return result
        except Exception as e:
            test_results["failed"] += 1
            print(f"!! TEST FAILED: {str(e)}")
            return None

    # 1. Test User Registration
    def register_user():
        response = session.post(
            f"{BASE_URL}/register/",
            json=test_user,
            timeout=TIMEOUT
        )
        print_response(response)

        if response.status_code != 201:
            raise Exception(f"Registration failed with status {response.status_code}")

        verification_token = response.json().get('verification_token')
        if not verification_token:
            raise Exception("No verification token received")

        return verification_token

    verification_token = run_test_step("USER REGISTRATION", register_user)
    if not verification_token:
        print("\nAborting tests due to registration failure")
        print_final_results(test_results)
        sys.exit(1)

    # 2. Verify User Exists in System
    def check_user_exists():
        def user_check():
            # Try a login (which should fail with 401 if user exists but password is wrong)
            response = session.post(
                f"{BASE_URL}/login/",
                json={"username": test_user["username"], "password": "wrong_password"},
                timeout=TIMEOUT
            )
            # 401 means user exists but password is wrong (which is good)
            # 404 would mean user doesn't exist
            return response.status_code == 401

        if not wait_for_condition(user_check, timeout=10, interval=1):
            raise Exception("User not found in system after registration")
        return True

    run_test_step("USER EXISTENCE VERIFICATION", check_user_exists)

    # 3. Test Email Verification
    def verify_email():
        response = session.post(
            f"{BASE_URL}/verify-email/",
            json={"token": verification_token},
            timeout=TIMEOUT
        )
        print_response(response)

        if response.status_code != 200:
            raise Exception(f"Email verification failed with status {response.status_code}")
        return True

    run_test_step("EMAIL VERIFICATION", verify_email)

    # 4. Test User Login
    def test_login():
        for attempt in range(MAX_RETRIES):
            response = session.post(
                f"{BASE_URL}/login/",
                json={
                    "username": test_user["username"],
                    "password": test_user["password"]
                },
                timeout=TIMEOUT
            )

            if response.status_code == 200:
                print_response(response)
                return response.json()

            time.sleep(RETRY_DELAY)

        print_response(response)
        raise Exception(f"Login failed after {MAX_RETRIES} attempts")

    tokens = run_test_step("USER LOGIN", test_login)
    if not tokens:
        print("\nAborting tests due to login failure")
        print_final_results(test_results)
        sys.exit(1)

    # 5. Test Authenticated Endpoints
    session.headers.update({"Authorization": f"Bearer {tokens['access']}"})

    def test_profile():
        # Get profile
        response = session.get(f"{BASE_URL}/profile/", timeout=TIMEOUT)
        print_response(response)

        if response.status_code != 200:
            raise Exception(f"Get profile failed with status {response.status_code}")

        # Update profile
        update_data = {"username": f"updated_{test_user['username']}"}
        response = session.patch(
            f"{BASE_URL}/profile/",
            json=update_data,
            timeout=TIMEOUT
        )
        print_response(response)

        if response.status_code != 200:
            raise Exception(f"Update profile failed with status {response.status_code}")

        return True

    run_test_step("PROFILE OPERATIONS", test_profile)

    # 6. Test Token Refresh
    def test_refresh():
        response = session.post(
            f"{BASE_URL}/token/refresh/",
            json={"refresh": tokens["refresh"]},
            timeout=TIMEOUT
        )
        print_response(response)

        if response.status_code != 200:
            raise Exception(f"Token refresh failed with status {response.status_code}")

        # Update session with new access token
        session.headers.update({"Authorization": f"Bearer {response.json()['access']}"})
        return True

    run_test_step("TOKEN REFRESH", test_refresh)

    # 7. Cleanup (optional - implement if you have a delete endpoint)
    # def cleanup():
    #     response = session.delete(f"{BASE_URL}/delete-account/", timeout=TIMEOUT)
    #     print_response(response)
    #     if response.status_code != 204:
    #         raise Exception("Cleanup failed")
    #     return True
    # run_test_step("CLEANUP", cleanup)

    print("\nTEST SUMMARY:")
    print(f"Passed: {test_results['passed']}")
    print(f"Failed: {test_results['failed']}")

    if test_results["failed"] > 0:
        sys.exit(1)

def print_final_results(results):
    print("\nFINAL TEST RESULTS:")
    print(f"Passed: {results['passed']}")
    print(f"Failed: {results['failed']}")

if __name__ == "__main__":
    test_api()