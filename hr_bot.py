import os
import json
import logging
from datetime import datetime # Though not used in initial setup, good to have for later
import asyncio # Required for python-telegram-bot v20+ async operations

# Apply Windows event loop policy patch if on Windows
import platform
if platform.system() == "Windows":
    current_policy = asyncio.get_event_loop_policy()
    # Only set the policy if it's not already WindowsSelectorEventLoopPolicy
    # or if we specifically want to ensure it is.
    # For simplicity here, we'll set it if on Windows,
    # assuming it might be Proactor by default.
    # A more nuanced check could be:
    # if not isinstance(current_policy, asyncio.WindowsSelectorEventLoopPolicy):
    # This simple set is often fine for dedicated bot scripts.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
import telegram # Added for telegram.error.BadRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder, # For v20+
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence, # Optional: for simple persistence if needed later
)

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
HR_CHAT_ID = os.getenv('HR_CHAT_ID') # We'll use this to restrict command access

# Define constants
APPLICATION_LOG_FILE = 'submitted_applications.log.json'
UPLOAD_FOLDER = 'uploads/' # Make sure this matches app.py if it's used directly

# Set up basic logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def load_applications() -> list:
    """Loads applications from the JSON log file."""
    if not os.path.exists(APPLICATION_LOG_FILE):
        logger.info(f"{APPLICATION_LOG_FILE} not found. Returning empty list.")
        return []
    try:
        with open(APPLICATION_LOG_FILE, 'r') as f:
            content = f.read()
            if not content:
                logger.info(f"{APPLICATION_LOG_FILE} is empty. Returning empty list.")
                return []
            applications_data = json.loads(content)
            if not isinstance(applications_data, list):
                logger.warning(f"Data in {APPLICATION_LOG_FILE} is not a list. Returning empty list.")
                return []
            return applications_data
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {APPLICATION_LOG_FILE}. Returning empty list.", exc_info=True)
        return []
    except IOError as e:
        logger.error(f"IOError reading {APPLICATION_LOG_FILE}: {e}. Returning empty list.", exc_info=True)
        return []

def save_applications(applications_data: list) -> bool:
    """Saves the list of applications to the JSON log file."""
    try:
        with open(APPLICATION_LOG_FILE, 'w') as f:
            json.dump(applications_data, f, indent=4)
        logger.info(f"Successfully saved {len(applications_data)} applications to {APPLICATION_LOG_FILE}")
        return True
    except IOError as e:
        logger.error(f"IOError writing to {APPLICATION_LOG_FILE}: {e}", exc_info=True)
        return False

def get_application_by_cv_filename(cv_filename: str, applications_data: list = None) -> dict | None:
    """
    Finds an application by its unique CV filename.
    Optionally accepts pre-loaded applications_data to avoid re-reading the file.
    """
    if applications_data is None:
        applications_data = load_applications()

    for app in applications_data:
        if app.get('cv_filename') == cv_filename:
            return app
    logger.warning(f"Application with cv_filename '{cv_filename}' not found.")
    return None

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for MarkdownV2, safer version."""
    if not isinstance(text, str): # Ensure text is a string
        return ""
    # Chars to escape: _ * [ ] ( ) ~ ` > # + - = | { } . !
    # Standard MarkdownV2 escaping involves prefixing the character with a backslash '\'.
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped_text = text
    for char in escape_chars:
        escaped_text = escaped_text.replace(char, '\\' + char)
    return escaped_text

# --- End Helper Functions ---

# --- Command Handlers ---
async def restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the command is from the authorized HR_CHAT_ID."""
    if str(update.effective_chat.id) != HR_CHAT_ID:
        await update.message.reply_text("Sorry, you are not authorized to use this command.")
        logger.warning(f"Unauthorized access attempt by chat_id: {update.effective_chat.id}")
        return False
    return True

