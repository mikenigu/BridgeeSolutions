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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ApplicationBuilder, # For v20+
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence, # Optional: for simple persistence if needed later
    MessageHandler, # Added MessageHandler
    filters # Added filters
)

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
HR_CHAT_ID = os.getenv('HR_CHAT_ID') # We'll use this to restrict command access

# Define constants
APPLICATION_LOG_FILE = 'submitted_applications.log.json'
UPLOAD_FOLDER = 'uploads/' # Make sure this matches app.py if it's used directly
APPS_PER_PAGE = 3 # Number of applications to show per page in review mode

# Set up basic logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Keyboards ---
main_menu_keyboard_layout = [
    ["Review New Applications"],
    ["Help"]
]
main_menu_keyboard = ReplyKeyboardMarkup(
    main_menu_keyboard_layout, resize_keyboard=True, one_time_keyboard=False
)

review_mode_keyboard_layout = [
    ["Next Page", "Previous Page"],
    ["Back to Main Menu"]
]
review_mode_keyboard = ReplyKeyboardMarkup(
    review_mode_keyboard_layout, resize_keyboard=True, one_time_keyboard=False
)
# --- End Keyboards ---

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

def escape_markdown_v2(text: str) -> str: # Type hint is str, but we handle non-str
    """Escapes special characters for MarkdownV2, safer version."""
    # Ensure text is a string before processing
    if not isinstance(text, str):
        text = str(text) # Convert to string if not already

    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Standard MarkdownV2 escaping involves prefixing the character with a backslash '\'.
    escaped_text = text
    for char in escape_chars:
        escaped_text = escaped_text.replace(char, '\\' + char)
    return escaped_text

# --- End Helper Functions ---

# --- Command Handlers ---
async def restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the command is from the authorized HR_CHAT_ID."""
    # This version of restricted_access assumes update.message could be None
    # and relies on update.effective_chat.id for the check.
    # It attempts to reply via update.message if available, otherwise logs.
    chat_id_str = str(update.effective_chat.id)
    if chat_id_str != HR_CHAT_ID:
        unauthorized_message = "Sorry, you are not authorized to use this command."
        if update.message:
            await update.message.reply_text(unauthorized_message)
        else:
            # If no direct message to reply to, consider sending a new message to the chat.
            # await context.bot.send_message(chat_id=update.effective_chat.id, text=unauthorized_message)
            # For now, just log if no direct reply target.
            pass # Log is done by the caller in some cases, or add specific log here.
        logger.warning(f"Unauthorized access attempt by chat_id: {chat_id_str}")
        return False
    return True

async def start_review_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates a new review session for 'new' applications."""
    # Note: restricted_access should be called by the initiating command/message handler.
    # This function focuses on the logic of starting the session.

    logger.info(f"Attempting to start review session for chat_id: {update.effective_chat.id}")
    all_applications = load_applications()

    job_title_filter = None
    if context.args: # context.args is for CommandHandler, will be empty for MessageHandler
        job_title_filter = " ".join(context.args).strip().lower()
        if job_title_filter: # Only log if filter is not empty
            logger.info(f"Filtering review session for job title: '{escape_markdown_v2(job_title_filter)}'")

    new_applications_to_review = []
    for app in all_applications:
        if app.get('status') == 'new':
            if job_title_filter:
                if str(app.get('job_title', '')).lower() == job_title_filter:
                    new_applications_to_review.append(app)
            else:
                new_applications_to_review.append(app)

    reply_target = update.effective_message
    if not reply_target and update.callback_query : # Should not happen if called from command/message
        reply_target = update.callback_query.message

    if not reply_target :
        logger.error(f"start_review_session: No effective_message to reply to for chat_id {update.effective_chat.id}.")
        # Potentially send a new message to the chat_id if absolutely necessary
        # await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not initiate review session: no message context.")
        return

    if not new_applications_to_review:
        message_text = "No new applications to review."
        if job_title_filter:
            message_text = f"No new applications to review for job title: '{escape_markdown_v2(job_title_filter)}'."
        await reply_target.reply_text(message_text, reply_markup=main_menu_keyboard)
        context.user_data.pop('review_list', None)
        context.user_data.pop('review_page_num', None)
        return

    context.user_data['review_list'] = new_applications_to_review
    context.user_data['review_page_num'] = 0

    logger.info(f"Found {len(new_applications_to_review)} new applications. Starting review session for chat_id {update.effective_chat.id}.")

    await reply_target.reply_text(
        f"Starting review of {len(new_applications_to_review)} application(s). Use navigation buttons below.",
        reply_markup=review_mode_keyboard
    )

    await display_application_page(update, context) # display_application_page will be defined next

