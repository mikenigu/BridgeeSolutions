import os
import json
import logging
from datetime import datetime # Though not used in initial setup, good to have for later
import asyncio # Required for python-telegram-bot v20+ async operations

# Apply Windows event loop policy patch if on Windows
import platform
if platform.system() == "Windows":
    current_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
import telegram # Added for telegram.error.BadRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
    MessageHandler,
    filters
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
HR_CHAT_ID = os.getenv('HR_CHAT_ID')

APPLICATION_LOG_FILE = 'submitted_applications.log.json'
UPLOAD_FOLDER = 'uploads/'
APPS_PER_PAGE = 3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Keyboards ---
main_menu_keyboard_layout = [
    ["Review New Applications"],
    ["View Accepted Apps", "View Rejected Apps"],
    ["Help"]
]
main_menu_keyboard = ReplyKeyboardMarkup(
    main_menu_keyboard_layout, resize_keyboard=True, one_time_keyboard=False
)

review_mode_keyboard_layout = [
    ["Previous Page", "Next Page"],
    ["Back to Main Menu"]
]
review_mode_keyboard = ReplyKeyboardMarkup(
    review_mode_keyboard_layout, resize_keyboard=True, one_time_keyboard=False
)
# --- End Keyboards ---

# --- Helper Functions ---
def load_applications() -> list:
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
    try:
        with open(APPLICATION_LOG_FILE, 'w') as f:
            json.dump(applications_data, f, indent=4)
        logger.info(f"Successfully saved {len(applications_data)} applications to {APPLICATION_LOG_FILE}")
        return True
    except IOError as e:
        logger.error(f"IOError writing to {APPLICATION_LOG_FILE}: {e}", exc_info=True)
        return False

def get_application_by_cv_filename(cv_filename: str, applications_data: list = None) -> dict | None:
    # This function might need adjustment if we solely rely on app_id for lookups from callbacks
    # For now, it expects the full cv_filename as stored in the log.
    if applications_data is None:
        applications_data = load_applications()
    for app in applications_data:
        if app.get('cv_filename') == cv_filename: # cv_filename is the full unique name
            return app
    logger.warning(f"Application with full cv_filename '{cv_filename}' not found by get_application_by_cv_filename.")
    return None

def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped_text = text
    for char in escape_chars:
        escaped_text = escaped_text.replace(char, '\\' + char)
    return escaped_text
# --- End Helper Functions ---

# --- Command Handlers ---
async def restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id_str = str(update.effective_chat.id)
    if chat_id_str != HR_CHAT_ID:
        unauthorized_message = "Sorry, you are not authorized to use this command."
        if update.message:
            await update.message.reply_text(unauthorized_message)
        logger.warning(f"Unauthorized access attempt by chat_id: {chat_id_str}")
        return False
    return True

async def start_review_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Attempting to start review session for chat_id: {update.effective_chat.id}")
    all_applications = load_applications()
    job_title_filter = None
    if context.args:
        job_title_filter = " ".join(context.args).strip().lower()
        if job_title_filter:
            logger.info(f"Filtering review session for job title: '{escape_markdown_v2(job_title_filter)}'")
    new_applications_to_review = [
        app for app in all_applications
        if app.get('status') == 'new' and
           (not job_title_filter or str(app.get('job_title', '')).lower() == job_title_filter)
    ]
    reply_target = update.effective_message
    if not reply_target and update.callback_query :
        reply_target = update.callback_query.message
    if not reply_target :
        logger.error(f"start_review_session: No effective_message to reply to for chat_id {update.effective_chat.id}.")
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
    context.user_data['current_view_status'] = 'new' # Explicitly set for new apps review
    logger.info(f"Found {len(new_applications_to_review)} new applications. Starting review session for chat_id {update.effective_chat.id}.")
    await reply_target.reply_text(
        f"Starting review of {len(new_applications_to_review)} application(s). Use navigation buttons below.",
        reply_markup=review_mode_keyboard
    )
    await display_application_page(update, context)

