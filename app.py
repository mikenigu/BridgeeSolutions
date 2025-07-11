from flask import Flask, request, jsonify, send_from_directory, abort, render_template, redirect, url_for, flash
from flask_cors import CORS, cross_origin # Make sure cross_origin is imported
import os
import asyncio # Added asyncio
# import uuid # Import uuid module - no longer needed
import json # Import json module
from datetime import datetime # Import datetime
from dotenv import load_dotenv
from werkzeug.utils import secure_filename # Keep for now, might be used by other routes later or full version
from flask_mail import Mail, Message # Import Mail and Message
import telegram # Keep for now
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import uuid # For generating unique post IDs
import mammoth # For .docx conversion

load_dotenv()

app = Flask(__name__)
CORS(app) # Initialize CORS globally
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_default_fallback_secret_key_for_development') # Added for Flask-Login session management

# --- Configuration ---
# Telegram Bot Configuration (already present)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN_PLACEHOLDER') # This seems to be a general token, review if HR bot needs a separate one
HR_CHAT_ID = os.environ.get('HR_CHAT_ID', 'YOUR_HR_CHAT_ID_PLACEHOLDER')

# File Paths
APPLICATION_LOG_FILE = 'submitted_applications.log.json'
BLOG_POSTS_FILE = 'blog_posts.json'

# Uploads Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max file size

# Flask-Mail Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])
# Note: SERVICE_REQUEST_RECIPIENT will be used directly in the mail sending logic, not as a Flask-Mail config.

mail = Mail(app)

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Name of the login route

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# In-memory user store (for simplicity, replace with database in production)
# TODO: Move to a more secure user store if this app goes beyond simple admin use
users_db = {
    "1": User(id="1", username="admin", password_hash=generate_password_hash("adminpass123"))
}

@login_manager.user_loader
def load_user(user_id):
    return users_db.get(user_id)

# --- Helper Functions ---
def save_blog_posts(posts_data: list) -> bool:
    try:
        with open(BLOG_POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts_data, f, indent=4, ensure_ascii=False)
        app.logger.info(f"Successfully saved {len(posts_data)} posts to {BLOG_POSTS_FILE}")
        return True
    except IOError as e:
        app.logger.error(f"IOError writing to {BLOG_POSTS_FILE}: {e}", exc_info=True)
        return False
    except Exception as e: # Catch any other potential errors during save
        app.logger.error(f"Unexpected error saving to {BLOG_POSTS_FILE}: {e}", exc_info=True)
        return False

def generate_unique_post_id() -> str:
    return str(uuid.uuid4())

def get_current_timestamp_iso() -> str:
    return datetime.utcnow().isoformat() + 'Z'

def save_uploaded_image(file_storage) -> str | None:
    if file_storage and file_storage.filename:
        original_filename = secure_filename(file_storage.filename)
        # Get file extension
        extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'jpg' # default to jpg
        unique_filename_stem = uuid.uuid4().hex
        unique_filename = f"{unique_filename_stem}.{extension}"

        upload_folder = os.path.join(app.static_folder, 'uploaded_images') # Use app.static_folder
        os.makedirs(upload_folder, exist_ok=True)
        save_path = os.path.join(upload_folder, unique_filename)

        try:
            file_storage.save(save_path)
            # Return web-accessible path
            return url_for('static', filename=f'uploaded_images/{unique_filename}', _external=False)
        except Exception as e:
            app.logger.error(f"Error saving uploaded image {original_filename} to {save_path}: {e}")
            return None
    return None

def convert_docx_to_html(docx_file_stream) -> str | None:
    """Converts a .docx file stream to HTML."""
    try:
        # Ensure the stream is at the beginning if it's a file-like object that has been read before
        if hasattr(docx_file_stream, 'seek') and callable(docx_file_stream.seek):
            docx_file_stream.seek(0)

        result = mammoth.convert_to_html(docx_file_stream)
        html_content = result.value
        if result.messages:
            app.logger.warning(f"Mammoth conversion messages: {result.messages}")
        return html_content
    except Exception as e:
        app.logger.error(f"Error converting .docx to HTML with mammoth: {e}", exc_info=True)
        return None

