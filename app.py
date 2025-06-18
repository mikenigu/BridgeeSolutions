from flask import Flask, request, jsonify, send_from_directory, abort, render_template
from flask_cors import CORS, cross_origin # Make sure cross_origin is imported
import os
import asyncio # Added asyncio
# import uuid # Import uuid module - no longer needed
import json # Import json module
from datetime import datetime # Import datetime
from dotenv import load_dotenv
from werkzeug.utils import secure_filename # Keep for now, might be used by other routes later or full version
import telegram # Keep for now

load_dotenv()

app = Flask(__name__)
CORS(app) # Initialize CORS globally

# --- Configuration ---
# For a real application, use environment variables for sensitive data like tokens and IDs
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN_PLACEHOLDER')
HR_CHAT_ID = os.environ.get('HR_CHAT_ID', 'YOUR_HR_CHAT_ID_PLACEHOLDER') # Can be a user ID or group/channel ID
APPLICATION_LOG_FILE = 'submitted_applications.log.json' # Log file for submitted applications
BLOG_POSTS_FILE = 'blog_posts.json'

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max file size, optional

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for MarkdownV2."""
    if not isinstance(text, str):
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Escape each character with a preceding backslash
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

async def send_telegram_notification(applicant_data, cv_filepath):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

    message_text = f"📢 New Job Application Received\!\n\n"

    # Escape MarkdownV2 characters for user-provided fields
    job_title = escape_markdown_v2(applicant_data.get('job_title', 'N/A'))
    full_name = escape_markdown_v2(applicant_data.get('full_name', 'N/A'))
    email = escape_markdown_v2(applicant_data.get('email', 'N/A'))
    phone_number = escape_markdown_v2(applicant_data.get('phone_number', '')) # Renamed for clarity

    raw_cover_letter = applicant_data.get('cover_letter', '')
    truncated_cover_letter = raw_cover_letter[:200]
    cover_letter_snippet = escape_markdown_v2(truncated_cover_letter)
    if len(raw_cover_letter) > 200:
        cover_letter_snippet += escape_markdown_v2("...")


    message_text += f"**Job Title:** {job_title}\n"
    message_text += f"**Name:** {full_name}\n"
    message_text += f"**Email:** {email}\n"
    if applicant_data.get('phone_number'): # Check if phone exists before adding
        message_text += f"**Phone:** {phone_number}\n" # Use the new variable name

    if applicant_data.get('cover_letter'): # Check if cover_letter exists
        message_text += f"\n**Cover Letter Snippet:**\n{cover_letter_snippet}\n"

    message_text += f"\n\n📄 CV attached\\." # Escape the final period as well

    try:
        # Send the text message (using MarkdownV2 for bolding)
        await bot.send_message(chat_id=HR_CHAT_ID, text=message_text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

        # Send the CV document
        if cv_filepath and os.path.exists(cv_filepath):
            with open(cv_filepath, 'rb') as cv_document:
                await bot.send_document(chat_id=HR_CHAT_ID, document=cv_document, filename=os.path.basename(cv_filepath))
            print(f"Successfully sent application and CV for {applicant_data.get('full_name')} to Telegram.")
            return True
        else:
            # If CV was expected but not found on server (e.g. saving failed but this function was still called)
            await bot.send_message(chat_id=HR_CHAT_ID, text="CV file was expected but not found on server for the preceding application.", parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)
            print(f"CV file not found at path: {cv_filepath} for {applicant_data.get('full_name')}")
            return False # Indicate CV sending failed

    except telegram.error.TelegramError as e:
        print(f"Error sending Telegram notification for {applicant_data.get('full_name')}: {e}")
        # Potentially send a fallback notification to admin or log extensively
        return False
    except Exception as e:
        print(f"An unexpected error occurred in send_telegram_notification: {e}")
        return False

def load_blog_posts():
    if not os.path.exists(BLOG_POSTS_FILE):
        # Using print for now, as app.logger might not be configured for this context
        print(f"INFO: {BLOG_POSTS_FILE} not found. Returning empty list.")
        return []
    try:
        with open(BLOG_POSTS_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                print(f"INFO: {BLOG_POSTS_FILE} is empty. Returning empty list.")
                return []
            posts_data = json.loads(content)
            if not isinstance(posts_data, list):
                print(f"WARNING: Data in {BLOG_POSTS_FILE} is not a list. Returning empty list.")
                return []
            return posts_data
    except json.JSONDecodeError:
        print(f"ERROR: Error decoding JSON from {BLOG_POSTS_FILE}. Returning empty list.")
        return []
    except IOError as e:
        print(f"ERROR: IOError reading {BLOG_POSTS_FILE}: {e}. Returning empty list.")
        return []

# --- Routes ---
@app.route('/')
@cross_origin() # Add if CORS is applied globally, for consistency
def serve_index():
    return send_from_directory('.', 'Index.html')

@app.route('/<path:filename>')
@cross_origin() # For consistency
def serve_static_files(filename):
    # Basic security: prevent access to .py files or other sensitive files
    # This is a simple check; more robust validation might be needed for production.
    if filename.endswith('.py') or filename == '.env' or '.git' in filename:
        return abort(404)
    return send_from_directory('.', filename)

@app.route('/api/blog-posts', methods=['GET'])
@cross_origin()
def get_blog_posts():
    page = request.args.get('page', 1, type=int)
    POSTS_PER_PAGE = 3  # Define how many posts per page

    all_posts = load_blog_posts()
    # Sort posts by date, assuming 'date_published' is in ISO format and can be sorted lexicographically for recent first
    # For more robust sorting, convert to datetime objects if not already
    try:
        # Assuming date_published is like "YYYY-MM-DDTHH:MM:SSZ" or similar sortable string
        all_posts.sort(key=lambda x: x.get('date_published', ''), reverse=True)
    except Exception as e:
        app.logger.error(f"Error sorting blog posts: {e}")
        # Decide if you want to return unsorted or handle error differently

    total_posts = len(all_posts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE

    start_index = (page - 1) * POSTS_PER_PAGE
    end_index = start_index + POSTS_PER_PAGE
    posts_on_page = all_posts[start_index:end_index]

    return jsonify({
        'posts': posts_on_page,
        'current_page': page,
        'total_pages': total_pages,
        'total_posts': total_posts
    }), 200

@app.route('/api/blog-posts/<string:post_id>', methods=['GET'])
@cross_origin()
def get_blog_post(post_id):
    posts = load_blog_posts()
    post = next((p for p in posts if p.get('id') == post_id), None)
    if post:
        return jsonify(post), 200
    else:
        return jsonify({'error': 'Post not found'}), 404

@app.route('/post/<string:post_id>')
@cross_origin()
def view_post(post_id):
    posts = load_blog_posts()
    found_post = next((p for p in posts if p.get('id') == post_id), None)
    if found_post:
        return render_template('post.html', post=found_post)
    else:
        abort(404)

@app.route('/api/submit-application', methods=['POST', 'OPTIONS'])
@cross_origin() # Keep CORS decorator
def submit_application(): # Synchronous route
    if request.method == 'OPTIONS':
        print("Full endpoint: Received OPTIONS request")
        return jsonify({'message': 'CORS preflight successful (full endpoint)'}), 200

    if request.method == 'POST':
        # Extract form data
        form_data = request.form.to_dict()
        full_name = form_data.get('full_name')
        email = form_data.get('email')
        job_title = form_data.get('job_title')

        print(f"Full endpoint: Received POST for job: {job_title}. Form data: {request.form}")

        if not all([full_name, email, job_title]): # Basic check, full_name is not used by log yet but essential for application
            return jsonify({'error': 'Missing required fields (full_name, email, job_title)'}), 400

        # --- Duplicate Application Check ---
        applications_log = []
        if os.path.exists(APPLICATION_LOG_FILE):
            try:
                with open(APPLICATION_LOG_FILE, 'r') as f:
                    content = f.read()
                    if content:
                        applications_log = json.loads(content)
                        if not isinstance(applications_log, list): # Ensure it's a list
                            print(f"Warning: Log file {APPLICATION_LOG_FILE} does not contain a list. Resetting log.")
                            applications_log = []
                    else:
                        applications_log = [] # File is empty
            except json.JSONDecodeError:
                print(f"Warning: Could not decode JSON from {APPLICATION_LOG_FILE}. Starting with an empty log.")
                applications_log = [] # File is malformed
            except IOError as e:
                print(f"Warning: Could not read {APPLICATION_LOG_FILE}: {e}. Starting with an empty log.")
                applications_log = []

        for app_log in applications_log:
            # Ensure keys exist in log entry before accessing
            if app_log.get('email') == email and app_log.get('job_title') == job_title:
                return jsonify({'error': 'You have already applied for this position.'}), 409
        # --- End Duplicate Application Check ---

        cv_file = None
        cv_filepath = None
        filename = None # Initialize filename

        if 'cv_upload' not in request.files:
            return jsonify({'error': 'No CV file part in the request'}), 400

        cv_file = request.files['cv_upload']

        if cv_file.filename == '':
            return jsonify({'error': 'No CV file selected'}), 400

        if cv_file and allowed_file(cv_file.filename):
            original_filename = secure_filename(cv_file.filename)
            # New timestamp-based unique filename:
            timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
            unique_filename = f"{timestamp_ms}-{original_filename}"
            filename = unique_filename

            # Ensure uploads folder exists (Flask often runs from project root in dev)
            upload_folder_path = app.config['UPLOAD_FOLDER']
            if not os.path.exists(upload_folder_path):
                os.makedirs(upload_folder_path)
                print(f"Created upload folder: {upload_folder_path}")

            cv_filepath = os.path.join(upload_folder_path, filename)

            try:
                cv_file.save(cv_filepath)
                print(f"CV saved to {cv_filepath}")
            except Exception as e:
                print(f"Error saving CV: {e}")
                return jsonify({'error': f'Could not save CV file: {str(e)}'}), 500
        else:
            return jsonify({'error': 'Invalid CV file type. Allowed: pdf, doc, docx'}), 400

        # Prepare applicant data dictionary for Telegram function
        applicant_data = {
            'full_name': full_name,
            'email': email,
            'phone_number': form_data.get('phone_number', ''),
            'cover_letter': form_data.get('cover_letter', ''),
            'job_title': job_title
        }

        # --- Log Application ---
        # The applications_log list is already populated from the duplicate check step earlier
        # or initialized as an empty list if the log file didn't exist or was invalid.
        new_application_entry = {
            'email': email,
            'job_title': job_title,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'full_name': full_name,
            'phone_number': form_data.get('phone_number', ''),
            'cv_filename': filename, # This is the unique filename
            'cover_letter': form_data.get('cover_letter', ''), # ADD THIS LINE
            'status': 'new' # New field
        }
        applications_log.append(new_application_entry)

        try:
            with open(APPLICATION_LOG_FILE, 'w') as f:
                json.dump(applications_log, f, indent=4)
            print(f"Successfully logged application for {full_name} to {APPLICATION_LOG_FILE}")
        except IOError as e:
            print(f"Error: Could not write to {APPLICATION_LOG_FILE}: {e}. Application for {full_name} was processed but not logged.")
        # --- End Log Application ---

        # Telegram notification is now handled by the HR bot, so we remove the direct call here.
        # print(f"Preparing to send Telegram notification for {full_name}...")
        # telegram_success = False # Initialize
        # try:
        #     telegram_success = asyncio.run(send_telegram_notification(applicant_data, cv_filepath))
        # except RuntimeError as e:
        #     # This can happen if asyncio.run() is called when an event loop is already running
        #     # (less common with Flask's dev server but good to be aware of for other contexts)
        #     print(f"Asyncio RuntimeError (possibly nested event loops): {e}. Trying to get existing loop.")
        #     try:
        #         loop = asyncio.get_event_loop()
        #         if loop.is_running():
        #             # This is a more complex scenario, often requires contextvars or different async handling
        #             print("Event loop is already running. Telegram notification might not work as expected from sync Flask route without further async management.")
        #             pass
        #         else:
        #              telegram_success = loop.run_until_complete(send_telegram_notification(applicant_data, cv_filepath))
        #     except Exception as async_e:
        #         print(f"Error during advanced asyncio handling for Telegram: {async_e}")

        # The function will now unconditionally return success if all prior steps (CV save, logging) are fine.
        # The logging to submitted_applications.log.json which includes 'status': 'new' happens before this.

        # Optionally, delete the CV from local server if it's only needed for the (now removed) direct Telegram upload.
        # However, the HR bot will need access to this file via "Get CV" button, so we should NOT delete it here.
        # if cv_filepath and os.path.exists(cv_filepath):
        #     try:
        #         os.remove(cv_filepath)
        #         print(f"Removed temporary CV: {cv_filepath}") # This might be premature if HR bot needs it later
        #     except Exception as e:
        #         print(f"Error removing temporary CV {cv_filepath}: {e}")

        return jsonify({
            'message': 'Application received successfully and logged.',
            'filename': filename # The unique CV filename
        }), 200

    else: # Should not be reached if methods are POST, OPTIONS
        return jsonify({'error': 'Method not allowed'}), 405

if __name__ == '__main__':
    app.run(debug=True, port=5000)