async def start_view_specific_status_session(update: Update, context: ContextTypes.DEFAULT_TYPE, target_status: str):
    if not await restricted_access(update, context): return
    logger.info(f"Starting specific status view session for status '{target_status}', chat_id: {update.effective_chat.id}")
    all_applications = load_applications()
    job_title_filter = None
    if context.args:
        job_title_filter = " ".join(context.args).strip().lower()
        if job_title_filter:
             logger.info(f"Filtering specific status view for job title: '{escape_markdown_v2(job_title_filter)}'")
    apps_with_target_status = [
        app for app in all_applications
        if app.get('status') == target_status and
           (not job_title_filter or str(app.get('job_title', '')).lower() == job_title_filter)
    ]
    reply_target = update.effective_message or update.message
    if not reply_target:
        logger.error(f"start_view_specific_status_session: No message context for reply. Chat ID: {update.effective_chat.id}")
        return
    status_display_name = target_status.replace("reviewed_", "").replace("_", " ").capitalize()
    if not apps_with_target_status:
        message_text = f"No applications found with status: {status_display_name}."
        if job_title_filter:
            message_text = f"No applications found for job title '{escape_markdown_v2(job_title_filter)}' with status: {status_display_name}."
        await reply_target.reply_text(message_text, reply_markup=main_menu_keyboard)
        context.user_data.pop('review_list', None)
        context.user_data.pop('review_page_num', None)
        context.user_data.pop('current_view_status', None)
        return
    context.user_data['review_list'] = apps_with_target_status
    context.user_data['review_page_num'] = 0
    context.user_data['current_view_status'] = target_status
    logger.info(f"Found {len(apps_with_target_status)} applications with status '{target_status}'. Starting specific view session for chat_id {update.effective_chat.id}.")
    await reply_target.reply_text(
        f"Viewing {len(apps_with_target_status)} application(s) with status: {status_display_name}. Use navigation buttons below.",
        reply_markup=review_mode_keyboard
    )
    await display_application_page_for_status_view(update, context)