def read_text_from_file(file_storage) -> str | None:
    """Reads text content from an uploaded text/markdown file stream."""
    try:
        # Ensure the stream is at the beginning
        if hasattr(file_storage, 'seek') and callable(file_storage.seek):
            file_storage.seek(0)
        return file_storage.read().decode('utf-8')
    except UnicodeDecodeError:
        app.logger.error("Error decoding uploaded text file. Ensure it is UTF-8 encoded.")
        return None # Or raise an error to be caught by the route
    except Exception as e:
        app.logger.error(f"Error reading text from file: {e}", exc_info=True)
        return None


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

    message_text = f"📢 New Job Application Received!\n\n" # Corrected: Removed backslash before !

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
        # Update date format before passing to template
        if 'date_published' in found_post and isinstance(found_post['date_published'], str):
            try:
                # Parse ISO 8601 string, handling the 'Z' for UTC
                dt_object = datetime.fromisoformat(found_post['date_published'].replace('Z', '+00:00'))
                # Format to "Month DD, YYYY"
                found_post['date_published'] = dt_object.strftime('%B %d, %Y')
            except ValueError as e:
                app.logger.error(f"Error parsing date for post {post_id}: {e}")
                # Keep original date string if parsing fails, or set to a default
                # For now, we'll keep the original malformed or unparsable string
                pass # Keep original if parsing fails
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
        try:
            # Extract form data
            form_data = request.form.to_dict()
            full_name = form_data.get('full_name')
            email = form_data.get('email')
            job_title = form_data.get('job_title') # Specific to job application

            print(f"Job Application: Received POST for job: {job_title}. Form data: {request.form}")

            if not all([full_name, email, job_title]): # Basic check for job application
                return jsonify({'success': False, 'message': 'Validation Error: Missing required fields (Full Name, Email, Job Title).'}), 400

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
                    return jsonify({'success': False, 'message': 'It looks like you have already applied for this position with this email.'}), 409
            # --- End Duplicate Application Check ---

            cv_file = None
            cv_filepath = None
            filename = None # Initialize filename

            if 'cv_upload' not in request.files:
                return jsonify({'success': False, 'message': 'Validation Error: No CV file part in the request.'}), 400

            cv_file = request.files['cv_upload']

            if cv_file.filename == '':
                return jsonify({'success': False, 'message': 'Validation Error: No CV file selected.'}), 400

            if cv_file and allowed_file(cv_file.filename):
                original_filename = secure_filename(cv_file.filename)
                timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
                unique_filename = f"{timestamp_ms}-{original_filename}"
                filename = unique_filename

                upload_folder_path = app.config['UPLOAD_FOLDER']
                if not os.path.exists(upload_folder_path):
                    try:
                        os.makedirs(upload_folder_path)
                        app.logger.info(f"Created upload folder: {upload_folder_path}")
                    except Exception as e:
                        app.logger.error(f"Error creating upload folder {upload_folder_path}: {str(e)}")
                        return jsonify({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}), 500

                cv_filepath = os.path.join(upload_folder_path, filename)

                try:
                    cv_file.save(cv_filepath)
                    app.logger.info(f"CV saved to {cv_filepath}")
                except Exception as e:
                    app.logger.error(f"Error saving CV to {cv_filepath}: {str(e)}")
                    return jsonify({'success': False, 'message': 'An unexpected error occurred while saving your CV. Please try again later.'}), 500
            else:
                return jsonify({'success': False, 'message': 'Validation Error: Invalid CV file type. Allowed: pdf, doc, docx.'}), 400

            # Applicant data for logging (Telegram data prep removed/commented out previously)
            # applicant_data = {
            #     'full_name': full_name,
            #     'email': email,
            #     'phone_number': form_data.get('phone_number', ''),
            #     'cover_letter': form_data.get('cover_letter', ''),
            #     'job_title': job_title
            # }

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
            return jsonify({
                'success': True,
                'message': 'Your application has been submitted successfully!', # Standardized message
                'filename': filename # The unique CV filename
            }), 200

        except Exception as e: # Catch any unexpected errors during the process
            app.logger.error(f"An unexpected error occurred in /api/submit-application: {str(e)}")
            # It's good practice to log the full exception for debugging:
            # import traceback
            # app.logger.error(traceback.format_exc())
            return jsonify({'success': False, 'message': 'An unexpected error occurred. Please try again later.'}), 500
    # Removed the 'else' for method not allowed as it's implicitly handled or covered by OPTIONS preflight

