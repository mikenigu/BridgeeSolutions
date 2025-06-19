import os
import json
import logging
from datetime import datetime
import asyncio
import re # Ensure re is imported

import platform
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

load_dotenv()

HR_BOT_TOKEN = os.getenv('HR_BOT_TOKEN')
HR_CHAT_ID = os.getenv('HR_CHAT_ID')

APPLICATION_LOG_FILE = 'submitted_applications.log.json'
UPLOAD_FOLDER = 'uploads/'
APPS_PER_PAGE = 3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Status Definitions ---
ALL_STATUSES = [
    'new',
    'reviewed_accepted',
    'interviewing',
    'offer_extended',
    'employed',
    'reviewed_declined',
    'offer_declined'
]

STATUS_DISPLAY_NAMES = {
    'new': 'New',
    'reviewed_accepted': 'Accepted (Pending Interview)',
    'interviewing': 'Interviewing',
    'offer_extended': 'Offer Extended',
    'employed': 'Employed',
    'reviewed_declined': 'Declined by Company',
    'offer_declined': 'Offer Declined by Candidate'
}

# --- Keyboards ---
main_menu_keyboard_layout = [
    ["Review New Applications"],
    ["View Accepted (Pending Interview)"],
    ["View Interviewing", "View Offer Extended"],
    ["View Employed"],
    ["View Declined by Company", "View Offer Declined by Candidate"],
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
        with open(APPLICATION_LOG_FILE, 'r', encoding='utf-8') as f:
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
        with open(APPLICATION_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(applications_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully saved {len(applications_data)} applications to {APPLICATION_LOG_FILE}")
        return True
    except IOError as e:
        logger.error(f"IOError writing to {APPLICATION_LOG_FILE}: {e}", exc_info=True)
        return False

def get_application_by_app_id(app_id: str, applications_data: list = None) -> tuple[dict | None, int]:
    if applications_data is None:
        applications_data = load_applications()
    search_prefix_for_loop = app_id + '-'
    for i, app in enumerate(applications_data):
        stored_cv_filename = app.get('cv_filename', '')
        if stored_cv_filename.startswith(search_prefix_for_loop):
            return app, i
    logger.warning(f"Application with app_id (timestamp prefix) '{app_id}' not found.")
    return None, -1

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2 parse mode."""
    if not isinstance(text, str):
        text = str(text)  # Ensure text is a string
    # Special characters for MarkdownV2: _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., !
    # This pattern matches any of these characters for substitution.
    pattern = r"([_*\[\]()~`>#+\-=|{}.!])"
    # The replacement prepends a backslash to the matched character.
    return re.sub(pattern, r"\\\1", text) # Corrected to \\1 for re.sub

# --- End Helper Functions ---

# --- Command Handlers ---
async def restricted_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id_str = str(update.effective_chat.id)
    if HR_CHAT_ID and chat_id_str != HR_CHAT_ID:
        unauthorized_message = "Sorry, you are not authorized to use this command."
        if update.message:
            await update.message.reply_text(unauthorized_message)
        elif update.callback_query:
             await update.callback_query.answer("Unauthorized", show_alert=True)
        logger.warning(f"Unauthorized access attempt by chat_id: {chat_id_str}")
        return False
    return True

async def start_review_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"Attempting to start review session for 'new' applications for chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, 'new', is_review_session=True)


async def start_view_specific_status_session(update: Update, context: ContextTypes.DEFAULT_TYPE, target_status: str, is_review_session: bool = False):
    if not await restricted_access(update, context): return
    logger.info(f"Starting specific status view session for status '{target_status}', chat_id: {update.effective_chat.id}")

    all_applications = load_applications()
    job_title_filter = None

    is_command_with_args = context.args and not is_review_session and update.message and not update.message.text.startswith("View")

    if is_command_with_args:
        job_title_filter = " ".join(context.args).strip().lower()
        if job_title_filter:
             logger.info(f"Filtering command-based status view for job title: '{escape_markdown_v2(job_title_filter)}'")

    apps_with_target_status = [
        app for app in all_applications
        if app.get('status') == target_status and
           (not job_title_filter or str(app.get('job_title', '')).lower() == job_title_filter)
    ]

    reply_target = update.effective_message
    if not reply_target and update.callback_query:
        reply_target = update.callback_query.message
    if not reply_target:
        logger.error(f"start_view_specific_status_session: No message context for reply. Chat ID: {update.effective_chat.id if update.effective_chat else 'Unknown'}")
        return

    status_display_name = STATUS_DISPLAY_NAMES.get(target_status, target_status.capitalize())

    if not apps_with_target_status:
        message_text = f"No applications found with status: {status_display_name}."
        if job_title_filter:
            message_text = f"No applications found for job title '{escape_markdown_v2(job_title_filter)}' with status: {status_display_name}."
        await reply_target.reply_text(message_text, reply_markup=main_menu_keyboard)
        context.user_data.pop('review_list', None)
        context.user_data.pop('review_page_num', None)
        context.user_data.pop('current_view_status', None)
        return

    context.user_data['review_list'] = sorted(apps_with_target_status, key=lambda x: x.get('timestamp', ''), reverse=True)
    context.user_data['review_page_num'] = 0
    context.user_data['current_view_status'] = target_status

    logger.info(f"Found {len(apps_with_target_status)} applications with status '{target_status}'. Starting session for chat_id {update.effective_chat.id}.")

    session_start_message = f"Viewing {len(apps_with_target_status)} application(s) with status: {status_display_name}. Use navigation buttons below."
    if is_review_session and target_status == 'new':
        session_start_message = f"Starting review of {len(apps_with_target_status)} new application(s). Use navigation buttons below."

    await reply_target.reply_text(session_start_message, reply_markup=review_mode_keyboard)

    await _display_application_page_common(update, context, "initial_view")


async def _display_application_page_common(update: Update, context: ContextTypes.DEFAULT_TYPE, page_type: str):
    logger.info(f"Attempting to display an application page for type: {page_type}")
    review_list = context.user_data.get('review_list', [])
    page_num = context.user_data.get('review_page_num', 0)
    current_view_status = context.user_data.get('current_view_status', 'N/A')
    chat_id = update.effective_chat.id

    reply_target = update.effective_message
    if not reply_target and update.callback_query:
        reply_target = update.callback_query.message

    if not review_list:
        logger.info(f"_display_application_page_common: no review_list for chat_id {chat_id}, type {page_type}.")
        msg_content = f"No applications to display in the current '{STATUS_DISPLAY_NAMES.get(current_view_status, current_view_status)}' view."
        await (reply_target.reply_text if reply_target else context.bot.send_message)(chat_id=chat_id, text=msg_content, reply_markup=main_menu_keyboard)
        return

    start_index = page_num * APPS_PER_PAGE
    end_index = start_index + APPS_PER_PAGE
    apps_on_page = review_list[start_index:end_index]

    if not apps_on_page:
        logger.info(f"No applications found for page {page_num} in chat {chat_id} (type {page_type}).")
        no_apps_message = f"You've reached the end of the '{STATUS_DISPLAY_NAMES.get(current_view_status, current_view_status)}' application list." if page_num > 0 else f"No '{STATUS_DISPLAY_NAMES.get(current_view_status, current_view_status)}' applications to display."
        await (reply_target.reply_text if reply_target else context.bot.send_message)(chat_id=chat_id, text=no_apps_message, reply_markup=review_mode_keyboard)
        return

    total_apps = len(review_list)
    total_pages = (total_apps + APPS_PER_PAGE - 1) // APPS_PER_PAGE
    page_summary_content = f"Displaying page {page_num + 1} of {total_pages} for '{STATUS_DISPLAY_NAMES.get(current_view_status, current_view_status)}' applications. ({start_index + 1}-{min(end_index, total_apps)} of {total_apps} total)."

    try:
        await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(page_summary_content), parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending page summary for {page_type} apps: {e}. Text: {escape_markdown_v2(page_summary_content)}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="Error displaying page summary. Continuing...")

    for app_data in apps_on_page:
        cv_filename_stored = app_data.get('cv_filename', 'N/A')
        app_id_for_callback = cv_filename_stored.split('-', 1)[0] if isinstance(cv_filename_stored, str) and '-' in cv_filename_stored else cv_filename_stored
        parts = cv_filename_stored.split('-', 1) if isinstance(cv_filename_stored, str) else []
        original_cv_name_for_display = parts[1] if len(parts) > 1 else cv_filename_stored

        app_actual_status = app_data.get('status', 'new')
        status_display_name = STATUS_DISPLAY_NAMES.get(app_actual_status, app_actual_status.capitalize())

        message_text = (
            f"*Status: {escape_markdown_v2(status_display_name)}*\n\n"
            f"*Name:* {escape_markdown_v2(app_data.get('full_name', 'N/A'))}\n"
            f"*Email:* {escape_markdown_v2(app_data.get('email', 'N/A'))}\n"
            f"*Job Title:* {escape_markdown_v2(str(app_data.get('job_title', 'N/A')))}\n"
        )

        cover_letter_text = app_data.get('cover_letter', '')
        if cover_letter_text and cover_letter_text.strip():
            snippet = cover_letter_text[:200]
            if len(cover_letter_text) > 200:
                snippet += "..."
            message_text += f"\n*Cover Letter Snippet:*\n{escape_markdown_v2(snippet)}\n"

        # Process submission timestamp
        submitted_ts_iso = app_data.get('timestamp')
        submitted_ts_display_formatted = "N/A" # Default
        if submitted_ts_iso:
            try:
                if submitted_ts_iso.endswith('Z'):
                    dt_submitted_obj = datetime.fromisoformat(submitted_ts_iso.replace('Z', '+00:00'))
                else:
                    dt_submitted_obj = datetime.fromisoformat(submitted_ts_iso)
                submitted_ts_display_formatted = escape_markdown_v2(dt_submitted_obj.strftime('%Y-%m-%d %H:%M'))
            except ValueError:
                logger.warning(f"Could not parse submission timestamp: {submitted_ts_iso} for app {cv_filename_stored}")
                submitted_ts_display_formatted = escape_markdown_v2(submitted_ts_iso) # Fallback

        message_text += (
            f"\n*Original CV Name:* {escape_markdown_v2(original_cv_name_for_display)}\n"
            f"*Submitted:* {submitted_ts_display_formatted}\n" # Use new formatted timestamp
        )

        reviewed_timestamp_raw = app_data.get('reviewed_timestamp')
        reviewed_by_id_raw = app_data.get('reviewed_by')
        reviewed_by_name_raw = app_data.get('reviewed_by_name', 'N/A')

        if reviewed_timestamp_raw:
            try:
                dt_object = datetime.fromisoformat(reviewed_timestamp_raw.replace('Z', '+00:00'))
                formatted_timestamp = escape_markdown_v2(dt_object.strftime('%Y-%m-%d %H:%M UTC'))
            except ValueError:
                logger.warning(f"Could not parse reviewed timestamp: {reviewed_timestamp_raw} for app {cv_filename_stored}")
                formatted_timestamp = escape_markdown_v2(reviewed_timestamp_raw) # Fallback

            actor_info_display = escape_markdown_v2(reviewed_by_name_raw)
            # Removed ID display: if reviewed_by_id_raw: actor_info_display += f" \\(ID: {escape_markdown_v2(str(reviewed_by_id_raw))}\\)"
            message_text += f"*Last Action:* {formatted_timestamp} by {actor_info_display}\n"

        keyboard_buttons = []
        if app_actual_status == 'new':
            keyboard_buttons.append([
                InlineKeyboardButton("Accept for Review", callback_data=f"set_status:accepted:{app_id_for_callback}"),
                InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_for_callback}")
            ])
        elif app_actual_status == 'reviewed_accepted':
            keyboard_buttons.append([
                InlineKeyboardButton("Start Interviewing", callback_data=f"set_status:interviewing:{app_id_for_callback}"),
                InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_for_callback}")
            ])
        elif app_actual_status == 'interviewing':
            keyboard_buttons.append([
                InlineKeyboardButton("Extend Offer", callback_data=f"set_status:offer_extended:{app_id_for_callback}"),
                InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_for_callback}")
            ])
        elif app_actual_status == 'offer_extended':
            keyboard_buttons.append([
                InlineKeyboardButton("Mark as Employed", callback_data=f"set_status:employed:{app_id_for_callback}"),
                InlineKeyboardButton("Offer Declined by Candidate", callback_data=f"set_status:offer_declined:{app_id_for_callback}")
            ])
        elif app_actual_status == 'reviewed_declined':
            keyboard_buttons.append([
                InlineKeyboardButton("Set as New (Undo Decline)", callback_data=f"set_status:new:{app_id_for_callback}"),
            ])
            keyboard_buttons.append([
                InlineKeyboardButton("Re-evaluate (Accept)", callback_data=f"set_status:accepted:{app_id_for_callback}")
            ])
        elif app_actual_status == 'offer_declined':
            keyboard_buttons.append([
                InlineKeyboardButton("Set as New (Undo Decline)", callback_data=f"set_status:new:{app_id_for_callback}")
            ])
            keyboard_buttons.append([
                InlineKeyboardButton("Re-evaluate (Accept)", callback_data=f"set_status:accepted:{app_id_for_callback}")
            ])

        if app_actual_status not in ['new', 'employed', 'reviewed_declined', 'offer_declined']:
             keyboard_buttons.append([InlineKeyboardButton("Set as New (Undo)", callback_data=f"set_status:new:{app_id_for_callback}")])

        # Removed "Get CV" button from here: keyboard_buttons.append([InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_for_callback}")])
        reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None # Ensure markup is None if no buttons

        try:
            await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')

            # Proactively send CV after sending the application details
            cv_path = os.path.join(UPLOAD_FOLDER, cv_filename_stored)
            if os.path.exists(cv_path):
                try:
                    with open(cv_path, 'rb') as cv_doc:
                        await context.bot.send_document(chat_id=chat_id, document=cv_doc, filename=original_cv_name_for_display)
                    logger.info(f"Proactively sent CV {cv_filename_stored} as {original_cv_name_for_display} to chat_id {chat_id} for application {app_id_for_callback}")
                except Exception as e_doc:
                    logger.error(f"Failed to proactively send CV {cv_filename_stored} for app {app_id_for_callback}: {e_doc}", exc_info=True)
                    await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(f"Could not send CV document ({original_cv_name_for_display}) for this applicant due to an error."), parse_mode='MarkdownV2')
            else:
                logger.warning(f"CV file {cv_filename_stored} not found at {cv_path} for proactive send (app {app_id_for_callback}).")
                await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(f"CV file ({original_cv_name_for_display}) not found on server for this applicant."), parse_mode='MarkdownV2')

        except Exception as e:
            logger.error(f"Error sending app details for {cv_filename_stored} (type {page_type}): {e}. Text: {message_text}", exc_info=True)
            error_content = f"Error displaying application: {app_data.get('full_name', 'N/A')} (CV: {cv_filename_stored})."
            await context.bot.send_message(chat_id=chat_id, text=escape_markdown_v2(error_content), parse_mode='MarkdownV2')

    logger.info(f"Displayed {len(apps_on_page)} applications on page {page_num + 1} for type '{page_type}', view_status '{current_view_status}' for chat_id {chat_id}.")

async def display_application_page_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _display_application_page_common(update, context, 'new_applications_review')

async def display_application_page_for_status_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _display_application_page_common(update, context, 'specific_status_view')


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
    logger.info(f"'View Accepted (Pending Interview)' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_accepted")

async def view_declined_company_apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"'View Declined by Company' triggered by chat_id: {update.effective_chat.id}")
    await start_view_specific_status_session(update, context, "reviewed_declined")

async def help_command_menu_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await restricted_access(update, context): return
    status_list_md = "\n".join([f"- *{escape_markdown_v2(name)}* (`{escape_markdown_v2(key)}`)" for key, name in STATUS_DISPLAY_NAMES.items()])

    help_text = (
        "Welcome to the HR Application Management Bot!\n\n"
        "**Main Menu Navigation:**\n"
        "Use the buttons on the main menu (type /start to see it) to view applications based on their status.\n\n"
        "**Application Statuses:**\n"
        "Applications move through the following statuses:\n"
        f"{status_list_md}\n\n"
        "**Managing Applications:**\n"
        "When you view a list of applications (e.g., 'Review New Applications' or 'View Interviewing'):\n"
        "- Each application will be displayed as a separate message.\n"
        "- Applicant details now include a 'Last Action' section, showing who made the most recent status change and when it occurred (formatted as YYYY-MM-DD HH:MM UTC by User Name (ID: UserID)).\n"
        "- A 200-character snippet of the cover letter is now shown with applicant details, if provided.\n"
        "- Below each application, you'll find inline buttons for actions relevant to its current status (e.g., 'Accept for Review', 'Start Interviewing', 'Extend Offer', 'Mark as Employed', 'Decline', etc.).\n"
        "- Click these buttons to change an applicant's status. The message will update to show the new status and relevant next actions.\n"
        "- For applications in 'Declined by Company' or 'Offer Declined by Candidate' statuses, you will now see buttons to 'Set as New (Undo Decline)' or 'Re-evaluate (Accept)' allowing you to move them back into an active review cycle.\n"
        "- The *Get CV* button allows you to download the applicant's CV.\n"
        "- Use the 'Previous Page' and 'Next Page' buttons (on the main keyboard, when active) to navigate through lists of applications.\n"
        "- 'Back to Main Menu' (on the main keyboard) will always take you back to the main selection menu.\n\n"
        "**Filtering by Job Title (using commands):**\n"
        "You can filter most views by appending a job title to the command, for example:\n"
        "- `/review_applications Full-Stack Developer`\n"
        "- `/view_interviewing UI/UX Designer`\n"
        "- `/view_employed Virtual Assistant`\n\n"
        "Type /stop to hide the main menu keyboard if needed."
    )
    reply_target = update.effective_message
    if not reply_target and update.callback_query:
        reply_target = update.callback_query.message

    await reply_target.reply_text(help_text, parse_mode='MarkdownV2', reply_markup=main_menu_keyboard)


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    callback_parts = query.data.split(":", 2)
    action_prefix = callback_parts[0]

    logger.info(f"Callback received. Raw data: '{query.data}' by user {query.from_user.id}")

    app_id_from_callback = None
    new_short_status_key = None

    # Removed get_cv block
    if action_prefix == "set_status": # Adjusted from elif to if
        if len(callback_parts) == 3:
            new_short_status_key = callback_parts[1]
            app_id_from_callback = callback_parts[2]
        else:
            logger.error(f"Invalid format for set_status: {query.data}")
            if query.message: await query.edit_message_text("Error: Invalid status change format.")
            return
    else:
        logger.warning(f"Unknown callback action_prefix: {action_prefix} from data: {query.data}")
        if query.message: await query.edit_message_text("Unknown action.", reply_markup=None)
        return

    if not app_id_from_callback:
        logger.error(f"App ID could not be parsed from callback data: {query.data}")
        if query.message: await query.edit_message_text("Error processing action: App ID missing.")
        return

    logger.info(f"Parsed Callback: action='{action_prefix}', app_id='{app_id_from_callback}', new_short_status_key='{new_short_status_key or 'N/A'}'")

    all_applications = load_applications()
    target_app_obj, target_app_index = get_application_by_app_id(app_id_from_callback, all_applications)

    if not target_app_obj:
        logger.warning(f"Application NOT FOUND. App_id from callback: '{app_id_from_callback}'.")
        if query.message: await query.edit_message_text(text="Error: Application not found. It might have been processed or an ID error occurred.", reply_markup=None)
        return

    full_cv_filename = target_app_obj.get('cv_filename')
    original_cv_name_display_raw = full_cv_filename.split('-', 1)[1] if isinstance(full_cv_filename, str) and '-' in full_cv_filename else full_cv_filename

    # The get_cv block was here and has been removed.

    if action_prefix == "set_status":
        status_map = {
            'accepted': 'reviewed_accepted', 'interviewing': 'interviewing',
            'offer_extended': 'offer_extended', 'employed': 'employed',
            'declined_company': 'reviewed_declined', 'offer_declined': 'offer_declined',
            'new': 'new'
        }
        final_new_status = status_map.get(new_short_status_key)

        if not final_new_status:
            logger.error(f"Invalid short status key '{new_short_status_key}' for app {app_id_from_callback}.")
            if query.message: await query.edit_message_text("Error: Invalid status value.", reply_markup=None)
            return

        all_applications[target_app_index]['status'] = final_new_status
        status_display_name_for_confirmation = STATUS_DISPLAY_NAMES.get(final_new_status, final_new_status)

        if final_new_status == 'new':
            all_applications[target_app_index].pop('reviewed_timestamp', None)
            all_applications[target_app_index].pop('reviewed_by', None)
            all_applications[target_app_index].pop('reviewed_by_name', None)
        else:
            all_applications[target_app_index]['reviewed_timestamp'] = datetime.utcnow().isoformat() + 'Z'
            all_applications[target_app_index]['reviewed_by'] = str(query.from_user.id)
            all_applications[target_app_index]['reviewed_by_name'] = query.from_user.first_name or query.from_user.username or 'N/A'

        if save_applications(all_applications):
            logger.info(f"Application {full_cv_filename} status updated to {final_new_status} by user {query.from_user.id} ({all_applications[target_app_index].get('reviewed_by_name', 'N/A')}).")

            updated_app_data = all_applications[target_app_index]
            status_display_name_updated = STATUS_DISPLAY_NAMES.get(updated_app_data.get('status', 'N/A'), "N/A")

            message_text_updated = (
                f"*Status: {escape_markdown_v2(status_display_name_updated)}*\n\n"
                f"*Name:* {escape_markdown_v2(updated_app_data.get('full_name', 'N/A'))}\n"
                f"*Email:* {escape_markdown_v2(updated_app_data.get('email', 'N/A'))}\n"
                f"*Job Title:* {escape_markdown_v2(str(updated_app_data.get('job_title', 'N/A')))}\n"
            )
            cover_letter_raw_updated = updated_app_data.get('cover_letter', '')
            if cover_letter_raw_updated and cover_letter_raw_updated.strip():
                snippet_updated = cover_letter_raw_updated[:200]
                if len(cover_letter_raw_updated) > 200:
                    snippet_updated += "..."
                message_text_updated += f"\n*Cover Letter Snippet:*\n{escape_markdown_v2(snippet_updated)}\n"
            message_text_updated += (
                f"\n*Original CV Name:* {escape_markdown_v2(original_cv_name_display_raw)}\n"
                f"*Submitted:* {escape_markdown_v2(str(updated_app_data.get('timestamp', 'N/A')))}\n"
            )
            reviewed_timestamp_raw_updated = updated_app_data.get('reviewed_timestamp')
            reviewed_by_id_updated = updated_app_data.get('reviewed_by')
            reviewed_by_name_updated = updated_app_data.get('reviewed_by_name', 'N/A')

            if reviewed_timestamp_raw_updated:
                try:
                    dt_object = datetime.fromisoformat(reviewed_timestamp_raw_updated.replace('Z', '+00:00'))
                    formatted_timestamp = escape_markdown_v2(dt_object.strftime('%Y-%m-%d %H:%M UTC'))
                except ValueError:
                    logger.warning(f"Could not parse reviewed timestamp (updated): {reviewed_timestamp_raw_updated} for app {full_cv_filename}")
                    formatted_timestamp = escape_markdown_v2(reviewed_timestamp_raw_updated) # Fallback

                actor_info = escape_markdown_v2(reviewed_by_name_updated)
                # Removed ID display: if reviewed_by_id_updated: actor_info += f" \\(ID: {escape_markdown_v2(str(reviewed_by_id_updated))}\\)"
                message_text_updated += f"*Last Action:* {formatted_timestamp} by {actor_info}\n"

            keyboard_buttons_updated = []
            current_status_updated = updated_app_data.get('status')

            if current_status_updated == 'new':
                 keyboard_buttons_updated.append([
                    InlineKeyboardButton("Accept for Review", callback_data=f"set_status:accepted:{app_id_from_callback}"),
                    InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_from_callback}")
                 ])
            elif current_status_updated == 'reviewed_accepted':
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Start Interviewing", callback_data=f"set_status:interviewing:{app_id_from_callback}"),
                    InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_from_callback}")
                ])
            elif current_status_updated == 'interviewing':
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Extend Offer", callback_data=f"set_status:offer_extended:{app_id_from_callback}"),
                    InlineKeyboardButton("Decline", callback_data=f"set_status:declined_company:{app_id_from_callback}")
                ])
            elif current_status_updated == 'offer_extended':
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Mark as Employed", callback_data=f"set_status:employed:{app_id_from_callback}"),
                    InlineKeyboardButton("Offer Declined by Candidate", callback_data=f"set_status:offer_declined:{app_id_from_callback}")
                ])
            elif current_status_updated == 'reviewed_declined':
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Set as New (Undo Decline)", callback_data=f"set_status:new:{app_id_from_callback}"),
                ])
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Re-evaluate (Accept)", callback_data=f"set_status:accepted:{app_id_from_callback}")
                ])
            elif current_status_updated == 'offer_declined':
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Set as New (Undo Decline)", callback_data=f"set_status:new:{app_id_from_callback}")
                ])
                keyboard_buttons_updated.append([
                    InlineKeyboardButton("Re-evaluate (Accept)", callback_data=f"set_status:accepted:{app_id_from_callback}")
                ])

            if current_status_updated not in ['new', 'employed', 'reviewed_declined', 'offer_declined']:
                 keyboard_buttons_updated.append([InlineKeyboardButton("Set as New (Undo)", callback_data=f"set_status:new:{app_id_from_callback}")])

            # Removed "Get CV" button from here as well for consistency, CV is sent proactively.
            # keyboard_buttons_updated.append([InlineKeyboardButton("Get CV", callback_data=f"get_cv:{app_id_from_callback}")])
            reply_markup_updated = InlineKeyboardMarkup(keyboard_buttons_updated) if keyboard_buttons_updated else None

            try:
                if query.message:
                    await query.edit_message_text(text=message_text_updated, reply_markup=reply_markup_updated, parse_mode='MarkdownV2')
                await query.answer(text=f"Status updated to: {status_display_name_for_confirmation}")
            except telegram.error.BadRequest as e:
                logger.info(f"Message not modified (likely content identical), or other minor error: {e}. Answering callback.")
                await query.answer(text=f"Status is now: {status_display_name_for_confirmation}")
            except Exception as e:
                 logger.error(f"Unexpected error editing message for {full_cv_filename} (set_status): {e}", exc_info=True)
                 await query.answer(text="Error updating display. Status was changed.", show_alert=True)

            current_session_view_status = context.user_data.get('current_view_status')
            if 'review_list' in context.user_data and current_session_view_status and current_session_view_status != final_new_status:
                if not (current_session_view_status == 'new' and final_new_status == 'reviewed_accepted') and \
                   not (current_session_view_status == 'new' and final_new_status == 'reviewed_declined'):
                    context.user_data['review_list'] = [app for app in context.user_data['review_list'] if app.get('cv_filename') != full_cv_filename]
                    logger.info(f"Removed {full_cv_filename} from current view list ({current_session_view_status}) as status changed to '{final_new_status}'.")
        else:
            logger.error(f"Failed to save application status update for {full_cv_filename} (set_status).")
            if query.message: await query.edit_message_text("Error updating application status in log.", reply_markup=query.message.reply_markup if query.message else None)

    # The elif action_prefix == "get_cv" block was here and has been completely removed.
    # CVs are now sent proactively by _display_application_page_common.

    await query.answer() # This needs to be called for all callback queries handled.

async def handle_next_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"Next Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    review_list_size = len(context.user_data.get('review_list', []))
    total_pages = (review_list_size + APPS_PER_PAGE - 1) // APPS_PER_PAGE

    if page_num < total_pages - 1:
        context.user_data['review_page_num'] = page_num + 1
        current_status_view = context.user_data.get('current_view_status', 'new')
        if current_status_view == 'new': await display_application_page_new(update, context)
        else: await display_application_page_for_status_view(update, context)
    else:
        await update.message.reply_text("You are already on the last page.", reply_markup=review_mode_keyboard)

async def handle_previous_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"Previous Page requested by {update.effective_chat.id}")
    page_num = context.user_data.get('review_page_num', 0)
    if page_num > 0:
        context.user_data['review_page_num'] = page_num - 1
        current_status_view = context.user_data.get('current_view_status', 'new')
        if current_status_view == 'new': await display_application_page_new(update, context)
        else: await display_application_page_for_status_view(update, context)
    else:
        await update.message.reply_text("You are already on the first page.", reply_markup=review_mode_keyboard)

async def go_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted_access(update, context): return
    logger.info(f"Back to Main Menu requested by {update.effective_chat.id}")
    context.user_data.pop('review_list', None)
    context.user_data.pop('review_page_num', None)
    context.user_data.pop('current_view_status', None)
    await update.message.reply_text("Returning to the main menu.", reply_markup=main_menu_keyboard)

# --- End Command Handlers ---

def main():
    if not HR_BOT_TOKEN:
        logger.error("HR_BOT_TOKEN not found in environment variables.")
        return
    if not HR_CHAT_ID:
        logger.warning("HR_CHAT_ID not found. Bot commands will NOT be restricted.")

    application = ApplicationBuilder().token(HR_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("review_applications", review_applications_command))
    application.add_handler(CommandHandler("view_accepted", lambda u,c: start_view_specific_status_session(u,c,"reviewed_accepted")))
    application.add_handler(CommandHandler("view_interviewing", lambda u,c: start_view_specific_status_session(u,c,"interviewing")))
    application.add_handler(CommandHandler("view_offer_extended", lambda u,c: start_view_specific_status_session(u,c,"offer_extended")))
    application.add_handler(CommandHandler("view_employed", lambda u,c: start_view_specific_status_session(u,c,"employed")))
    application.add_handler(CommandHandler("view_declined_company", lambda u,c: start_view_specific_status_session(u,c,"reviewed_declined")))
    application.add_handler(CommandHandler("view_offer_declined", lambda u,c: start_view_specific_status_session(u,c,"offer_declined")))
    application.add_handler(CommandHandler("help", help_command_menu_entry))

    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # MessageHandlers for main menu buttons
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Review New Applications$"), start_review_session))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Accepted \(Pending Interview\)$"), view_accepted_apps_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Declined by Company$"), view_declined_company_apps_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Interviewing$"), lambda u, c: start_view_specific_status_session(u, c, "interviewing")))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Offer Extended$"), lambda u, c: start_view_specific_status_session(u, c, "offer_extended")))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Employed$"), lambda u, c: start_view_specific_status_session(u, c, "employed")))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^View Offer Declined by Candidate$"), lambda u, c: start_view_specific_status_session(u, c, "offer_declined")))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Help$"), help_command_menu_entry ))

    # MessageHandlers for pagination
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Next Page$"),handle_next_page))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Previous Page$"),handle_previous_page))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Back to Main Menu$"),go_to_main_menu))

    logger.info("HR Bot starting...")
    application.run_polling()
    logger.info("HR Bot has stopped.")

if __name__ == '__main__':
    main()

[end of hr_bot.py]