async def display_application_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This function is for 'new' applications
    logger.info("Attempting to display an application page (for new apps).")
    review_list = context.user_data.get('review_list', [])
    page_num = context.user_data.get('review_page_num', 0)
    chat_id = update.effective_chat.id
    reply_target = update.effective_message
    if not reply_target and update.callback_query:
        reply_target = update.callback_query.message
    if not review_list:
        logger.info(f"display_application_page called with no review_list for chat_id {chat_id}.")
        msg_content = "No applications to display in the current review session."
        if reply_target: await reply_target.reply_text(msg_content, reply_markup=main_menu_keyboard)
        else: await context.bot.send_message(chat_id=chat_id, text=msg_content, reply_markup=main_menu_keyboard)
        return
    start_index = page_num * APPS_PER_PAGE
    end_index = start_index + APPS_PER_PAGE
    apps_on_page = review_list[start_index:end_index]
    if not apps_on_page:
        logger.info(f"No applications found for page {page_num} in chat {chat_id} (new apps view).")
        no_apps_message = "You've reached the end of the new application list." if page_num > 0 else "No new applications to display at the moment."
        if reply_target: await reply_target.reply_text(no_apps_message, reply_markup=review_mode_keyboard)
        else: await context.bot.send_message(chat_id=chat_id, text=no_apps_message, reply_markup=review_mode_keyboard)
        return
    total_apps = len(review_list)
    total_pages = (total_apps + APPS_PER_PAGE - 1) // APPS_PER_PAGE
    page_summary_content = f"Displaying page {page_num + 1} of {total_pages} for 'New' applications. ({start_index + 1}-{min(end_index, total_apps)} of {total_apps} total)."
    final_page_summary_text = escape_markdown_v2(page_summary_content)
    try:
        await context.bot.send_message(chat_id=chat_id, text=final_page_summary_text, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending page summary for new apps: {e}. Text: {final_page_summary_text}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="Error displaying page summary. Continuing...")
    for app_data in apps_on_page:
        cv_filename_stored = app_data.get('cv_filename', 'N/A')
        app_id_for_callback = cv_filename_stored.split('-', 1)[0] if isinstance(cv_filename_stored, str) and '-' in cv_filename_stored else cv_filename_stored
        parts = cv_filename_stored.split('-', 1) if isinstance(cv_filename_stored, str) else []
        original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename_stored
        message_text = (
            f"*New Application*\n\n"
            f"*Name:* {escape_markdown_v2(app_data.get('full_name', 'N/A'))}\n"
            f"*Email:* {escape_markdown_v2(app_data.get('email', 'N/A'))}\n"
            f"*Job Title:* {escape_markdown_v2(str(app_data.get('job_title', 'N/A')))}\n"
        )
        cover_letter_raw = app_data.get('cover_letter', '')
        if cover_letter_raw and cover_letter_raw.strip():
            snippet_length = 200
            cover_letter_snippet_text = cover_letter_raw[:snippet_length] + ("..." if len(cover_letter_raw) > snippet_length else "")
            message_text += f"*Cover Letter Snippet:*\n{escape_markdown_v2(cover_letter_snippet_text)}\n\n"
        message_text += (
            f"*Original CV Name:* {escape_markdown_v2(original_cv_name_for_display)}\n"
            f"*Submitted:* {escape_markdown_v2(str(app_data.get('timestamp', 'N/A')))}\n"
        )
        keyboard = [[InlineKeyboardButton("Accept", callback_data=f"review_accept:{app_id_for_callback}"), InlineKeyboardButton("Reject", callback_data=f"review_reject:{app_id_for_callback}")], [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_for_callback}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
        except Exception as e: # Broader exception for individual message
            logger.error(f"Error sending new app details for {cv_filename_stored}: {e}. Text: {message_text}", exc_info=True)
            error_content = f"Error displaying application: {app_data.get('full_name', 'N/A')} (CV: {cv_filename_stored})."
            await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(error_content), parse_mode='MarkdownV2')
    logger.info(f"Displayed {len(apps_on_page)} new applications on page {page_num + 1} for chat_id {chat_id}.")

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
        if reply_target: await reply_target.reply_text(msg_content, reply_markup=main_menu_keyboard)
        else: await context.bot.send_message(chat_id=chat_id, text=msg_content, reply_markup=main_menu_keyboard)
        return
    start_index = page_num * APPS_PER_PAGE
    end_index = start_index + APPS_PER_PAGE
    apps_on_page = review_list[start_index:end_index]
    if not apps_on_page:
        logger.info(f"No applications found for page {page_num} in status view {current_view_status} for chat {chat_id}.")
        no_apps_message = "You've reached the end of the list for this status." if page_num > 0 else "No applications to display for this status."
        if reply_target: await reply_target.reply_text(no_apps_message, reply_markup=review_mode_keyboard)
        else: await context.bot.send_message(chat_id=chat_id, text=no_apps_message, reply_markup=review_mode_keyboard)
        return
    total_apps = len(review_list)
    total_pages = (total_apps + APPS_PER_PAGE - 1) // APPS_PER_PAGE
    status_display_name = current_view_status.replace("reviewed_", "").replace("_", " ").capitalize()
    page_summary_content = f"Displaying page {page_num + 1} of {total_pages} for '{status_display_name}' applications. ({start_index + 1}-{min(end_index, total_apps)} of {total_apps} total)."
    final_page_summary_text = escape_markdown_v2(page_summary_content)
    try:
        await context.bot.send_message(chat_id=chat_id, text=final_page_summary_text, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending page summary for status view: {e}. Text: {final_page_summary_text}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="Error displaying page summary. Continuing...")
    for app_data in apps_on_page:
        cv_filename_stored = app_data.get('cv_filename', 'N/A')
        app_id_for_callback = cv_filename_stored.split('-', 1)[0] if isinstance(cv_filename_stored, str) and '-' in cv_filename_stored else cv_filename_stored
        parts = cv_filename_stored.split('-', 1) if isinstance(cv_filename_stored, str) else []
        original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename_stored
        message_text = (
            f"*Status: {escape_markdown_v2(status_display_name)}*\n\n"
            f"*Name:* {escape_markdown_v2(app_data.get('full_name', 'N/A'))}\n"
            f"*Email:* {escape_markdown_v2(app_data.get('email', 'N/A'))}\n"
            f"*Job Title:* {escape_markdown_v2(str(app_data.get('job_title', 'N/A')))}\n"
        )
        cover_letter_raw = app_data.get('cover_letter', '')
        if cover_letter_raw and cover_letter_raw.strip():
            snippet_length = 200
            cover_letter_snippet_text = cover_letter_raw[:snippet_length] + ("..." if len(cover_letter_raw) > snippet_length else "")
            message_text += f"*Cover Letter Snippet:*\n{escape_markdown_v2(cover_letter_snippet_text)}\n\n"
        message_text += (
            f"*Original CV Name:* {escape_markdown_v2(original_cv_name_for_display)}\n"
            f"*Submitted:* {escape_markdown_v2(str(app_data.get('timestamp', 'N/A')))}\n"
        )
        reviewed_timestamp_raw = app_data.get('reviewed_timestamp')
        if reviewed_timestamp_raw:
            message_text += f"*Reviewed:* {escape_markdown_v2(str(reviewed_timestamp_raw))} by UserID: {escape_markdown_v2(str(app_data.get('reviewed_by')))}\n"
        inline_keyboard_buttons = []
        if current_view_status == "reviewed_accepted":
            inline_keyboard_buttons = [[InlineKeyboardButton("Mark as Rejected", callback_data=f"change_status:declined:{app_id_for_callback}")], [InlineKeyboardButton("Set as New", callback_data=f"change_status:new:{app_id_for_callback}")], [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_for_callback}")]]
        elif current_view_status == "reviewed_declined":
            inline_keyboard_buttons = [[InlineKeyboardButton("Mark as Accepted", callback_data=f"change_status:accepted:{app_id_for_callback}")], [InlineKeyboardButton("Set as New", callback_data=f"change_status:new:{app_id_for_callback}")], [InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_for_callback}")]]
        else: inline_keyboard_buttons.append([InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_for_callback}")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard_buttons) if inline_keyboard_buttons else None
        try:
            await context.bot.send_message(chat_id=chat_id,text=message_text,reply_markup=reply_markup,parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Error sending status view details for {cv_filename_stored}: {e}. Text: {message_text}", exc_info=True)
            error_content = f"Error displaying application: {app_data.get('full_name', 'N/A')} (CV: {cv_filename_stored})."
            await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(error_content), parse_mode='MarkdownV2')
    logger.info(f"Displayed {len(apps_on_page)} applications on page {page_num + 1} in status view '{current_view_status}' for chat {chat_id}.")

async def review_applications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    await start_review_session(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)
    context.user_data.pop('current_view_status', None)
    await update.message.reply_text("Welcome to the HR Bot! Please use the menu below or type commands.", reply_markup=main_menu_keyboard)
    logger.info(f"Sent /start menu to {update.effective_chat.id}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)
    context.user_data.pop('current_view_status', None)
    await update.message.reply_text("Custom keyboard removed. Send /start to show it again.", reply_markup=ReplyKeyboardRemove())
    logger.info(f"Custom keyboard removed, review state cleared for chat_id: {update.effective_chat.id}")

async def view_accepted_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"'View Accepted Apps' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_accepted")

async def view_rejected_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"'View Rejected Apps' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_declined")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_parts = query.data.split(":", 2)
    action_prefix = callback_parts[0]
    app_id_from_callback = None
    new_short_status_from_callback = None
    if not callback_parts:
        logger.error(f"Empty callback_data received.")
        if query.message: await query.edit_message_text("Error: Empty callback data.")
        return
    logger.info(f"Callback received. Raw data: '{query.data}' by user {query.from_user.id}")
    parsed_app_id_for_log = callback_parts[1] if len(callback_parts) > 1 else "N/A"
    parsed_new_status_for_log = callback_parts[2] if len(callback_parts) == 3 and action_prefix == "change_status" else "N/A"
    if action_prefix != "change_status" and len(callback_parts) == 3:
        parsed_new_status_for_log = "N/A (unexpected 3rd part)"
    logger.info(f"Pre-validation Parsed: action_prefix='{action_prefix}', potential_app_id='{parsed_app_id_for_log}', potential_new_status='{parsed_new_status_for_log}'")
    if action_prefix in ["review_accept", "review_reject", "get_cv"]:
        if len(callback_parts) == 2: app_id_from_callback = callback_parts[1]
        else:
            logger.error(f"Invalid format for {action_prefix}: {query.data} - expected 2 parts.")
            if query.message: await query.edit_message_text("Error: Invalid action data format.")
            return
    elif action_prefix == "change_status":
        if len(callback_parts) == 3:
            new_short_status_from_callback = callback_parts[1]
            app_id_from_callback = callback_parts[2]
        else:
            logger.error(f"Invalid format for {action_prefix}: {query.data} - expected 3 parts.")
            if query.message: await query.edit_message_text("Error: Invalid action data format.")
            return
    else:
        logger.warning(f"Unknown callback action_prefix: {action_prefix} from data: {query.data}")
        if query.message: await query.edit_message_text("Unknown action.", reply_markup=None)
        return
    if not app_id_from_callback:
        logger.error(f"App ID could not be parsed from callback data: {query.data}")
        if query.message: await query.edit_message_text("Error processing action: App ID missing.")
        return
    logger.info(f"Post-validation Parsed: action_prefix='{action_prefix}', app_id='{app_id_from_callback}', new_status='{new_short_status_from_callback if new_short_status_from_callback else 'N/A'}'")
    all_applications = load_applications()
    target_app_index = -1
    target_app_obj = None
    search_prefix_for_loop = app_id_from_callback + '-'
    for i, app in enumerate(all_applications):
        stored_cv_filename = app.get('cv_filename', '')
        if stored_cv_filename.startswith(search_prefix_for_loop):
            target_app_index = i
            target_app_obj = app
            logger.info(f"Match found! Stored CV: '{stored_cv_filename}', App ID: '{app_id_from_callback}'")
            break
    if not target_app_obj:
        logger.warning(f"Application NOT FOUND. App_id from callback: '{app_id_from_callback}'. Search prefix used: '{search_prefix_for_loop}'.")
        available_cv_filenames = [a.get('cv_filename', 'MISSING_CV_FILENAME_KEY') for a in all_applications]
        logger.info(f"Available cv_filenames in log for comparison: {available_cv_filenames}")
        if query.message: await query.edit_message_text(text="Error: Application not found (diag_v2).", reply_markup=None)
        return
    full_cv_filename = target_app_obj.get('cv_filename')
    applicant_name_raw = target_app_obj.get('full_name', 'N/A')
    parts = full_cv_filename.split('-', 1) if isinstance(full_cv_filename, str) else []
    original_cv_name_display_raw = parts[1] if len(parts) > 1 else full_cv_filename

    if action_prefix == "review_accept" or action_prefix == "review_reject":
        new_status = "reviewed_accepted" if action_prefix == "review_accept" else "reviewed_declined"
        all_applications[target_app_index]['status'] = new_status
        all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
        all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)
        if save_applications(all_applications):
            logger.info(f"Application {full_cv_filename} status updated to {new_status} by user {query.from_user.id}.")
            status_message_verb = "Accepted" if new_status == "reviewed_accepted" else "Declined"
            unescaped_confirm_text = f"Application for {applicant_name_raw} (CV: {original_cv_name_display_raw}) status set to: {status_message_verb} by {query.from_user.first_name or 'N/A'}."
            final_confirm_text_for_edit = escape_markdown_v2(unescaped_confirm_text)
            try:
                if query.message: await query.edit_message_text(text=final_confirm_text_for_edit, reply_markup=None, parse_mode='MarkdownV2')
            except telegram.error.BadRequest as e:
                logger.error(f"Error editing message for {full_cv_filename} after {action_prefix}: {e}. Text was: {final_confirm_text_for_edit}", exc_info=True)
                unescaped_fallback_text = f"Application {original_cv_name_display_raw} status updated to {status_message_verb}. (Original message edit failed)."
                final_fallback_text = escape_markdown_v2(unescaped_fallback_text)
                await context.bot.send_message(chat_id=query.message.chat.id, text=final_fallback_text, parse_mode='MarkdownV2')
            except Exception as e:
                 logger.error(f"Unexpected error editing message for {full_cv_filename} ({action_prefix}): {e}", exc_info=True)
            if 'review_list' in context.user_data and context.user_data.get('current_view_status', 'new') == 'new':
                context.user_data['review_list'] = [app for app in context.user_data['review_list'] if app.get('cv_filename') != full_cv_filename]
                logger.info(f"Removed {full_cv_filename} from 'new' review session list for user {query.from_user.id}.")
        else:
            logger.error(f"Failed to save application status update for {full_cv_filename} ({action_prefix}).")
            if query.message: await query.edit_message_text("Error updating application status in log.", reply_markup=query.message.reply_markup if query.message else None)

    elif action_prefix == "change_status":
        final_new_status = ""
        if new_short_status_from_callback == "accepted": final_new_status = "reviewed_accepted"
        elif new_short_status_from_callback == "declined": final_new_status = "reviewed_declined"
        elif new_short_status_from_callback == "new": final_new_status = "new"
        else:
            logger.error(f"Unknown new_short_status '{new_short_status_from_callback}' for change_status on CV ID {app_id_from_callback} (Filename: {full_cv_filename}).")
            if query.message: await query.edit_message_text("Error: Invalid status change value.", reply_markup=None)
            return
        all_applications[target_app_index]['status'] = final_new_status
        raw_status_display_verb = ""
        if final_new_status == "new":
            all_applications[target_app_index].pop('reviewed_timestamp', None)
            all_applications[target_app_index].pop('reviewed_by', None)
            raw_status_display_verb = "Set to New"
        else:
            all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
            all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)
            action_verb = "Accepted" if final_new_status == "reviewed_accepted" else "Declined"
            raw_status_display_verb = f"{action_verb} by {query.from_user.first_name or 'N/A'}"
        if save_applications(all_applications):
            logger.info(f"Application {full_cv_filename} status changed to {final_new_status} by user {query.from_user.id}.")
            unescaped_confirm_text = f"Application for {applicant_name_raw} (CV: {original_cv_name_display_raw}) status changed to: {raw_status_display_verb}."
            final_confirm_text_for_edit = escape_markdown_v2(unescaped_confirm_text)
            try:
                if query.message:
                    await query.edit_message_text(text=final_confirm_text_for_edit, reply_markup=None, parse_mode='MarkdownV2')
            except telegram.error.BadRequest as e:
                logger.error(f"Error editing message for {full_cv_filename} (change_status): {e}. Text: {final_confirm_text_for_edit}", exc_info=True)
                unescaped_fallback_text = f"Status for {original_cv_name_display_raw} changed to {raw_status_display_verb}. (Original message edit failed)."
                final_fallback_text = escape_markdown_v2(unescaped_fallback_text)
                await context.bot.send_message(chat_id=query.message.chat.id, text=final_fallback_text, parse_mode='MarkdownV2')
            except Exception as e:
                 logger.error(f"Unexpected error editing message for {full_cv_filename} (change_status): {e}", exc_info=True)
            if 'review_list' in context.user_data and isinstance(context.user_data.get('review_list'), list):
                context.user_data['review_list'] = [app for app in context.user_data['review_list'] if app.get('cv_filename') != full_cv_filename]
                logger.info(f"Removed {full_cv_filename} from current status view list for user {query.from_user.id}.")
        else:
            logger.error(f"Failed to save application status update for {full_cv_filename} (change_status).")
            if query.message: await query.edit_message_text("Error updating application status in log.", reply_markup=query.message.reply_markup if query.message else None)

    elif action_prefix == "get_cv":
        cv_path = os.path.join(UPLOAD_FOLDER, full_cv_filename)
        if os.path.exists(cv_path):
            try:
                with open(cv_path, 'rb') as cv_doc:
                    await context.bot.send_document(chat_id=query.message.chat.id, document=cv_doc, filename=original_cv_name_display_raw)
                logger.info(f"Sent CV {full_cv_filename} as {original_cv_name_display_raw} to chat_id {query.message.chat.id}")
            except Exception as e:
                logger.error(f"Failed to send CV {full_cv_filename}: {e}", exc_info=True)
                unescaped_text = f"Sorry, could not send CV {original_cv_name_display_raw} for {applicant_name_raw}."
                final_text = escape_markdown_v2(unescaped_text)
                await context.bot.send_message(chat_id=query.message.chat.id, text=final_text, parse_mode='MarkdownV2')
        else:
            logger.warning(f"CV file {full_cv_filename} not found at path {cv_path} for get_cv action.")
            unescaped_text = f"Sorry, CV file {original_cv_name_display_raw} for {applicant_name_raw} not found on server."
            final_text = escape_markdown_v2(unescaped_text)
            await context.bot.send_message(chat_id=query.message.chat.id, text=final_text, parse_mode='MarkdownV2')