@app.route('/api/submit-service-request', methods=['POST'])
@cross_origin() # Apply CORS for this new route as well
def submit_service_request():
    if request.method == 'POST':
        full_name = request.form.get('full_name')
        company_name = request.form.get('company_name') # Optional based on form
        email = request.form.get('email')
        phone = request.form.get('phone_number') # Matches 'phone_number' in service-request.html
        website = request.form.get('website') # Optional
        # country_timezone = request.form.get('country_timezone') # Removed
        service_type = request.form.get('service_type')
        custom_service_description = request.form.get('custom_service_description', '') # Default to empty string if not provided

        app.logger.info(f"Service Request Received: Name: {full_name}, Email: {email}, Service: {service_type}")

        # Basic Validation
        required_fields = {
            'Full Name': full_name,
            'Email Address': email,
            'Phone Number': phone,
            # 'Country and Time Zone': country_timezone, # Removed
            'Service Type': service_type
        }

        missing_fields = [name for name, value in required_fields.items() if not value]

        if service_type == 'Custom/Other' and not custom_service_description.strip():
            missing_fields.append('Custom Service Description (since "Custom/Other" was selected)')

        if missing_fields:
            message = f"Missing required fields: {', '.join(missing_fields)}"
            app.logger.warning(f"Service Request Validation Failed: {message}")
            return jsonify({'success': False, 'message': message}), 400

        # Email Sending Logic
        recipient_email = os.getenv('SERVICE_REQUEST_RECIPIENT')
        if not recipient_email:
            app.logger.error("SERVICE_REQUEST_RECIPIENT environment variable is not set. Cannot send service request email.")
            return jsonify({'success': False, 'message': 'Server configuration error. Could not process request.'}), 500

        subject = f"New Service Request from {company_name if company_name else full_name} - {service_type}"

        email_body_parts = [
            "You have received a new service request:",
            "",
            "Contact Information:",
            f"Full Name: {full_name}",
            f"Company Name: {company_name if company_name else 'N/A'}",
            f"Email Address: {email}",
            f"Phone Number: {phone}",
            f"Website: {website if website else 'N/A'}",
            # f"Country and Time Zone: {country_timezone}", # Removed
            "",
            "Service Needed:",
            f"Type: {service_type}"
        ]
        if service_type == "Custom/Other" and custom_service_description.strip(): # Ensure description is not just whitespace
            email_body_parts.append(f"Description: {custom_service_description}")

        email_body = "\n".join(email_body_parts)

        # Sender will be app.config['MAIL_DEFAULT_SENDER']
        msg = Message(subject, recipients=[recipient_email], body=email_body)

        try:
            mail.send(msg)
            app.logger.info(f"Service request email sent successfully to {recipient_email} from {email} for service {service_type}")
            return jsonify({'success': True, 'message': 'Your service request has been sent successfully!'})
        except Exception as e:
            app.logger.error(f"Failed to send service request email from {email} to {recipient_email}. Error: {str(e)}")
            # For more detailed debugging in a real scenario, you might log the full exception:
            # app.logger.exception("Exception occurred while sending service request email:")
            return jsonify({'success': False, 'message': 'There was an error processing your request. Please try again later.'}), 500

    # Should not be reached if methods only include POST and preflight handles OPTIONS
    return jsonify({'error': 'Method not allowed for /api/submit-service-request'}), 405