async def review_applications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows HR to review new applications, optionally filtered by job title."""
    if not await restricted_access(update, context):
        return

    apps_data = load_applications()
    job_title_filter = None
    if context.args:
        job_title_filter = " ".join(context.args).strip().lower()

    new_applications = []
    for app in apps_data:
        if app.get('status') == 'new':
            if job_title_filter:
                # Ensure app.get('job_title') is treated as string for .lower()
                if str(app.get('job_title', '')).lower() == job_title_filter:
                    new_applications.append(app)
            else:
                new_applications.append(app)

    if not new_applications:
        message = "No new applications to review."
        if job_title_filter:
            message = f"No new applications for job title: '{escape_markdown_v2(job_title_filter)}'."
        await update.message.reply_text(message)
        return

    for app in new_applications:
        full_name_escaped = escape_markdown_v2(app.get('full_name', 'N/A'))
        email_escaped = escape_markdown_v2(app.get('email', 'N/A'))
        job_title_app_escaped = escape_markdown_v2(str(app.get('job_title', 'N/A'))) # str() for safety
        cv_filename_escaped = escape_markdown_v2(app.get('cv_filename', 'N/A'))
        # Assuming timestamp is already in a readable string format and doesn't typically need escaping
        # However, if it could contain special characters, it should be escaped.
        # For now, let's assume it's safe or a simple format like ISO.
        timestamp_str = app.get('timestamp', 'N/A') # Not escaping this for now.

        # The prompt mentions cover_letter_snippet, but it's not in application_log.json by default.
        # The log only contains 'email', 'job_title', 'timestamp', 'full_name', 'phone_number', 'cv_filename', 'status'.
        # So, we cannot directly get 'cover_letter' from `app` unless app.py is modified to log it.
        # For now, we will omit the cover letter snippet from this command's output.

        message_text = (
            f"*New Application*\n\n"
            f"*Full Name:* {full_name_escaped}\n"
            f"*Email:* {email_escaped}\n"
            f"*Job Title:* {job_title_app_escaped}\n"
            f"*CV Filename:* `{cv_filename_escaped}`\n" # Use backticks for filename
            f"*Submitted:* {timestamp_str}\n" # Assuming timestamp_str is safe
        )

        cv_filename = app.get('cv_filename') # Use raw cv_filename for callback data
        if not cv_filename:
            logger.error(f"Application entry is missing 'cv_filename'. Skipping. Entry: {app}")
            continue # Skip if no cv_filename, as it's crucial for callbacks

        keyboard = [
            [
                InlineKeyboardButton("Accept", callback_data=f"accept:{cv_filename}"),
                InlineKeyboardButton("Decline", callback_data=f"decline:{cv_filename}")
            ],
            [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{cv_filename}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
        except telegram.error.BadRequest as e: # More specific error catching
            logger.error(f"Telegram BadRequest sending application message for {cv_filename}: {e}", exc_info=True)
            await update.message.reply_text(f"Error displaying application for {escape_markdown_v2(str(cv_filename))}. Some special characters might be causing issues after escaping. Please check logs.")
        except Exception as e:
            logger.error(f"Generic error sending application message for {cv_filename}: {e}", exc_info=True)
            await update.message.reply_text(f"A general error occurred while displaying application for {escape_markdown_v2(str(cv_filename))}.")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button presses from inline keyboards."""
    query = update.callback_query
    await query.answer() # Acknowledge receipt

    try:
        action, cv_filename = query.data.split(":", 1)
    except ValueError:
        logger.error(f"Error parsing callback_data: {query.data}", exc_info=True)
        await query.edit_message_text(text="Error processing action. Invalid callback data format.", reply_markup=None)
        return

    logger.info(f"Callback received: Action='{action}', CV Filename='{cv_filename}' from user {query.from_user.id}")

    applications = load_applications()
    target_app = get_application_by_cv_filename(cv_filename, applications_data=applications) # Pass loaded data

    if target_app is None:
        logger.warning(f"Application with cv_filename '{cv_filename}' not found during callback.")
        error_message = (
            f"{query.message.text}\n\n"
            f"--- Error: Application details for `{escape_markdown_v2(cv_filename)}` not found or an issue occurred. It might have been removed or the ID is incorrect. ---"
        )
        try:
            await query.edit_message_text(text=error_message, reply_markup=None, parse_mode='MarkdownV2')
        except Exception as e: # Catch potential error during edit_message_text itself
             logger.error(f"Error editing message for 'application not found' callback: {e}", exc_info=True)
        return

    if action == "accept" or action == "decline":
        new_status = "reviewed_accepted" if action == "accept" else "reviewed_declined"
        target_app['status'] = new_status
        target_app['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
        target_app['reviewed_by'] = query.from_user.id # Log who reviewed it

        if save_applications(applications):
            status_text_display = "Accepted" if action == "accept" else "Declined"
            user_name_escaped = escape_markdown_v2(query.from_user.first_name or "Unknown User")

            # Attempt to preserve the original message structure as much as possible
            # Assuming the original message ends before any "--- Status:" line
            original_content_parts = query.message.text.split("\n\n--- Status:")
            base_message_text = original_content_parts[0]

            updated_text = (
                f"{base_message_text}\n\n"
                f"--- Status: *{status_text_display}* by {user_name_escaped} on {escape_markdown_v2(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} UTC ---"
            )
            try:
                await query.edit_message_text(text=updated_text, reply_markup=None, parse_mode='MarkdownV2')
                logger.info(f"Application {cv_filename} status updated to {new_status} by {query.from_user.id}")
            except Exception as e:
                logger.error(f"Error editing message text after status update for {cv_filename}: {e}", exc_info=True)
                # Fallback if edit fails (e.g. message too old, or MarkdownV2 issue)
                await context.bot.send_message(
                    chat_id=query.effective_chat.id,
                    text=f"Application {escape_markdown_v2(cv_filename)} has been {status_text_display}.",
                    reply_to_message_id=query.message.message_id
                )
        else:
            logger.error(f"Failed to save application data after attempting to update status for {cv_filename}.")
            await context.bot.send_message(
                chat_id=query.effective_chat.id,
                text=f"Error: Could not save changes for application {escape_markdown_v2(cv_filename)}. Please try again or check logs.",
                reply_to_message_id=query.message.message_id
            )

    elif action == "get_cv":
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename)
        if os.path.exists(cv_path):
            try:
                await context.bot.send_document(chat_id=query.effective_chat.id, document=open(cv_path, 'rb'))
                # Optionally, edit the original message to note CV was sent, or leave as is.
                # For now, just sending the CV is clear enough. User might want to re-click.
                # Consider if query.message is still valid and if editing it is useful.
                # Example: await query.edit_message_reply_markup(reply_markup=None) # to remove buttons after CV sent
                logger.info(f"Sent CV {cv_filename} to user {query.from_user.id}")
            except Exception as e:
                logger.error(f"Error sending CV {cv_filename}: {e}", exc_info=True)
                await context.bot.send_message(
                    chat_id=query.effective_chat.id,
                    text=f"Sorry, there was an error sending the CV file: `{escape_markdown_v2(cv_filename)}`",
                    parse_mode='MarkdownV2'
                )
        else:
            logger.warning(f"CV file not found at path: {cv_path} for {cv_filename} when requested by user {query.from_user.id}")
            # Edit the original message to indicate CV not found
            original_content_parts = query.message.text.split("\n\n--- CV Status:")
            base_message_text = original_content_parts[0]
            updated_text = (
                f"{base_message_text}\n\n"
                f"--- CV Status: File `{escape_markdown_v2(cv_filename)}` not found on server. ---"
            )
            try:
                await query.edit_message_text(text=updated_text, reply_markup=None, parse_mode='MarkdownV2')
            except Exception as e:
                logger.error(f"Error editing message for 'CV not found' for {cv_filename}: {e}", exc_info=True)