async def handle_next_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Next Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    review_list_size = len(context.user_data.get('review_list', []))
    total_pages = (review_list_size + APPS_PER_PAGE - 1) // APPS_PER_PAGE

    if page_num < total_pages - 1:
        context.user_data['review_page_num'] = page_num + 1
        # Determine which display function to call based on current_view_status
        current_status_view = context.user_data.get('current_view_status', 'new')
        if current_status_view == 'new':
            await display_application_page(update, context)
        else: # Assumes any other status means we are in the "archived" view
            await display_application_page_for_status_view(update, context)
    else:
        await update.message.reply_text("You are already on the last page.", reply_markup=review_mode_keyboard)

async def handle_previous_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Previous Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    if page_num > 0:
        context.user_data['review_page_num'] = page_num - 1
        # Determine which display function to call based on current_view_status
        current_status_view = context.user_data.get('current_view_status', 'new')
        if current_status_view == 'new':
            await display_application_page(update, context)
        else: # Assumes any other status means we are in the "archived" view
            await display_application_page_for_status_view(update, context)
    else:
        await update.message.reply_text("You are already on the first page.", reply_markup=review_mode_keyboard)

async def go_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context):
        return

    logger.info(f"Back to Main Menu requested by {update.effective_chat.id}")
    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)
    context.user_data.pop('current_view_status', None) # Clear this too
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
        view_accepted_apps_command
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^View Rejected Apps$"),
        view_rejected_apps_command
    ))

    logger.info("HR Bot starting...")
    application.run_polling() # Changed from await application.run_polling()
    logger.info("HR Bot has stopped.")

if __name__ == '__main__':
    # The platform-specific asyncio policy setting for Windows,
    # if present (e.g., asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())),
    # should remain at the very top of the script (after imports).
    main()

[end of hr_bot.py]
