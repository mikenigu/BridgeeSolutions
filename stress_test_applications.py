import requests
import concurrent.futures
import time
import os
import uuid

# --- Configuration ---
BASE_URL = "http://127.0.0.1:5000"  # Assuming Flask app runs on default port
ENDPOINT = "/api/submit-application"
URL = BASE_URL + ENDPOINT

NUM_CONCURRENT_REQUESTS = 2  # Number of threads to use for concurrent requests
TOTAL_REQUESTS = 100         # Total number of applications to attempt to submit
REQUEST_DELAY_SECONDS = 1 # Optional delay between starting each batch of concurrent requests

DUMMY_CV_FILENAME = "dummy_cv.pdf" # CORRECTED
DUMMY_CV_CONTENT = "This is a dummy CV file for stress testing purposes. It's named .pdf to pass validation." # CORRECTED

# --- Helper Functions ---
def create_dummy_cv():
    """Creates a dummy CV file if it doesn't exist."""
    if not os.path.exists(DUMMY_CV_FILENAME):
        with open(DUMMY_CV_FILENAME, "w") as f:
            f.write(DUMMY_CV_CONTENT)
        print(f"Created dummy CV file: {DUMMY_CV_FILENAME}")

def generate_form_data(request_id):
    """Generates unique form data for each request."""
    unique_email = f"testuser_{request_id}_{uuid.uuid4().hex[:8]}@example.com"
    return {
        "full_name": f"Test User {request_id}",
        "email": unique_email,
        "phone_number": "1234567890",
        "job_title": "Full-Stack Developer", # Or cycle through available jobs
        "cover_letter": f"This is a cover letter for Test User {request_id}."
    }

def submit_application(request_id):
    """Submits a single application."""
    form_payload = generate_form_data(request_id)
    
    try:
        with open(DUMMY_CV_FILENAME, "rb") as cv_file:
            files = {"cv_upload": (DUMMY_CV_FILENAME, cv_file, "application/pdf")} # CORRECTED
            
            start_time = time.time()
            response = requests.post(URL, data=form_payload, files=files, timeout=30) # 30-second timeout
            end_time = time.time()
            
            duration = end_time - start_time
            
            if response.status_code == 200:
                try:
                    response_json = response.json()
                    if response_json.get("success"):
                        return {"status": "success", "id": request_id, "duration": duration, "message": response_json.get("message")}
                    else:
                        return {"status": "failure", "id": request_id, "duration": duration, "status_code": response.status_code, "error": response_json.get("message", "Unknown API error")}
                except requests.exceptions.JSONDecodeError:
                     return {"status": "failure", "id": request_id, "duration": duration, "status_code": response.status_code, "error": "Failed to decode JSON response", "response_text": response.text[:200]}

            else:
                return {"status": "failure", "id": request_id, "duration": duration, "status_code": response.status_code, "error": response.text[:200]} # Log first 200 chars of error

    except requests.exceptions.RequestException as e:
        return {"status": "error", "id": request_id, "duration": time.time() - start_time if 'start_time' in locals() else 0, "error": str(e)}
    except FileNotFoundError:
        return {"status": "error", "id": request_id, "duration": 0, "error": f"Dummy CV file '{DUMMY_CV_FILENAME}' not found."}
    except Exception as e:
        return {"status": "error", "id": request_id, "duration": 0, "error": f"An unexpected error occurred: {str(e)}"}


# --- Main Execution ---
if __name__ == "__main__":
    create_dummy_cv()

    if not os.path.exists(DUMMY_CV_FILENAME):
        print(f"Error: Dummy CV file '{DUMMY_CV_FILENAME}' could not be created. Exiting.")
        exit(1)

    print(f"Starting stress test:")
    print(f"  Target URL: {URL}")
    print(f"  Total requests: {TOTAL_REQUESTS}")
    print(f"  Concurrent requests: {NUM_CONCURRENT_REQUESTS}")
    print(f"  Delay between batches: {REQUEST_DELAY_SECONDS}s\n")

    successful_submissions = 0
    failed_submissions = 0
    error_submissions = 0
    total_duration = 0
    request_details = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_CONCURRENT_REQUESTS) as executor:
        futures = []
        for i in range(TOTAL_REQUESTS):
            futures.append(executor.submit(submit_application, i + 1))
            if (i + 1) % NUM_CONCURRENT_REQUESTS == 0 : # Optional: slight delay after submitting a batch
                 if REQUEST_DELAY_SECONDS > 0:
                    time.sleep(REQUEST_DELAY_SECONDS)
        
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            request_details.append(result)
            total_duration += result.get("duration", 0)

            if result["status"] == "success":
                successful_submissions += 1
                print(f"Request {result['id']}: SUCCESS (Duration: {result['duration']:.2f}s)")
            elif result["status"] == "failure":
                failed_submissions += 1
                print(f"Request {result['id']}: FAILED (Status: {result.get('status_code', 'N/A')}, Duration: {result['duration']:.2f}s, Error: {result.get('error', 'Unknown')})")
            else: # error
                error_submissions += 1
                print(f"Request {result['id']}: ERROR (Duration: {result['duration']:.2f}s, Error: {result.get('error', 'Unknown')})")

    print("\n--- Stress Test Summary ---")
    print(f"Total requests attempted: {TOTAL_REQUESTS}")
    print(f"Successful submissions: {successful_submissions}")
    print(f"Failed submissions (API errors): {failed_submissions}")
    print(f"Errored submissions (client-side/network): {error_submissions}")
    
    if TOTAL_REQUESTS > 0 :
        print(f"Success rate: {(successful_submissions / TOTAL_REQUESTS) * 100:.2f}%")
    if successful_submissions > 0:
        avg_success_duration = sum(r['duration'] for r in request_details if r['status'] == 'success') / successful_submissions
        print(f"Average duration for successful requests: {avg_success_duration:.2f}s")
    
    print(f"Total time spent sending requests (sum of durations): {total_duration:.2f}s")
    
    # Detailed failures
    if failed_submissions > 0 or error_submissions > 0:
        print("\n--- Failure/Error Details ---")
        for result in request_details:
            if result["status"] != "success":
                print(f"  ID: {result['id']}, Status: {result['status']}, Code: {result.get('status_code', 'N/A')}, Error: {result.get('error')}, Response: {result.get('response_text', '')}")

    print("\nStress test finished.")
    print("Remember to check server-side logs for more details on failures.")
    print(f"Check the 'uploads/' directory and 'submitted_applications.log.json' on the server.")

    # Clean up dummy CV
    # if os.path.exists(DUMMY_CV_FILENAME):
    #     os.remove(DUMMY_CV_FILENAME)
    #     print(f"Cleaned up dummy CV file: {DUMMY_CV_FILENAME}")
