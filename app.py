from flask import Flask, request, jsonify
import os
from werkzeug.utils import secure_filename
import telegram # Import the python-telegram-bot library

app = Flask(__name__)

# --- Configuration ---
# For a real application, use environment variables for sensitive data like tokens and IDs
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN_PLACEHOLDER')
HR_CHAT_ID = os.environ.get('HR_CHAT_ID', 'YOUR_HR_CHAT_ID_PLACEHOLDER') # Can be a user ID or group/channel ID

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max file size, optional

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

async def send_telegram_notification(applicant_data, cv_filepath):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

    message_text = f"📢 New Job Application Received!\n\n"
    # Escape MarkdownV2 characters for user-provided fields
    job_title = applicant_data.get('job_title', 'N/A').replace('.', '\\.') # Basic example, more comprehensive escaping might be needed
    full_name = applicant_data.get('full_name', 'N/A').replace('.', '\\.')
    email = applicant_data.get('email', 'N/A').replace('.', '\\.')
    phone = applicant_data.get('phone_number', '').replace('.', '\\.')
    cover_letter_snippet = applicant_data.get('cover_letter', '')[:200].replace('.', '\\.') + "..."


    message_text += f"**Job Title:** {job_title}\n"
    message_text += f"**Name:** {full_name}\n"
    message_text += f"**Email:** {email}\n"
    if applicant_data.get('phone_number'): # Check if phone exists before adding
        message_text += f"**Phone:** {phone}\n"

    if applicant_data.get('cover_letter'): # Check if cover_letter exists
        message_text += f"\n**Cover Letter Snippet:**\n{cover_letter_snippet}\n"

    message_text += f"\n\n📄 CV attached."

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

# --- Routes ---
@app.route('/')
def hello_world():
    return 'Hello, Bridgee Solutions Backend!'

@app.route('/api/submit-application', methods=['POST'])
async def submit_application(): # Make route async
    if request.method == 'POST':
        # Extract form data
        form_data = request.form.to_dict() # Get all form fields as a dictionary
        full_name = form_data.get('full_name')
        email = form_data.get('email')
        job_title = form_data.get('job_title')

        if not all([full_name, email, job_title]):
            return jsonify({'error': 'Missing required fields (name, email, job_title)'}), 400

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
            # Create a more unique filename to avoid overwrites and add original submitter info
            # Example: filename = f"{secure_filename(email)}_{int(time.time())}_{original_filename}"
            # For now, keeping it simpler for the subtask, but uniqueness is important.
            filename = original_filename # In a real app, ensure this is unique.

            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])

            cv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            try:
                cv_file.save(cv_filepath)
            except Exception as e:
                print(f"Error saving CV: {e}")
                return jsonify({'error': f'Could not save CV file: {e}'}), 500
        else:
            # This error is now for if allowed_file returns false
            return jsonify({'error': 'Invalid CV file type. Allowed: pdf, doc, docx'}), 400

        # Prepare applicant data dictionary for Telegram function
        applicant_data = {
            'full_name': full_name,
            'email': email,
            'phone_number': form_data.get('phone_number', ''),
            'cover_letter': form_data.get('cover_letter', ''),
            'job_title': job_title
        }

        # Send notification
        telegram_success = await send_telegram_notification(applicant_data, cv_filepath)

        if telegram_success:
            # Optionally, delete the CV from local server after sending if it's stored elsewhere
            # or if Telegram is the primary archive. For now, we'll keep it.
            # if cv_filepath and os.path.exists(cv_filepath):
            #     os.remove(cv_filepath)
            #     print(f"Removed temporary CV: {cv_filepath}")

            return jsonify({
                'message': 'Application received successfully and notification sent!',
                'filename': filename # Use the potentially modified filename
            }), 200
        else:
            # If Telegram failed, it's still a successful application upload,
            # but HR needs to be notified through a fallback or logs checked.
            return jsonify({
                'message': 'Application received, but failed to send Telegram notification. Please contact admin.',
                'filename': filename # Use the potentially modified filename
            }), 500 # Internal server error because notification failed
    else:
        return jsonify({'error': 'Method not allowed'}), 405

if __name__ == '__main__':
    app.run(debug=True, port=5000)