# --- End Command Handlers ---

async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables.")
        return
    if not HR_CHAT_ID:
        logger.error("HR_CHAT_ID not found in environment variables. Bot commands will not be restricted.")
        # Or handle this more strictly if preferred

    # Using ApplicationBuilder for python-telegram-bot v20+
    # You can add persistence here if needed, e.g., PicklePersistence(filepath='./bot_persistence')
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # ---- HANDLERS ----
    application.add_handler(CommandHandler("review_applications", review_applications_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    logger.info("HR Bot starting...")
    await application.run_polling()
    logger.info("HR Bot has stopped.")

if __name__ == '__main__':
    # The platform-specific policy setting (WindowsSelectorEventLoopPolicy)
    # should remain at the TOP of the script if it was added.

    loop = None  # Initialize loop to None
    try:
        loop = asyncio.get_event_loop()
        # If the loop is closed, try to create a new one.
        # This can be necessary in some environments or after previous runs.
        if loop.is_closed():
            logger.info("Default event loop was closed, creating a new one.")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        logger.info("Running main coroutine in the event loop.")
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("HR Bot stopped by user.")
    except RuntimeError as e: # Catch runtime errors specifically
        logger.error(f"RuntimeError in main execution: {e}", exc_info=True)
        if "Cannot close a running event loop" in str(e) or "Event loop is already running" in str(e):
            logger.error("This might indicate an environment-specific asyncio conflict.")
    except Exception as e:
        logger.error(f"HR Bot crashed with an unexpected error: {e}", exc_info=True)
    finally:
        # Optional: clean up the loop if it was explicitly created and is not Proactor.
        # For ProactorEventLoop, close() can also raise "Cannot close a running event loop".
        # Given run_polling is indefinite, explicit closing here is often problematic
        # and best handled by the application's shutdown sequence within run_polling.
        # if loop and not loop.is_running() and not isinstance(loop, asyncio.ProactorEventLoop):
        #    logger.info("Closing event loop.")
        #    loop.close()
        logger.info("HR Bot script execution finished or was interrupted.")
