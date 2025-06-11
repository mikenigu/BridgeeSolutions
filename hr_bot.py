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
    ["View Accepted Apps", "View Rejected Apps"], # New row with two buttons
    ["Help"]
]
main_menu_keyboard = ReplyKeyboardMarkup(
    main_menu_keyboard_layout, resize_keyboard=True, one_time_keyboard=False
)

review_mode_keyboard_layout = [
    ["Previous Page", "Next Page"], # Swapped order
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

async def start_view_specific_status_session(update: Update, context: ContextTypes.DEFAULT_TYPE, target_status: str):
    # Access check should be done by the calling command/message handler if it's the first point of user interaction.
    # Or, if this can be called from various places, ensure access is checked appropriately.
    # The wrapper commands view_accepted_apps_command and view_rejected_apps_command don't do it,
    # so this function should.
    if not await restricted_access(update, context):
        return

    logger.info(f"Starting specific status view session for status '{target_status}', chat_id: {update.effective_chat.id}")
    all_applications = load_applications()

    # Filter for the target_status
    # Also, handle potential job title filter if context.args exist (e.g. from a future /view_accepted <job_title> command)
    # For now, the MessageHandlers for buttons won't pass context.args.
    job_title_filter = None
    if context.args: # Check if there are any arguments passed (e.g. from a command)
        job_title_filter = " ".join(context.args).strip().lower()
        if job_title_filter: # Log only if a filter is actually applied
             logger.info(f"Filtering specific status view for job title: '{escape_markdown_v2(job_title_filter)}'")

    apps_with_target_status = []
    for app in all_applications:
        if app.get('status') == target_status:
            if job_title_filter:
                if str(app.get('job_title', '')).lower() == job_title_filter: # Ensure job_title is str
                    apps_with_target_status.append(app)
            else:
                apps_with_target_status.append(app)

    reply_target = update.effective_message or update.message # Ensure we can reply
    if not reply_target: # Should ideally not happen if restricted_access passed and called from msg/cmd
        logger.error(f"start_view_specific_status_session: No message context for reply. Chat ID: {update.effective_chat.id}")
        # Cannot reply if we don't have a message to reply to, could send new message to chat_id
        # await context.bot.send_message(chat_id=update.effective_chat.id, text="An error occurred: No message context.")
        return

    if not apps_with_target_status:
        status_display_name = target_status.replace("reviewed_", "").replace("_", " ").capitalize()
        message_text = f"No applications found with status: {status_display_name}."
        if job_title_filter:
            message_text = f"No applications found for job title '{escape_markdown_v2(job_title_filter)}' with status: {status_display_name}."

        await reply_target.reply_text(message_text, reply_markup=main_menu_keyboard)
        # Clear any old review state
        context.user_data.pop('review_list', None)
        context.user_data.pop('review_page_num', None)
        context.user_data.pop('current_view_status', None) # Clear this specific status view flag
        return

    context.user_data['review_list'] = apps_with_target_status
    context.user_data['review_page_num'] = 0
    context.user_data['current_view_status'] = target_status # Store what status we are viewing

    status_display_name = target_status.replace("reviewed_", "").replace("_", " ").capitalize()
    logger.info(f"Found {len(apps_with_target_status)} applications with status '{target_status}'. Starting specific view session for chat_id {update.effective_chat.id}.")

    await reply_target.reply_text(
        f"Viewing {len(apps_with_target_status)} application(s) with status: {status_display_name}. Use navigation buttons below.",
        reply_markup=review_mode_keyboard
    )

    # Display the first page of these applications
    # We need a new display function or to make display_application_page more generic
    # For this plan, we decided on display_application_page_for_status_view
    await display_application_page_for_status_view(update, context)

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

    page_summary_content = f"Displaying page {page_num + 1} of {total_pages} ({APPS_PER_PAGE} per page). Applications {start_index + 1}-{min(end_index, total_apps)} of {total_apps} total 'new' applications."
    final_page_summary_text = escape_markdown_v2(page_summary_content)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=final_page_summary_text,
            parse_mode='MarkdownV2'
        )
    except telegram.error.BadRequest as e:
        logger.error(f"Error sending page summary text due to BadRequest: {e}. Text was: {final_page_summary_text}", exc_info=True)
        # Send a non-markdown version or a simpler message as fallback
        await context.bot.send_message(
            chat_id=chat_id,
            text="Error displaying page summary. Continuing to applications..."
        )
    except Exception as e:
        logger.error(f"Unexpected error sending page summary text: {e}. Text was: {final_page_summary_text}", exc_info=True)
        # Send a non-markdown version or a simpler message as fallback
        await context.bot.send_message(
            chat_id=chat_id,
            text="An unexpected error occurred displaying page summary. Continuing..."
        )

    for app_data in apps_on_page:
        cv_filename = app_data.get('cv_filename', 'N/A')

        escaped_full_name = escape_markdown_v2(app_data.get('full_name', 'N/A'))
        escaped_email = escape_markdown_v2(app_data.get('email', 'N/A'))
        escaped_job_title = escape_markdown_v2(str(app_data.get('job_title', 'N/A')))

        parts = cv_filename.split('-', 1)
        original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename
        escaped_original_cv_name_display = escape_markdown_v2(original_cv_name_for_display)

        submission_timestamp = escape_markdown_v2(str(app_data.get('timestamp', 'N/A')))

        cover_letter_raw = app_data.get('cover_letter', '')
        escaped_cover_letter_snippet = ""
        if cover_letter_raw and cover_letter_raw.strip():
            snippet_length = 200
            cover_letter_snippet_text = cover_letter_raw[:snippet_length]
            if len(cover_letter_raw) > snippet_length:
                cover_letter_snippet_text += "..."
            escaped_cover_letter_snippet = escape_markdown_v2(cover_letter_snippet_text)

        message_text = (
            f"*New Application*\n\n"
            f"*Name:* {escaped_full_name}\n"
            f"*Email:* {escaped_email}\n"
            f"*Job Title:* {escaped_job_title}\n"
        )

        if escaped_cover_letter_snippet:
            message_text += f"*Cover Letter Snippet:*\n{escaped_cover_letter_snippet}\n\n"

        message_text += (
            f"*Original CV Name:* {escaped_original_cv_name_display}\n"
            f"*Submitted:* {submission_timestamp}\n"
        )

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
            error_content = f"Error displaying application for {app_data.get('full_name', 'N/A')} (CV: {cv_filename}). Some special characters might have caused an issue with the detailed display. Please check server logs."
            escaped_error_content = escape_markdown_v2(error_content)
            await context.bot.send_message(chat_id=chat_id, text=escaped_error_content, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Unexpected error sending application details for {cv_filename}: {e}. Text was: {message_text}", exc_info=True)
            unexpected_error_content = f"An unexpected error occurred while trying to display application: {app_data.get('full_name', 'N/A')} (CV: {cv_filename})."
            escaped_unexpected_error_content = escape_markdown_v2(unexpected_error_content)
            await context.bot.send_message(chat_id=chat_id, text=escaped_unexpected_error_content, parse_mode='MarkdownV2')

    logger.info(f"Displayed {len(apps_on_page)} applications on page {page_num + 1} for chat_id {chat_id}.")

async def display_application_page_for_status_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Attempting to display a page for status view.")
    review_list = context.user_data.get('review_list', [])
    page_num = context.user_data.get('review_page_num', 0)
    current_view_status = context.user_data.get('current_view_status', 'N/A')

    chat_id = update.effective_chat.id
    reply_target = update.effective_message
    if not reply_target and update.callback_query:
        reply_target = update.callback_query.message

    if not review_list:
        logger.info(f"display_application_page_for_status_view called with no review_list for status {current_view_status} in chat {chat_id}.")
        msg_content = f"No applications to display for status: {current_view_status.replace('reviewed_', '').capitalize()}."
        if reply_target:
            await reply_target.reply_text(msg_content, reply_markup=main_menu_keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg_content, reply_markup=main_menu_keyboard)
        return

    start_index = page_num * APPS_PER_PAGE
    end_index = start_index + APPS_PER_PAGE
    apps_on_page = review_list[start_index:end_index]

    if not apps_on_page:
        logger.info(f"No applications found for page {page_num} in status view {current_view_status} for chat {chat_id}.")
        no_apps_message = "No more applications to display on this page."
        if page_num > 0:
             no_apps_message = "You've reached the end of the list for this status."
        if reply_target:
            await reply_target.reply_text(no_apps_message, reply_markup=review_mode_keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text=no_apps_message, reply_markup=review_mode_keyboard)
        return

    total_apps = len(review_list)
    total_pages = (total_apps + APPS_PER_PAGE - 1) // APPS_PER_PAGE

    status_display_name = current_view_status.replace("reviewed_", "").replace("_", " ").capitalize()
    page_summary_content = f"Displaying page {page_num + 1} of {total_pages} for '{status_display_name}' applications. ({start_index + 1}-{min(end_index, total_apps)} of {total_apps} total)."
    final_page_summary_text = escape_markdown_v2(page_summary_content)

    try:
        await context.bot.send_message(chat_id=chat_id, text=final_page_summary_text, parse_mode='MarkdownV2')
    except telegram.error.BadRequest as e:
        logger.error(f"Error sending page summary for status view: {e}. Text: {final_page_summary_text}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="Error displaying page summary. Continuing...")
    except Exception as e:
        logger.error(f"Unexpected error sending page summary for status view: {e}. Text: {final_page_summary_text}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="An error occurred displaying page summary. Continuing...")

    for app_data in apps_on_page:
        cv_filename = app_data.get('cv_filename', 'N/A')

        escaped_full_name = escape_markdown_v2(app_data.get('full_name', 'N/A'))
        escaped_email = escape_markdown_v2(app_data.get('email', 'N/A'))
        escaped_job_title = escape_markdown_v2(str(app_data.get('job_title', 'N/A')))

        parts = cv_filename.split('-', 1)
        original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename
        escaped_original_cv_name_display = escape_markdown_v2(original_cv_name_for_display)

        submission_timestamp = escape_markdown_v2(str(app_data.get('timestamp', 'N/A')))
        reviewed_timestamp_raw = app_data.get('reviewed_timestamp')
        reviewed_by_raw = app_data.get('reviewed_by')

        message_text = (
            f"*Status: {escape_markdown_v2(status_display_name)}*\n\n"
            f"*Name:* {escaped_full_name}\n"
            f"*Email:* {escaped_email}\n"
            f"*Job Title:* {escaped_job_title}\n"
        )

        cover_letter_raw = app_data.get('cover_letter', '')
        if cover_letter_raw and cover_letter_raw.strip():
            snippet_length = 200
            cover_letter_snippet_text = cover_letter_raw[:snippet_length]
            if len(cover_letter_raw) > snippet_length:
                cover_letter_snippet_text += "..."
            escaped_cover_letter_snippet = escape_markdown_v2(cover_letter_snippet_text)
            message_text += f"*Cover Letter Snippet:*\n{escaped_cover_letter_snippet}\n\n"

        message_text += (
            f"*Original CV Name:* {escaped_original_cv_name_display}\n"
            f"*Submitted:* {submission_timestamp}\n"
        )
        if reviewed_timestamp_raw:
            message_text += f"*Reviewed:* {escape_markdown_v2(str(reviewed_timestamp_raw))} by UserID: {escape_markdown_v2(str(reviewed_by_raw))}\n"

        inline_keyboard_buttons = []
        if current_view_status == "reviewed_accepted":
            inline_keyboard_buttons = [
                [InlineKeyboardButton("Mark as Rejected", callback_data=f"change_status:declined:{cv_filename}")],
                [InlineKeyboardButton("Set as New", callback_data=f"change_status:new:{cv_filename}")],
                [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{cv_filename}")]
            ]
        elif current_view_status == "reviewed_declined":
            inline_keyboard_buttons = [
                [InlineKeyboardButton("Mark as Accepted", callback_data=f"change_status:accepted:{cv_filename}")],
                [InlineKeyboardButton("Set as New", callback_data=f"change_status:new:{cv_filename}")],
                [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{cv_filename}")]
            ]
        else:
             inline_keyboard_buttons.append([InlineKeyboardButton("Get CV", callback_data=f"get_cv:{cv_filename}")])

        reply_markup = InlineKeyboardMarkup(inline_keyboard_buttons) if inline_keyboard_buttons else None

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )
        except telegram.error.BadRequest as e:
            logger.error(f"Error sending status view details for {cv_filename} (Status: {current_view_status}) due to BadRequest: {e}. Text was: {message_text}", exc_info=True)
            error_content = f"Error displaying application: {app_data.get('full_name', 'N/A')} (CV: {cv_filename}) for status '{status_display_name}'. Check logs."
            escaped_error_content = escape_markdown_v2(error_content)
            await context.bot.send_message(chat_id=chat_id, text=escaped_error_content, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Unexpected error sending status view details for {cv_filename} (Status: {current_view_status}): {e}. Text was: {message_text}", exc_info=True)
            error_content_unexpected = f"An error occurred displaying: {app_data.get('full_name', 'N/A')} (CV: {cv_filename})."
            escaped_error_content_unexpected = escape_markdown_v2(error_content_unexpected)
            await context.bot.send_message(chat_id=chat_id, text=escaped_error_content_unexpected, parse_mode='MarkdownV2')

    logger.info(f"Displayed {len(apps_on_page)} applications on page {page_num + 1} in status view '{current_view_status}' for chat {chat_id}.")

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
    context.user_data.pop('current_view_status', None) # Also clear this on /start

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
    context.user_data.pop('current_view_status', None) # Also clear this on /stop

    await update.message.reply_text(
        "Custom keyboard removed. Send /start to show it again.",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"Custom keyboard removed, review state cleared for chat_id: {update.effective_chat.id}")

async def view_accepted_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'View Accepted Apps' button."""
    if not await restricted_access(update, context): # Added restricted_access
        return
    logger.info(f"'View Accepted Apps' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_accepted")

async def view_rejected_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'View Rejected Apps' button."""
    if not await restricted_access(update, context): # Added restricted_access
        return
    logger.info(f"'View Rejected Apps' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_declined")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    callback_parts = query.data.split(":", 2) # Max split 2 for change_status
    action_prefix = callback_parts[0]
    cv_filename_from_callback = None
    new_short_status_from_callback = None

    if not callback_parts:
        logger.error(f"Empty callback_data received.")
        if query.message: await query.edit_message_text("Error: Empty callback data.")
        return

    logger.info(f"Callback received: data='{query.data}' by user {query.from_user.id}")

    # Parse callback data
    if action_prefix in ["review_accept", "review_reject", "get_cv"]:
        if len(callback_parts) >= 2:
            cv_filename_from_callback = callback_parts[1]
        else:
            logger.error(f"Invalid format for {action_prefix}: {query.data} - missing CV filename.")
            if query.message: await query.edit_message_text("Error: Invalid action data (missing CV filename).")
            return
    elif action_prefix == "change_status":
        if len(callback_parts) == 3:
            new_short_status_from_callback = callback_parts[1]
            cv_filename_from_callback = callback_parts[2]
        else:
            logger.error(f"Invalid format for {action_prefix}: {query.data} - missing status or CV filename.")
            if query.message: await query.edit_message_text("Error: Invalid action data (missing status or CV filename).")
            return
    else:
        logger.warning(f"Unknown callback action_prefix: {action_prefix} from data: {query.data}")
        if query.message: await query.edit_message_text("Unknown action.", reply_markup=None)
        return

    if not cv_filename_from_callback: # Safeguard, should be caught by parsing logic
        logger.error(f"CV filename could not be parsed from callback data: {query.data}")
        if query.message: await query.edit_message_text("Error processing action: CV filename missing.")
        return

    # Common logic: Load applications and find the target application
    all_applications = load_applications()
    target_app_index = -1
    target_app_obj = None
    for i, app in enumerate(all_applications):
        if app.get('cv_filename') == cv_filename_from_callback:
            target_app_index = i
            target_app_obj = app
            break

    if not target_app_obj:
        logger.warning(f"App with cv_filename '{cv_filename_from_callback}' not found for action '{action_prefix}'.")
        if query.message: await query.edit_message_text(text="Error: Application not found.", reply_markup=None) # Simplified error
        return

    # --- Action Handling ---
    if action_prefix == "review_accept" or action_prefix == "review_reject":
        new_status = "reviewed_accepted" if action_prefix == "review_accept" else "reviewed_declined"

        all_applications[target_app_index]['status'] = new_status
        all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
        all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)

        if save_applications(all_applications):
            logger.info(f"Application {cv_filename_from_callback} status updated to {new_status} by user {query.from_user.id}.")

            status_message_display = "Accepted" if new_status == "reviewed_accepted" else "Rejected"
            reviewer_name_escaped = escape_markdown_v2(query.from_user.first_name or "Unknown User")
            review_time_str = escape_markdown_v2(datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))

            base_text = ""
            if query.message and query.message.text_markdown_v2: base_text = query.message.text_markdown_v2
            elif query.message and query.message.text: base_text = escape_markdown_v2(query.message.text) # Escape if it was plain

            updated_text = f"{base_text}\n\n*Status:* {status_message_display} by {reviewer_name_escaped} on {review_time_str}"

            try:
                if query.message: await query.edit_message_text(text=updated_text, reply_markup=None, parse_mode='MarkdownV2')
            except telegram.error.BadRequest as e:
                logger.error(f"Error editing message for {cv_filename_from_callback} (review action): {e}. Text: {updated_text}", exc_info=True)
                fallback_content = f"Application {escape_markdown_v2(cv_filename_from_callback)} status updated to {status_message_display}."
                await context.bot.send_message(chat_id=query.message.chat.id, text=escape_markdown_v2(fallback_content), parse_mode='MarkdownV2')
            except Exception as e: # Catch other potential errors during edit
                 logger.error(f"Unexpected error editing message for {cv_filename_from_callback} (review action): {e}", exc_info=True)


            # Remove from current 'new' review session's list in user_data
            # Only remove if the current view is for 'new' apps, which is implied by review_accept/reject action existing
            if 'review_list' in context.user_data and context.user_data.get('current_view_status', 'new') == 'new': # Check if it's the 'new' app list
                context.user_data['review_list'] = [app for app in context.user_data['review_list'] if app.get('cv_filename') != cv_filename_from_callback]
                logger.info(f"Removed {cv_filename_from_callback} from 'new' review session list for user {query.from_user.id}.")
        else:
            logger.error(f"Failed to save application status update for {cv_filename_from_callback} (review action).")
            if query.message: await query.edit_message_text("Error updating application status in log.", reply_markup=query.message.reply_markup if query.message else None)

    elif action_prefix == "change_status":
        final_new_status = ""
        if new_short_status_from_callback == "accepted": final_new_status = "reviewed_accepted"
        elif new_short_status_from_callback == "declined": final_new_status = "reviewed_declined"
        elif new_short_status_from_callback == "new": final_new_status = "new"
        else:
            logger.error(f"Unknown new_short_status '{new_short_status_from_callback}' for change_status action on CV {cv_filename_from_callback}.")
            if query.message: await query.edit_message_text("Error: Invalid status change value.", reply_markup=None)
            return

        all_applications[target_app_index]['status'] = final_new_status
        if final_new_status == "new":
            all_applications[target_app_index].pop('reviewed_timestamp', None)
            all_applications[target_app_index].pop('reviewed_by', None)
        else: # For "reviewed_accepted" or "reviewed_declined"
            all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
            all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)

        if save_applications(all_applications):
            logger.info(f"Application {cv_filename_from_callback} status changed to {final_new_status} by user {query.from_user.id}.")
            status_display_name = final_new_status.replace("reviewed_", "").replace("_", " ").capitalize()

            # Extract original CV name for display in confirmation message
            parts = cv_filename_from_callback.split('-', 1)
            original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename_from_callback

            confirm_message_content = f"Status for {escape_markdown_v2(target_app_obj.get('full_name', 'N/A'))} (CV: {escape_markdown_v2(original_cv_name_for_display)}) changed to: *{escape_markdown_v2(status_display_name)}*."

            try:
                if query.message:
                    await query.edit_message_text(text=confirm_message_content, reply_markup=None, parse_mode='MarkdownV2')
            except telegram.error.BadRequest as e:
                logger.error(f"Error editing message for {cv_filename_from_callback} (change_status): {e}. Text: {confirm_message_content}", exc_info=True)
                # If edit fails, send as a new message
                await context.bot.send_message(chat_id=query.message.chat.id, text=confirm_message_content, parse_mode='MarkdownV2')
            except Exception as e: # Catch other potential errors during edit
                 logger.error(f"Unexpected error editing message for {cv_filename_from_callback} (change_status): {e}", exc_info=True)

            # Remove from current review session's list in user_data because its status has changed
            if 'review_list' in context.user_data and isinstance(context.user_data.get('review_list'), list):
                context.user_data['review_list'] = [app for app in context.user_data['review_list'] if app.get('cv_filename') != cv_filename_from_callback]
                logger.info(f"Removed {cv_filename_from_callback} from current status view list for user {query.from_user.id}.")
        else:
            logger.error(f"Failed to save application status update for {cv_filename_from_callback} (change_status).")
            if query.message: await query.edit_message_text("Error updating application status in log.", reply_markup=query.message.reply_markup if query.message else None)

    elif action_prefix == "get_cv":
        cv_path = os.path.join(UPLOAD_FOLDER, cv_filename_from_callback)
        if os.path.exists(cv_path):
            try:
                with open(cv_path, 'rb') as cv_doc:
                    parts = cv_filename_from_callback.split('-', 1)
                    original_display_name = parts[1] if len(parts) > 1 else cv_filename_from_callback
                    await context.bot.send_document(chat_id=query.message.chat.id, document=cv_doc, filename=original_display_name)
                logger.info(f"Sent CV {cv_filename_from_callback} as {original_display_name} to chat_id {query.message.chat.id}")
            except Exception as e:
                logger.error(f"Failed to send CV {cv_filename_from_callback}: {e}", exc_info=True)
                error_content = f"Sorry, could not send CV {cv_filename_from_callback}."
                await context.bot.send_message(chat_id=query.message.chat.id, text=escape_markdown_v2(error_content), parse_mode='MarkdownV2')
        else:
            logger.warning(f"CV file {cv_filename_from_callback} not found at path {cv_path} for get_cv action.")
            error_content = f"Sorry, CV file {cv_filename_from_callback} not found on server."
            await context.bot.send_message(chat_id=query.message.chat.id, text=escape_markdown_v2(error_content), parse_mode='MarkdownV2')

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
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^View Accepted Apps$"),
        view_accepted_apps_command  # New function to be created in the next step
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^View Rejected Apps$"),
        view_rejected_apps_command  # New function to be created in the next step
    ))

    logger.info("HR Bot starting...")
    application.run_polling() # Changed from await application.run_polling()
    logger.info("HR Bot has stopped.")

if __name__ == '__main__':
    # The platform-specific asyncio policy setting for Windows,
    # if present (e.g., asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())),
    # should remain at the very top of the script (after imports).
    main()
