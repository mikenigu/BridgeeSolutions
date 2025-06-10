from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin # Make sure cross_origin is imported
import os
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

@app.route('/api/submit-application', methods=['POST', 'OPTIONS'])
@cross_origin() # Apply a simple cross_origin decorator for testing
async def submit_application(): # Keep it async even if not strictly needed for this simple version
    if request.method == 'OPTIONS':
        print("Simplified endpoint: Received OPTIONS request")
        return jsonify({'message': 'CORS preflight successful (simplified endpoint)'}), 200

    if request.method == 'POST':
        # Just get one field to confirm form data is coming through at all
        job_title = request.form.get('job_title', 'N/A')
        print(f"Simplified endpoint: Received POST request for job: {job_title}. Form data: {request.form}")
        return jsonify({'message': f'Test POST for {job_title} received successfully by simplified endpoint!'}), 200

    # Fallback, though should not be reached if methods are correctly restricted
    return jsonify({'error': 'Method not allowed by simplified endpoint logic'}), 405

if __name__ == '__main__':
    app.run(debug=True, port=5000)