async def display_application_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Attempting to display an application page.")
    review_list = context.user_data.get('review_list', [])
    page_num = context.user_data.get('review_page_num', 0)

    # Determine the correct chat_id and reply_target
    chat_id = update.effective_chat.id
    reply_target = update.effective_message
    if not reply_target and update.callback_query: # Check if called from callback
        reply_target = update.callback_query.message

    if not review_list:
        logger.info(f"display_application_page called with no review_list for chat_id {chat_id}.")
        # Try to send to reply_target if available, else to chat_id directly
        msg_content = "No applications to display in the current review session."
        if reply_target:
            await reply_target.reply_text(msg_content, reply_markup=main_menu_keyboard)
        else: # Fallback, send a new message to the chat
            await context.bot.send_message(chat_id=chat_id, text=msg_content, reply_markup=main_menu_keyboard)
        return

    start_index = page_num * APPS_PER_PAGE
    end_index = start_index + APPS_PER_PAGE
    apps_on_page = review_list[start_index:end_index]

    if not apps_on_page:
        logger.info(f"No applications found for page {page_num} in chat {chat_id}.")
        no_apps_message = "No more applications to display."
        if page_num > 0 :
            no_apps_message = "You've reached the end of the application list."

        if reply_target:
            await reply_target.reply_text(no_apps_message, reply_markup=review_mode_keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text=no_apps_message, reply_markup=review_mode_keyboard)
        return

    total_apps = len(review_list)
    total_pages = (total_apps + APPS_PER_PAGE - 1) // APPS_PER_PAGE

    page_summary_text = f"Displaying page {page_num + 1} of {total_pages} ({APPS_PER_PAGE} per page)\. Applications {start_index + 1}-{min(end_index, total_apps)} of {total_apps} total 'new' applications\."
    await context.bot.send_message(
        chat_id=chat_id,
        text=page_summary_text,
        parse_mode='MarkdownV2'
    )

    for app_data in apps_on_page:
        cv_filename = app_data.get('cv_filename', 'N/A')

        escaped_full_name = escape_markdown_v2(app_data.get('full_name', 'N/A'))
        escaped_email = escape_markdown_v2(app_data.get('email', 'N/A'))
        escaped_job_title = escape_markdown_v2(str(app_data.get('job_title', 'N/A')))
        escaped_cv_filename_display = escape_markdown_v2(cv_filename)
        submission_timestamp = escape_markdown_v2(str(app_data.get('timestamp', 'N/A')))

        message_text = (
            f"*New Application*\n\n"
            f"*Name:* {escaped_full_name}\n"
            f"*Email:* {escaped_email}\n"
            f"*Job Title:* {escaped_job_title}\n"
            f"*CV Filename:* {escaped_cv_filename_display}\n" # Markdown fix: No backticks here
            f"*Submitted:* {submission_timestamp}\n"
        )

        # Using distinct callback prefixes for actions originating from paged review
        # vs. actions from initial single notifications (if those were kept).
        keyboard = [
            [
                InlineKeyboardButton("Accept", callback_data=f"review_accept:{cv_filename}"),
                InlineKeyboardButton("Reject", callback_data=f"review_reject:{cv_filename}")
            ],
            [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{cv_filename}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )
        except telegram.error.BadRequest as e:
            logger.error(f"Error sending application details for {cv_filename} due to BadRequest: {e}. Text was: {message_text}", exc_info=True)
            error_display_text = f"Error displaying application for {escape_markdown_v2(cv_filename)}\. Some special characters might still be causing issues\. Please check logs\. Raw details: Name: {escape_markdown_v2(app_data.get('full_name', 'N/A'))}, CV: {escape_markdown_v2(cv_filename)}"
            await context.bot.send_message(chat_id=chat_id, text=error_display_text, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Unexpected error sending application details for {cv_filename}: {e}. Text was: {message_text}", exc_info=True)
            error_display_text_unexpected = f"An unexpected error occurred while trying to display application: {escape_markdown_v2(cv_filename)}\."
            await context.bot.send_message(chat_id=chat_id, text=error_display_text_unexpected, parse_mode='MarkdownV2')

    logger.info(f"Displayed {len(apps_on_page)} applications on page {page_num + 1} for chat_id {chat_id}.")

async def review_applications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to start reviewing applications. Wrapper for start_review_session."""
    if not await restricted_access(update, context):
        return
    await start_review_session(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    if not await restricted_access(update, context):
        return

    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)

    await update.message.reply_text(
        "Welcome to the HR Bot! Please use the menu below or type commands.",
        reply_markup=main_menu_keyboard
    )
    logger.info(f"Sent /start menu to {update.effective_chat.id}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /stop command, removes keyboard and clears review state."""
    if not await restricted_access(update, context):
        return

    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)

    await update.message.reply_text(
        "Custom keyboard removed. Send /start to show it again.",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"Custom keyboard removed, review state cleared for chat_id: {update.effective_chat.id}")

# button_callback_handler should remain unchanged by this diff
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Acknowledge callback

    try:
        action_type, cv_filename = query.data.split(":", 1)
    except ValueError:
        logger.error(f"Invalid callback_data format: {query.data}")
        # Try to edit the message if possible, otherwise log and move on
        if query.message:
            await query.edit_message_text("Error processing action. Invalid data format.", reply_markup=None)
        return

    logger.info(f"Callback received: action_type='{action_type}', cv_filename='{cv_filename}' from user {query.from_user.id}")

    all_applications = load_applications() # Load fresh data for modification
    target_app_index = -1
    target_app_obj = None

    for i, app in enumerate(all_applications):
        if app.get('cv_filename') == cv_filename:
            target_app_index = i
            target_app_obj = app
            break

    if not target_app_obj:
        logger.warning(f"Application with cv_filename '{cv_filename}' not found in main log for callback action '{action_type}'.")
        if query.message: # Check if there's a message to edit
            await query.edit_message_text(text="Error: Application not found or already processed.", reply_markup=None)
        return

    if action_type == "review_accept" or action_type == "review_reject":
        new_status = "reviewed_accepted" if action_type == "review_accept" else "reviewed_declined"

        all_applications[target_app_index]['status'] = new_status
        all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
        all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)

        if save_applications(all_applications):
            logger.info(f"Application {cv_filename} status updated to {new_status} in JSON log.")

            status_message_display = "Accepted" if new_status == "reviewed_accepted" else "Rejected"
            reviewer_name_escaped = escape_markdown_v2(query.from_user.first_name or "Unknown User")

            # Use query.message.text_markdown_v2 to preserve original entities/formatting
            # Ensure query.message and query.message.text_markdown_v2 are not None
            base_text = ""
            if query.message and query.message.text_markdown_v2:
                base_text = query.message.text_markdown_v2
            elif query.message and query.message.text: # Fallback to plain text if markdown_v2 is not available
                base_text = escape_markdown_v2(query.message.text) # Escape it if it was plain

            # Append status information
            # Ensure the timestamp is also escaped if it contains special characters, though strftime usually doesn't.
            review_time_str = escape_markdown_v2(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))
            updated_text = f"{base_text}\n\n*Status:* {status_message_display} by {reviewer_name_escaped} on {review_time_str}"

            try:
                if query.message: # Can only edit if there was an original message
                    await query.edit_message_text(
                        text=updated_text,
                        reply_markup=None,
                        parse_mode='MarkdownV2'
                    )
            except telegram.error.BadRequest as e:
                logger.error(f"Error editing message for {cv_filename} after status update: {e}. Text was: {updated_text}", exc_info=True)
                await context.bot.send_message(chat_id=query.effective_chat.id, text=f"Application {escape_markdown_v2(cv_filename)} status updated to {status_message_display}\. (Could not update original message)")
            except Exception as e: # Catch other potential errors during edit
                logger.error(f"Unexpected error editing message for {cv_filename}: {e}", exc_info=True)

            # Remove from current review session's list in user_data if it exists
            if 'review_list' in context.user_data and isinstance(context.user_data['review_list'], list):
                context.user_data['review_list'] = [
                    app for app in context.user_data['review_list'] if app.get('cv_filename') != cv_filename
                ]
                logger.info(f"Removed {cv_filename} from current review session list for user {query.from_user.id}.")
        else:
            logger.error(f"Failed to save application status update for {cv_filename}.")
            if query.message:
                 await query.edit_message_text("Error updating application status in the log. Please try again.", reply_markup=query.message.reply_markup)

    elif action_type == "get_cv":
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename)
        if os.path.exists(cv_path):
            try:
                with open(cv_path, 'rb') as cv_doc:
                    await context.bot.send_document(chat_id=query.effective_chat.id, document=cv_doc)
                logger.info(f"Sent CV {cv_filename} to chat_id {query.effective_chat.id}")
            except Exception as e:
                logger.error(f"Failed to send CV {cv_filename}: {e}", exc_info=True)
                await context.bot.send_message(chat_id=query.effective_chat.id, text=f"Sorry, could not send CV {escape_markdown_v2(cv_filename)}\.")
        else:
            logger.warning(f"CV file {cv_filename} not found at path {cv_path} for get_cv action.")
            await context.bot.send_message(chat_id=query.effective_chat.id, text=f"Sorry, CV file {escape_markdown_v2(cv_filename)} not found on server\.")

    else:
        logger.warning(f"Unknown callback action_type: {action_type} for cv_filename: {cv_filename}")
        if query.message:
            await query.edit_message_text("Unknown action. Please try again.", reply_markup=None)