@app.route('/submit_contact_form', methods=['POST'])
@cross_origin()
def submit_contact_form():
    if request.method == 'POST':
        full_name = request.form.get('name') # Matches 'name' in contact.html
        email = request.form.get('email')
        subject = request.form.get('subject', 'No Subject Provided') # Default if not provided
        message_body = request.form.get('message')

        app.logger.info(f"Contact Form Submission: Name: {full_name}, Email: {email}, Subject: {subject}")

        # Basic Validation
        required_fields = {
            'Full Name': full_name,
            'Email Address': email,
            'Message': message_body
        }
        missing_fields = [name for name, value in required_fields.items() if not value or not value.strip()]

        if missing_fields:
            message = f"Missing required fields: {', '.join(missing_fields)}"
            app.logger.warning(f"Contact Form Validation Failed: {message}")
            # For a traditional form post, you'd redirect with an error.
            # For now, returning JSON. If contact.html is updated for AJAX, this is fine.
            # Otherwise, a user-friendly error page or redirect would be better.
            return jsonify({'success': False, 'message': message}), 400

        # Email Sending Logic
        recipient_email = os.getenv('CONTACT_FORM_RECIPIENT') # New environment variable
        if not recipient_email:
            app.logger.error("CONTACT_FORM_RECIPIENT environment variable is not set. Cannot send contact form email.")
            return jsonify({'success': False, 'message': 'Server configuration error. Could not process request.'}), 500

        email_subject = f"Contact Form: {subject} - from {full_name}"

        email_body_content = [
            f"You have received a new message from your website contact form:",
            "",
            f"Name: {full_name}",
            f"Email: {email}",
            f"Subject: {subject}",
            "",
            "Message:",
            message_body
        ]
        email_text = "\n".join(email_body_content)

        # Sender will be app.config['MAIL_DEFAULT_SENDER']
        # Reply-To header can be set to the user's email for easier replies
        msg = Message(email_subject, recipients=[recipient_email], body=email_text, reply_to=email)

        try:
            mail.send(msg)
            app.logger.info(f"Contact form email sent successfully to {recipient_email} from {email}")
            # If not using AJAX on contact.html, redirect to a thank you page or back with a success message.
            # For now, returning JSON.
            # A simple HTML response could also be: return "Thank you for your message!", 200
            return jsonify({'success': True, 'message': 'Your message has been sent successfully!'}), 200
        except Exception as e:
            app.logger.error(f"Failed to send contact form email from {email} to {recipient_email}. Error: {str(e)}")
            return jsonify({'success': False, 'message': 'There was an error sending your message. Please try again later.'}), 500