async def handle_next_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Next Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    review_list_size = len(context.user_data.get('review_list', []))
    total_pages = (review_list_size + APPS_PER_PAGE - 1) // APPS_PER_PAGE

    if page_num < total_pages - 1:
        context.user_data['review_page_num'] = page_num + 1
        await display_application_page(update, context)
    else:
        # Make sure to use update.message for reply_text with ReplyKeyboardMarkup
        await update.message.reply_text("You are already on the last page.", reply_markup=review_mode_keyboard)

async def handle_previous_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Previous Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    if page_num > 0:
        context.user_data['review_page_num'] = page_num - 1
        await display_application_page(update, context)
    else:
        await update.message.reply_text("You are already on the first page.", reply_markup=review_mode_keyboard)

async def go_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Back to Main Menu requested by {update.effective_chat.id}")
    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)
    await update.message.reply_text(
        "Returning to the main menu.",
        reply_markup=main_menu_keyboard
    )

# --- End Command Handlers ---

def main(): # Changed from async def
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
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))

    # Add MessageHandler for the "Review New Applications" button
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^Review New Applications$"),
        start_review_session
    ))
    # Add MessageHandlers for pagination and menu navigation
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^Next Page$"),
        handle_next_page
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^Previous Page$"),
        handle_previous_page
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^Back to Main Menu$"),
        go_to_main_menu
    ))

    logger.info("HR Bot starting...")
    application.run_polling() # Changed from await application.run_polling()
    logger.info("HR Bot has stopped.")

if __name__ == '__main__':
    # The platform-specific asyncio policy setting for Windows,
    # if present (e.g., asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())),
    # should remain at the very top of the script (after imports).
    main()