# --- Login/Logout Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_test')) # Or an admin dashboard later
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember_me') else False

        # Find user by username (simple iteration for in-memory store)
        user_obj = None
        for user_in_db in users_db.values():
            if user_in_db.username == username:
                user_obj = user_in_db
                break

        if user_obj and user_obj.check_password(password):
            login_user(user_obj, remember=remember)
            flash('Logged in successfully!', 'success')
            # Redirect to the page the user was trying to access, or a default
            next_page = request.args.get('next')
            if not next_page or url_for(next_page.lstrip('/')) == url_for('login'): # Basic security check for next_page
                 next_page = url_for('admin_test') # Default to admin_test for now
            return redirect(next_page)
        else:
            flash('Invalid username or password. Please try again.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('serve_index'))

# --- Protected Test Route ---
@app.route('/admin_test')
@login_required
def admin_test():
    return f"Hello, {current_user.username}! This is a protected admin page. Your ID is {current_user.id}"

# --- Admin Panel Routes ---
@app.route('/admin/blog')
@login_required
def admin_blog_list():
    posts = load_blog_posts()
    # Sort posts by date, newest first. Assuming 'date_published' is in ISO format.
    # More robust sorting might be needed if date formats vary or are not always present.
    try:
        posts.sort(key=lambda x: x.get('date_published', ''), reverse=True)
    except Exception as e:
        app.logger.error(f"Error sorting blog posts for admin panel: {e}")
        # Continue with unsorted posts if sorting fails
    return render_template('admin_blog_list.html', posts=posts, title="Blog Posts", now=datetime.utcnow()) # Changed title for clarity

@app.route('/admin/blog/create', methods=['GET', 'POST'])
@login_required
def admin_create_blog_post():
    if request.method == 'POST':
        title = request.form.get('title')
        author = request.form.get('author', current_user.username) # Default to current user
        content_text = request.form.get('content_text')
        content_file = request.files.get('content_file')
        content_is_html = 'content_is_html' in request.form

        image_url_form = request.form.get('image_url')
        image_upload_file = request.files.get('image_upload')

        final_content = ""

        # --- Content Processing ---
        if content_file and content_file.filename:
            filename = secure_filename(content_file.filename)
            if filename.lower().endswith('.docx'):
                html_from_docx = convert_docx_to_html(content_file.stream)
                if html_from_docx:
                    final_content = html_from_docx
                    content_is_html = True # Override if docx is successfully processed
                else:
                    flash('Error converting .docx file. Please check the file or provide content manually.', 'error')
                    return render_template('admin_blog_form.html', title="Create New Blog Post", post=request.form, now=datetime.utcnow())
            elif filename.lower().endswith(('.txt', '.md')):
                text_from_file = read_text_from_file(content_file.stream)
                if text_from_file:
                    final_content = text_from_file
                    # content_is_html might remain as user set it for .md if they want to parse it later
                else:
                    flash('Error reading content from text/markdown file.', 'error')
                    return render_template('admin_blog_form.html', title="Create New Blog Post", post=request.form, now=datetime.utcnow())
            else:
                flash('Unsupported content file type. Please use .txt, .md, or .docx.', 'error')
                return render_template('admin_blog_form.html', title="Create New Blog Post", post=request.form, now=datetime.utcnow())
        elif content_text:
            final_content = content_text
        else:
            flash('Content is required (either typed or from a file).', 'error')
            return render_template('admin_blog_form.html', title="Create New Blog Post", post=request.form, now=datetime.utcnow())

        if not title:
            flash('Title is required.', 'error')
            # Pass back current form data to re-populate
            return render_template('admin_blog_form.html', title="Create New Blog Post", post=request.form, content=final_content, content_is_html=content_is_html, now=datetime.utcnow())

        # --- Image Processing ---
        final_image_url = None
        image_url_is_static = False # Flag to differentiate between external URLs and uploaded static files

        if image_upload_file and image_upload_file.filename:
            # Prioritize uploaded image
            saved_image_path = save_uploaded_image(image_upload_file)
            if saved_image_path:
                final_image_url = saved_image_path
                image_url_is_static = True # Mark that this is a path to a static file
            else:
                flash('Error saving uploaded image. Please try again or use a URL.', 'error')
                # Continue without image or return error? For now, continue.
        elif image_url_form:
            final_image_url = image_url_form
            image_url_is_static = False # It's an external URL

        # --- Create and Save Post ---
        new_post_id = generate_unique_post_id()
        new_post = {
            "id": new_post_id,
            "title": title,
            "author": author if author else None, # Store None if author was skipped/empty
            "content": final_content,
            "content_is_html": content_is_html,
            "date_published": get_current_timestamp_iso(),
            "image_url": final_image_url,
            "image_url_is_static": image_url_is_static # Store this new flag
        }

        posts = load_blog_posts()
        posts.append(new_post)
        if save_blog_posts(posts):
            flash(f"Blog post '{title}' created successfully!", 'success')
            return redirect(url_for('admin_blog_list'))
        else:
            flash('Error saving blog post to file. Please check server logs.', 'error')
            # Re-render form with data if save fails
            return render_template('admin_blog_form.html', title="Create New Blog Post", post=new_post, now=datetime.utcnow())

    # GET request
    return render_template('admin_blog_form.html', title="Create New Blog Post", post=None, now=datetime.utcnow())

@app.route('/admin/blog/edit/<string:post_id>', methods=['GET', 'POST'])
@login_required
def admin_edit_blog_post(post_id):
    posts = load_blog_posts()
    post_to_edit = None
    post_index = -1

    for i, p in enumerate(posts):
        if p.get('id') == post_id:
            post_to_edit = p
            post_index = i
            break

    if not post_to_edit:
        flash(f"Blog post with ID {post_id} not found.", 'error')
        return redirect(url_for('admin_blog_list'))

    if request.method == 'POST':
        title = request.form.get('title')
        author = request.form.get('author', current_user.username)
        content_text = request.form.get('content_text')
        content_file = request.files.get('content_file')
        content_is_html_form = 'content_is_html' in request.form # From checkbox

        image_url_form = request.form.get('image_url')
        image_upload_file = request.files.get('image_upload')

        # --- Content Processing ---
        new_content = post_to_edit.get('content') # Default to existing content
        new_content_is_html = post_to_edit.get('content_is_html', False)

        if content_file and content_file.filename:
            filename = secure_filename(content_file.filename)
            if filename.lower().endswith('.docx'):
                html_from_docx = convert_docx_to_html(content_file.stream)
                if html_from_docx is not None:
                    new_content = html_from_docx
                    new_content_is_html = True
                else:
                    flash('Error converting .docx file. Content not updated.', 'error')
                    # Return here or let it proceed with old content if desired. For now, let's return.
                    return render_template('admin_blog_form.html', title=f"Edit Post: {post_to_edit.get('title')}", post=post_to_edit, now=datetime.utcnow())
            elif filename.lower().endswith(('.txt', '.md')):
                text_from_file = read_text_from_file(content_file.stream)
                if text_from_file is not None:
                    new_content = text_from_file
                    new_content_is_html = content_is_html_form # Respect checkbox for txt/md
                else:
                    flash('Error reading content from text/markdown file. Content not updated.', 'error')
                    return render_template('admin_blog_form.html', title=f"Edit Post: {post_to_edit.get('title')}", post=post_to_edit, now=datetime.utcnow())
            # No error for unsupported type, just means content_file won't be used if not .docx, .txt, .md
        elif content_text: # Only use content_text if no (valid) file was uploaded
             new_content = content_text
             new_content_is_html = content_is_html_form


        if not title:
            flash('Title is required.', 'error')
            # Update post_to_edit with current form values for re-rendering
            current_form_data = request.form.to_dict()
            current_form_data['content'] = new_content # Use processed content
            current_form_data['content_is_html'] = new_content_is_html
            return render_template('admin_blog_form.html', title=f"Edit Post: {post_to_edit.get('title')}", post=current_form_data, now=datetime.utcnow())

        # --- Image Processing ---
        updated_image_url = post_to_edit.get('image_url')
        updated_image_is_static = post_to_edit.get('image_url_is_static', False)
        old_static_image_path = None

        if updated_image_is_static and updated_image_url:
            old_static_image_filename = updated_image_url.split('/')[-1]
            old_static_image_path = os.path.join(app.static_folder, 'uploaded_images', old_static_image_filename)

        if image_upload_file and image_upload_file.filename:  # New image uploaded
            saved_image_path = save_uploaded_image(image_upload_file)
            if saved_image_path:
                if old_static_image_path and os.path.exists(old_static_image_path):
                    # Check if it's truly a different file before deleting
                    new_filename_for_comparison = saved_image_path.split('/')[-1]
                    if old_static_image_filename != new_filename_for_comparison:
                        try:
                            os.remove(old_static_image_path)
                            app.logger.info(f"Deleted old static image: {old_static_image_path} (replaced by new upload)")
                        except Exception as e:
                            app.logger.error(f"Error deleting old static image {old_static_image_path}: {e}")
                updated_image_url = saved_image_path
                updated_image_is_static = True
            else:
                flash('Error saving uploaded image. Image was not updated.', 'warning')
        else:  # No new image uploaded, evaluate form_url
            form_url = request.form.get('image_url', '').strip()
            original_url_in_post = post_to_edit.get('image_url')
            original_is_static_in_post = post_to_edit.get('image_url_is_static', False)

            if form_url: # User typed something into the URL field
                if form_url != original_url_in_post or original_is_static_in_post: # It's a new URL, or was static and now is URL
                    if original_is_static_in_post and old_static_image_path and os.path.exists(old_static_image_path):
                        try:
                            os.remove(old_static_image_path)
                            app.logger.info(f"Deleted old static image: {old_static_image_path} (replaced by new URL)")
                        except Exception as e:
                            app.logger.error(f"Error deleting old static image {old_static_image_path}: {e}")
                    updated_image_url = form_url
                    updated_image_is_static = False
                # else: form_url is same as original_url_in_post and it was not static - no change
            elif not form_url and original_url_in_post and not original_is_static_in_post:
                # URL field was explicitly cleared for an existing external URL
                updated_image_url = None
                updated_image_is_static = False
            # If form_url is empty AND original was static, updated_image_url & updated_image_is_static retain their initial values (original static image is kept).
            # If form_url is empty AND original was also empty/None, no change.

        # --- Update Post ---
        posts[post_index]['title'] = title
        posts[post_index]['author'] = author if author else None
        posts[post_index]['content'] = new_content
        posts[post_index]['content_is_html'] = new_content_is_html
        posts[post_index]['image_url'] = updated_image_url # Use the processed updated_image_url
        posts[post_index]['image_url_is_static'] = updated_image_is_static # Use the processed updated_image_is_static
        # date_published is not changed on edit, but could add a 'last_modified' field

        if save_blog_posts(posts):
            flash(f"Blog post '{title}' updated successfully!", 'success')
            return redirect(url_for('admin_blog_list'))
        else:
            flash('Error saving updated blog post. Please check server logs.', 'error')
            # Re-render form with current (attempted) data
            # Construct a dictionary representing the current state for re-rendering
            current_form_state = {
                'id': post_id, # Keep the ID
                'title': title,
                'author': author,
                'content': new_content,
                'content_is_html': new_content_is_html,
                'image_url': updated_image_url,
                'image_url_is_static': updated_image_is_static,
                'date_published': post_to_edit.get('date_published') # Keep original publish date
            }
            return render_template('admin_blog_form.html', title=f"Edit Post: {title}", post=current_form_state, now=datetime.utcnow())

    # GET request
    return render_template('admin_blog_form.html', title=f"Edit Post: {post_to_edit.get('title')}", post=post_to_edit, now=datetime.utcnow())

# --- Jinja Filters ---
def format_datetime_admin_filter(value, format='%B %d, %Y %H:%M %Z'):
    """Formats an ISO datetime string (with or without Z) for display."""
    if not value:
        return "N/A"
    try:
        if isinstance(value, str):
            if value.endswith('Z'):
                dt_obj = datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                dt_obj = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            dt_obj = value
        else:
            return str(value) # Fallback for unexpected types
        return dt_obj.strftime(format)
    except ValueError:
        return str(value) # Return original if parsing fails

app.jinja_env.filters['format_datetime_admin'] = format_datetime_admin_filter


if __name__ == '__main__':
    app.run(debug=True, port=5000)
