import os
import json
import logging
from datetime import datetime
import uuid # For generating unique post IDs

from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence
)
import asyncio # Ensure asyncio is imported

# Load environment variables from .env file
load_dotenv()

# Basic logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration Variables ---
BLOG_BOT_TOKEN = os.getenv('BLOG_BOT_TOKEN')
BLOG_ADMIN_CHAT_ID = os.getenv('BLOG_ADMIN_CHAT_ID')
BLOG_POSTS_FILE = 'blog_posts.json'

# Conversation states for /newpost
TITLE, CONTENT_CHOICE, RECEIVE_TYPED_CONTENT, RECEIVE_CONTENT_FILE, AUTHOR, IMAGE_URL = range(6)

# Conversation states for /editpost
SELECT_POST_TO_EDIT, SELECT_FIELD_TO_EDIT, GET_NEW_FIELD_VALUE = range(6, 9)

# --- Constants ---
POSTS_PER_PAGE = 3

# --- Helper Functions ---
def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram MarkdownV2."""
    if text is None:
        return ""
    text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f'\\{char}' if char in escape_chars else char for char in text)

def load_blog_posts() -> list:
    if not os.path.exists(BLOG_POSTS_FILE):
        logger.info(f"{BLOG_POSTS_FILE} not found. Returning empty list.")
        return []
    try:
        with open(BLOG_POSTS_FILE, 'r', encoding='utf-8') as f:
            file_content = f.read()
            if not file_content:
                logger.info(f"{BLOG_POSTS_FILE} is empty. Returning empty list.")
                return []
            posts_data = json.loads(file_content)
            if not isinstance(posts_data, list):
                logger.warning(f"Data in {BLOG_POSTS_FILE} is not a list. Returning empty list.")
                return []
            return posts_data
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {BLOG_POSTS_FILE}. Returning empty list.", exc_info=True)
        return []
    except IOError as e:
        logger.error(f"IOError reading {BLOG_POSTS_FILE}: {e}. Returning empty list.", exc_info=True)
        return []

def save_blog_posts(posts_data: list) -> bool:
    try:
        with open(BLOG_POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully saved {len(posts_data)} posts to {BLOG_POSTS_FILE}")
        return True
    except IOError as e:
        logger.error(f"IOError writing to {BLOG_POSTS_FILE}: {e}", exc_info=True)
        return False

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("BLOG_ADMIN_CHAT_ID is not set. Access control is disabled.")
        if update.effective_message:
            await update.effective_message.reply_text("Warning: Bot admin chat ID is not configured. Access is open.")
        return True

    if str(update.effective_chat.id) == BLOG_ADMIN_CHAT_ID:
        return True
    else:
        if update.effective_message:
            await update.effective_message.reply_text("Sorry, you are not authorized to use this command.")
        logger.warning(f"Unauthorized access attempt by chat_id: {update.effective_chat.id}")
        return False

# --- /newpost Conversation Handler Functions ---
async def newpost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update, context):
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data['new_post'] = {}

    message_text = "Let's create a new blog post! First, what's the title of the post?"
    if update.callback_query:
        await update.callback_query.message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    return TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text
    context.user_data['new_post']['title'] = title

    keyboard = [
        [InlineKeyboardButton("Type Content Directly", callback_data='content_type_direct')],
        [InlineKeyboardButton("Upload Content File (.txt, .md)", callback_data='content_type_file')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Great! Title set to: '{escape_markdown_v2(str(title))}'\\.\n\nHow would you like to provide the content for your post? \\(Max 1MB for files, UTF\\-8 format preferred for files\\)",
        reply_markup=reply_markup,
        parse_mode='MarkdownV2'
    )
    return CONTENT_CHOICE

async def handle_content_type_direct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    text_to_send = "Okay, please send the full content of the blog post as a text message\\. Remember Telegram has a character limit for messages\\. \\(Or type /cancel\\)"
    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{text_to_send}<<<")
    await query.edit_message_text(
        text=text_to_send,
        parse_mode='MarkdownV2'
    )
    return RECEIVE_TYPED_CONTENT

async def handle_content_type_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    text_to_send = "Please upload a plain text file \\(\\.txt\\) or a Markdown file \\(\\.md\\) containing the blog content\\. Max file size: 1MB, UTF\\-8 encoded\\. \\(Or type /cancel\\)"
    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{text_to_send}<<<")
    await query.edit_message_text(
        text=text_to_send,
        parse_mode='MarkdownV2'
    )
    return RECEIVE_CONTENT_FILE

async def receive_typed_content_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    content = update.message.text
    context.user_data['new_post']['content'] = content
    await update.message.reply_text("Content received\\. Who is the author? \\(Type 'skip' or /cancel\\)", parse_mode='MarkdownV2')
    return AUTHOR

async def receive_content_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if doc.file_size > 1 * 1024 * 1024: # Max 1MB
        await update.message.reply_text("File is too large \\(max 1MB\\)\\. Please upload a smaller file or type /cancel\\.", parse_mode='MarkdownV2')
        return RECEIVE_CONTENT_FILE

    try:
        file = await context.bot.get_file(doc.file_id)
        file_content_bytes = await file.download_as_bytearray()
        file_content_text = file_content_bytes.decode('utf-8')
        context.user_data['new_post']['content'] = file_content_text
        await update.message.reply_text("Content file received and processed successfully\\. Who is the author? \\(Type 'skip' or /cancel\\)", parse_mode='MarkdownV2')
        return AUTHOR
    except UnicodeDecodeError:
        await update.message.reply_text("Error: Could not decode the file\\. Please ensure it is a UTF\\-8 encoded text file\\. Upload again or type /cancel\\.", parse_mode='MarkdownV2')
        return RECEIVE_CONTENT_FILE
    except Exception as e:
        logger.error(f"Error processing uploaded file: {e}", exc_info=True)
        await update.message.reply_text("An error occurred while processing your file\\. Please try again or type /cancel\\.", parse_mode='MarkdownV2')
        return RECEIVE_CONTENT_FILE

async def handle_unexpected_message_in_file_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Please upload a content file as requested, or type /cancel to exit\\.")
    return RECEIVE_CONTENT_FILE

async def unexpected_text_in_content_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Please choose an option by clicking one of the buttons above, or type /cancel\\.")
    return CONTENT_CHOICE

async def receive_author(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    author = update.message.text
    if author.lower() != 'skip':
        context.user_data['new_post']['author'] = author
    else:
        context.user_data['new_post']['author'] = None
    await update.message.reply_text("Author noted\\.\n\nPlease provide a URL for the post's image\\. \\(Type 'skip' if no image\\)", parse_mode='MarkdownV2')
    return IMAGE_URL

async def receive_image_url_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    image_url = update.message.text
    if image_url.lower() != 'skip':
        context.user_data['new_post']['image_url'] = image_url
    else:
        context.user_data['new_post']['image_url'] = None

    post_data = context.user_data['new_post']
    post_data['id'] = str(uuid.uuid4())
    post_data['date_published'] = datetime.utcnow().isoformat() + 'Z'
    post_data.setdefault('title', 'Untitled Post')
    post_data.setdefault('content', '')
    post_data.setdefault('author', None)
    post_data.setdefault('image_url', None)

    posts = load_blog_posts()
    posts.append(post_data)

    if save_blog_posts(posts):
        await update.message.reply_text(f"Blog post '{escape_markdown_v2(str(post_data['title']))}' successfully saved with ID: {escape_markdown_v2(str(post_data['id']))}!", parse_mode='MarkdownV2')
    else:
        await update.message.reply_text("Error: Could not save the blog post to the file.")

    context.user_data.pop('new_post', None)
    await start_command(update, context)
    return ConversationHandler.END

async def cancel_newpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('new_post', None)
    if update.callback_query:
        await update.callback_query.edit_message_text("New post creation cancelled.", reply_markup=None)
    else:
        await update.effective_message.reply_text("New post creation cancelled.", reply_markup=ReplyKeyboardRemove())

    await start_command(update, context)
    return ConversationHandler.END

async def cancel_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current post editing process and returns to the main menu."""
    message_text = "Editing cancelled."

    # Clear all session data related to post editing and selection
    context.user_data.pop('edit_post_data', None)
    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    # These might also be good to clear, similar to start_command
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('current_action_type', None)

    if update.callback_query:
        await update.callback_query.answer() # Answer callback query
        try:
            # Try to edit the message the inline keyboard was attached to
            await update.callback_query.edit_message_text(text=message_text, reply_markup=None)
        except Exception as e:
            logger.info(f"Could not edit message for cancel_editing, sending new one: {e}")
            # Fallback: Send a new message if editing fails (e.g., message too old)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, reply_markup=None)
    elif update.message:
        # If cancelled via a /command
        await update.message.reply_text(text=message_text, reply_markup=ReplyKeyboardRemove())
    else:
        # Fallback if neither (should not typically happen in a ConversationHandler context)
        # Ensure effective_chat.id is available
        if update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, reply_markup=ReplyKeyboardRemove())
        else:
            logger.warning("cancel_editing called without effective_chat_id")


    # Navigate back to the main menu
    # start_command itself will clean up most user_data and show the main menu
    await start_command(update, context)
    return ConversationHandler.END

# --- Start and Help Commands (and their Callbacks) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return

    keyboard = [
        [InlineKeyboardButton("➕ Add New Post", callback_data='menu_new_post')],
        [InlineKeyboardButton("📄 List All Posts", callback_data='list_posts_page:0')],
        [InlineKeyboardButton("⚙️ Manage Posts", callback_data='manage_posts_init:0')], # Updated
        [InlineKeyboardButton("❓ Help", callback_data='menu_help')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data.pop('current_action_type', None)
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    context.user_data.pop('edit_post_data', None)
    context.user_data.pop('new_post', None)

    message_text = "Welcome, Blog Admin! I'm here to help you manage your website's blog posts. Use the menu below to get started or type /help for a list of commands."
    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_text(text=message_text, reply_markup=reply_markup)
        except Exception as e:
            logger.info(f"Could not edit message for main menu, sending new one: {e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text=message_text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text=message_text, reply_markup=reply_markup )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    help_text = (
        "I'm here to help you manage your website's blog posts! "
        "Use the main menu buttons (shown with /start) to perform actions.\n\n"
        "Here's how to use the main menu options:\n\n"
        "🔹 <b>Main Menu Navigation:</b>\n"
        "   - Use the buttons on the main menu to start an action.\n"
        "   - In paginated lists (like when choosing a post or viewing all posts), use '⬅️ Previous' and '➡️ Next' buttons to navigate, and '🏠 Main Menu' to return.\n\n"
        "🔹 <b>Menu Options:</b>\n"
        "   - <b>➕ Add New Post</b>: Starts the process to create a new blog post.\n"
        "     1. I'll ask for the title first.\n"
        "     2. Then, you can choose to <b>Type Content Directly</b> or <b>Upload a Content File</b>.\n"
        "        (Accepted files: .txt, .md, UTF-8 encoded, max 1MB).\n"
        "     3. Finally, I'll ask for an author (optional) and an image URL (optional).\n\n"

        "   - <b>📄 List All Posts</b>: Shows a paginated view of all your blog posts, including their titles, dates, IDs, and short content snippets for quick reference.\n\n"

        "   - <b>⚙️ Manage Posts</b>: Allows you to edit or delete an existing blog post.\n"
        "     1. I'll show you a paginated list of your posts.\n"
        "     2. Press '[Select]' next to the post you want to manage.\n"
        "     3. After selection, new buttons will appear: \n"
        "        - '[✏️ Edit Details]' to change the post's title, content, etc.\n"
        "        - '[🗑️ Delete Post]' to remove the post (you'll get a final confirmation).\n"
        "        - '[↩️ Choose Different Post]' to go back to the list.\n"
        "        - '[🏠 Main Menu]' to return to the main menu.\n\n"

        "🔹 <b>Cancelling an Operation:</b>\n"
        "   - If you start an operation (like adding or editing a post) and want to stop, type /cancel. This will take you back to the main menu.\n\n"
        "Type /start at any time to see the main menu."
    )
    effective_message = update.effective_message
    if update.callback_query:
        effective_message = update.callback_query.message

    await effective_message.reply_text(help_text, parse_mode='HTML')

# --- Orphaned Command Handlers (Kept for reference, can be removed later) ---
async def listposts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass
async def deletepost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass
async def deletepost_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: pass
async def editpost_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: return ConversationHandler.END
async def receive_post_id_for_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: return SELECT_FIELD_TO_EDIT

# --- Menu Callback Handlers ---
async def handle_menu_new_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['new_post'] = {}
    await query.edit_message_text(text="Let's create a new blog post! First, please send me the title for the post. (Or type /cancel to go back to the main menu.)", reply_markup=None)
    return TITLE

async def handle_menu_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        # Try to edit the message to "Loading help..." then call help_command to replace it
        await query.edit_message_text(text="Loading help...", reply_markup=None)
        await help_command(update, context)
    except Exception as e:
        logger.error(f"Error editing message for help in handle_menu_help_callback: {e}")
        # Fallback: Send new messages if edit fails
        await context.bot.send_message(chat_id=query.message.chat_id, text="Please see help text below:")
        # Create a dummy update object for help_command if original update.message is None
        dummy_update = Update(update_id=update.update_id, message=query.message) if not update.message else update
        await help_command(dummy_update, context)


# --- Post Selection and Paginated Display Functions ---
async def initiate_post_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    action = 'manage' # Default action for this flow
    page_str = '0'

    try:
        # query.data could be 'manage_posts_init:0' or 'select_post_init:manage:0'
        parts = query.data.split(':')
        if parts[0] == 'manage_posts_init':
            page_str = parts[1]
        elif parts[0] == 'select_post_init': # Legacy or alternative entry
            action = parts[1] # Should be 'manage' if coming from new prompt_action_for_selected_post
            page_str = parts[2]
        page_num = int(page_str)
    except (ValueError, IndexError) as e:
        logger.error(f"Invalid callback data for initiate_post_selection_callback: {query.data} - {e}")
        await query.edit_message_text(text="Error: Invalid action parameters. Returning to main menu.")
        await start_command(update, context)
        return

    context.user_data['current_action_type'] = 'manage' # Force to 'manage' for this flow
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data['current_page_num'] = page_num

    await display_post_selection_page(update, context, page_num)

async def display_post_selection_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page_num: int) -> None:
    query = update.callback_query

    action = context.user_data.get('current_action_type', 'manage') # Default to 'manage'

    if 'paginated_posts_cache' not in context.user_data or page_num == 0: # Always refresh on page 0 for this flow
        all_posts = load_blog_posts()
        if not all_posts:
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
            raw_message_text = f"There are no posts to {str(action)}. Would you like to create one?"
            message_text_escaped = escape_markdown_v2(raw_message_text)
            # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text_escaped}<<<")
            await query.edit_message_text(
                text=message_text_escaped,
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2'
            )
            return
        context.user_data['paginated_posts_cache'] = sorted(
            [{'id': p['id'], 'title': p.get('title', 'No Title'), 'date_published': p.get('date_published', '')} for p in all_posts],
            key=lambda x: x.get('date_published', ''),
            reverse=True
        )

    cached_posts = context.user_data.get('paginated_posts_cache', [])
    if not cached_posts:
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
        raw_message_text = f"There are no posts to {str(action)}. Would you like to create one?"
        message_text_escaped = escape_markdown_v2(raw_message_text)
        # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text_escaped}<<<")
        await query.edit_message_text(
            text=message_text_escaped,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='MarkdownV2'
        )
        return

    total_posts = len(cached_posts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    page_num = max(0, min(page_num, total_pages - 1))
    context.user_data['current_page_num'] = page_num

    start_index = page_num * POSTS_PER_PAGE
    end_index = start_index + POSTS_PER_PAGE
    posts_on_page = cached_posts[start_index:end_index]

    # Updated prompt for "manage" action
    message_text = f"Select a post to manage \\(Page {page_num + 1}/{total_pages}\\):\n\n"
    keyboard_buttons = []

    for post in posts_on_page:
        escaped_post_title = escape_markdown_v2(str(post.get('title', 'No Title')))
        escaped_post_id = escape_markdown_v2(str(post['id']))
        display_title = escaped_post_title

        date_str = post.get('date_published', '')
        if date_str:
            try:
                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                escaped_formatted_date = escape_markdown_v2(str(date_obj.strftime("%Y-%m-%d")))
                display_title = f"{escaped_post_title} \\({escaped_formatted_date}\\)"
            except ValueError:
                pass

        message_text += f"*{display_title}*\nID: `{escaped_post_id}`\n\n"
        # Callback data now uses 'manage' as the action
        keyboard_buttons.append([InlineKeyboardButton(f"Select: {str(post.get('title', 'No Title'))[:30]}...", callback_data=f"post_selected:{post['id']}:manage")])

    pagination_row = []
    # Pagination callbacks also use 'manage' as the action
    if page_num > 0:
        pagination_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"select_post_page:manage:{page_num - 1}"))
    if page_num < total_pages - 1:
        pagination_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"select_post_page:manage:{page_num + 1}"))

    if pagination_row:
        keyboard_buttons.append(pagination_row)
    keyboard_buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    try:
        # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending paginated message (MarkdownV2 failed, trying plain): {e}")
        plain_message_text = f"Select a post to manage (Page {page_num + 1}/{total_pages}):\n\n" # Updated plain text
        for post_item in posts_on_page:
            title = str(post_item.get('title', 'No Title'))
            date_str_plain = post_item.get('date_published', '')
            display_title_plain = title
            if date_str_plain:
                try:
                    date_obj_plain = datetime.fromisoformat(date_str_plain.replace('Z', '+00:00'))
                    formatted_date_plain = date_obj_plain.strftime("%Y-%m-%d")
                    display_title_plain = f"{title} ({formatted_date_plain})"
                except ValueError:
                    pass
            plain_message_text += f"{display_title_plain}\nID: {str(post_item['id'])}\n\n"
        await query.edit_message_text(text=plain_message_text, reply_markup=reply_markup)

async def handle_select_post_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, action_from_callback, page_str = query.data.split(':') # action could be 'manage'
        page_num = int(page_str)
    except ValueError:
        logger.error(f"Invalid callback data for handle_select_post_page_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid pagination parameters. Returning to main menu.")
        await start_command(update, context)
        return

    # Ensure current_action_type is set, default to 'manage' if coming from this flow
    # This also makes sure the action type is consistent for display_post_selection_page
    context.user_data['current_action_type'] = action_from_callback

    if 'paginated_posts_cache' not in context.user_data:
        logger.info("paginated_posts_cache is missing in handle_select_post_page_callback. Re-initializing.")
        # Re-trigger the selection process, which will use current_action_type
        # Need to form the correct callback data for initiate_post_selection_callback
        # This part is tricky, as initiate_post_selection_callback expects specific patterns.
        # For simplicity, if cache is lost, we go to page 0 of the current action.
        await display_post_selection_page(update, context, 0)
        return

    context.user_data['current_page_num'] = page_num
    await display_post_selection_page(update, context, page_num)

async def handle_post_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, post_uuid, action_from_callback = query.data.split(':', 2)
    except ValueError:
        logger.error(f"Invalid callback data for handle_post_selection_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid selection parameters. Returning to main menu.")
        await start_command(update, context)
        return

    all_posts = load_blog_posts()
    selected_post_obj = next((post for post in all_posts if post.get('id') == post_uuid), None)

    if not selected_post_obj:
        logger.error(f"Post with UUID {post_uuid} not found during selection.")
        await query.edit_message_text(text="Error: Selected post not found. It might have been deleted. Returning to post selection.")
        context.user_data.pop('paginated_posts_cache', None)
        # Fallback to the current action type if available, else 'manage'
        fallback_action = context.user_data.get('current_action_type', 'manage')
        context.user_data['current_action_type'] = fallback_action
        await display_post_selection_page(update, context, page_num=0)
        return

    context.user_data['selected_post_uuid'] = post_uuid
    context.user_data['selected_post_full_data'] = dict(selected_post_obj)
    context.user_data['current_action_type'] = action_from_callback # This will be 'manage'

    await prompt_action_for_selected_post(update, context)

async def prompt_action_for_selected_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    selected_post_data = context.user_data.get('selected_post_full_data')
    # current_action should be 'manage' if coming from the manage flow
    # original_action = context.user_data.get('current_action_type')

    if not selected_post_data:
        logger.error("prompt_action_for_selected_post called without selected_post_data.")
        await query.edit_message_text("Error: Critical information missing. Returning to main menu.")
        await start_command(update, context)
        return

    escaped_post_title = escape_markdown_v2(str(selected_post_data.get('title', "N/A")))
    post_id_for_actions = selected_post_data['id'] # Use the actual ID for callbacks
    escaped_post_id_for_display = escape_markdown_v2(str(post_id_for_actions))


    message_text = (
        f"You selected: *{escaped_post_title}*\n"
        f"ID: `{escaped_post_id_for_display}`\n\n"
        f"What would you like to do with this post?"
    )

    keyboard = [
        [InlineKeyboardButton(f"✏️ Edit Details", callback_data=f"do_edit_post_init:{post_id_for_actions}")],
        [InlineKeyboardButton(f"🗑️ Delete Post", callback_data=f"do_delete_post_prompt:{post_id_for_actions}")],
        [InlineKeyboardButton("↩️ Choose Different Post", callback_data=f"manage_posts_init:0")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"MarkdownV2 failed in prompt_action_for_selected_post: {e}. Sending plain.")
        plain_text = (
            f"You selected: {selected_post_data.get('title', 'N/A')}\n"
            f"ID: {post_id_for_actions}\n\n"
            f"What would you like to do with this post?"
        )
        await query.edit_message_text(text=plain_text, reply_markup=reply_markup)

# --- Read-Only Post Listing Functions ---
async def handle_readonly_list_posts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, page_str = query.data.split(':')
        page_num = int(page_str)
    except ValueError:
        logger.error(f"Invalid callback data for handle_readonly_list_posts_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid parameters. Returning to main menu.")
        await start_command(update, context)
        return

    if page_num == 0:
        context.user_data.pop('paginated_posts_cache', None)

    context.user_data['current_page_num'] = page_num
    context.user_data.pop('current_action_type', None)

    await display_readonly_posts_page(update, context, page_num)

async def display_readonly_posts_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page_num: int) -> None:
    query = update.callback_query

    if 'paginated_posts_cache' not in context.user_data or page_num == 0: # Always refresh cache on page 0 for this view
        all_posts = load_blog_posts()
        if not all_posts:
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
            raw_message_text = "There are no blog posts to display. Would you like to create one?"
            message_text_escaped = escape_markdown_v2(raw_message_text)
            # Assuming this message should also be MarkdownV2 if it's a common pattern,
            # though the original didn't specify parse_mode. Adding for consistency.
            # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text_escaped}<<<")
            await query.edit_message_text(
                text=message_text_escaped,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='MarkdownV2' # Added parse_mode for consistency
            )
            return

        new_cached_posts = []
        for p in all_posts:
            title = p.get('title', 'No Title')
            date_str = p.get('date_published', '')
            content_str = p.get('content', '')
            post_id = p['id']

            escaped_title = escape_markdown_v2(str(title))

            formatted_date = "Unknown Date"
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    formatted_date = date_obj.strftime("%Y-%m-%d")
                except ValueError:
                    pass
            escaped_formatted_date = escape_markdown_v2(formatted_date)

            snippet_text = content_str[:100] + ('...' if len(content_str) > 100 else '')
            escaped_snippet = escape_markdown_v2(str(snippet_text))

            new_cached_posts.append({
                'id': post_id,
                'escaped_title': escaped_title,
                'escaped_formatted_date': escaped_formatted_date,
                'escaped_snippet': escaped_snippet
            })
        context.user_data['paginated_posts_cache'] = sorted(
            new_cached_posts,
            key=lambda x: x.get('escaped_formatted_date', ''), # Sort by original date logic if needed, or escaped if consistent
            reverse=True # Assuming reverse chronological
        )


    cached_posts = context.user_data.get('paginated_posts_cache', [])
    if not cached_posts:
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
        raw_message_text = "There are no blog posts to display. Would you like to create one?"
        message_text_escaped = escape_markdown_v2(raw_message_text)
        # Assuming this message should also be MarkdownV2
        # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text_escaped}<<<")
        await query.edit_message_text(
            text=message_text_escaped,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='MarkdownV2' # Added parse_mode for consistency
        )
        return

    total_posts = len(cached_posts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    page_num = max(0, min(page_num, total_pages - 1))
    context.user_data['current_page_num'] = page_num

    start_index = page_num * POSTS_PER_PAGE
    end_index = start_index + POSTS_PER_PAGE
    posts_on_page = cached_posts[start_index:end_index]

    message_text = f"📝 *Blog Posts* \\(Page {page_num + 1}/{total_pages}\\):\n\n"
    keyboard_buttons = []

    for post_data_cache_entry in posts_on_page: # Renamed post to avoid conflict
        escaped_post_id_for_display = escape_markdown_v2(str(post_data_cache_entry['id']))
        message_text += f"*Title:* {post_data_cache_entry['escaped_title']}\n"
        message_text += f"*Date:* {post_data_cache_entry['escaped_formatted_date']}\n"
        message_text += f"*ID:* `{escaped_post_id_for_display}`\n"
        message_text += f"*Snippet:* {post_data_cache_entry['escaped_snippet']}\n\n"


    pagination_row = []
    if page_num > 0:
        pagination_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"readonly_list_page:{page_num - 1}"))
    if page_num < total_pages - 1:
        pagination_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"readonly_list_page:{page_num + 1}"))

    if pagination_row:
        keyboard_buttons.append(pagination_row)
    keyboard_buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    try:
        # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
        # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"MarkdownV2 failed in display_readonly_posts_page: {e}. Sending plain.")
        plain_text = f"Blog Posts (Page {page_num + 1}/{total_pages}):\n\n"
        for post_item_cache in posts_on_page:
            # For plain text, we use the unescaped versions if we had stored them,
            # or re-create them if only escaped ones are in cache.
            # Assuming cache stores escaped for MD, for plain text it's better to use raw or re-generate.
            # For this example, I'll just use the escaped ones without MD formatting.
            plain_text += f"Title: {post_item_cache['escaped_title']}\n"
            plain_text += f"Date: {post_item_cache['escaped_formatted_date']}\n"
            plain_text += f"ID: {post_item_cache['id']}\n" # ID is fine as is for plain text
            plain_text += f"Snippet: {post_item_cache['escaped_snippet']}\n\n"
        await query.edit_message_text(text=plain_text, reply_markup=reply_markup)

async def handle_readonly_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, page_str = query.data.split(':')
        page_num = int(page_str)
    except ValueError:
        logger.error(f"Invalid callback data for handle_readonly_pagination_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid pagination parameters. Returning to main menu.")
        await start_command(update, context)
        return

    context.user_data['current_page_num'] = page_num
    if 'paginated_posts_cache' not in context.user_data:
        logger.info("paginated_posts_cache missing in handle_readonly_pagination_callback. Re-initializing readonly list from page 0.")
        context.user_data.pop('paginated_posts_cache', None)
        await display_readonly_posts_page(update, context, page_num=0)
        return

    await display_readonly_posts_page(update, context, page_num)

# --- Edit and Delete Action Handlers (Post-Selection) ---
async def handle_do_edit_post_init_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return ConversationHandler.END

    try:
        _, post_uuid = query.data.split(':')
    except ValueError:
        logger.error(f"Invalid callback data for handle_do_edit_post_init_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid edit parameters. Returning to main menu.")
        await start_command(update, context)
        return ConversationHandler.END

    all_posts = load_blog_posts()
    post_to_edit = next((post for post in all_posts if post.get('id') == post_uuid), None)

    if not post_to_edit:
        logger.warning(f"Post UUID {post_uuid} for editing not found. Potentially deleted.")
        await query.edit_message_text(text=f"Error: Post with ID '{post_uuid}' not found. It might have been deleted. Returning to main menu.")
        await start_command(update, context)
        return ConversationHandler.END

    context.user_data['edit_post_data'] = {
        'post_id': post_uuid,
        'original_post': dict(post_to_edit)
    }

    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('current_action_type', None)

    return await prompt_select_field_to_edit(update, context)

async def prompt_select_field_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user which part of the post they want to edit."""
    query = update.callback_query
    await query.answer()

    message_text = "Which part of the post would you like to edit?"
    keyboard = [
        [InlineKeyboardButton("Edit Title", callback_data='editfield_title')],
        [InlineKeyboardButton("Edit Content", callback_data='editfield_content')],
        [InlineKeyboardButton("Edit Author", callback_data='editfield_author')],
        [InlineKeyboardButton("Edit Image URL", callback_data='editfield_image_url')],
        [InlineKeyboardButton("Back to Post Actions", callback_data='editfield_back_to_actions')],
        [InlineKeyboardButton("Cancel Editing", callback_data='editfield_cancel_current_edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
    await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    return SELECT_FIELD_TO_EDIT

async def handle_field_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's choice of which field to edit."""
    query = update.callback_query
    await query.answer()
    callback_data = query.data

    if 'edit_post_data' not in context.user_data or not context.user_data['edit_post_data'].get('post_id'):
        logger.error("handle_field_selection_callback: edit_post_data or post_id missing from context.")
        await query.edit_message_text("Error: Post editing session data is missing. Please start over.")
        # Clean up potentially inconsistent state
        context.user_data.pop('edit_post_data', None)
        context.user_data.pop('selected_post_uuid', None)
        context.user_data.pop('selected_post_full_data', None)
        await start_command(update, context) # Send back to main menu
        return ConversationHandler.END

    if callback_data == 'editfield_back_to_actions':
        post_id = context.user_data['edit_post_data'].get('post_id')
        # No need to check post_id again here as it's checked above

        # Restore context for prompt_action_for_selected_post
        context.user_data['selected_post_uuid'] = post_id
        all_posts = load_blog_posts()
        selected_post_obj = next((post for post in all_posts if post.get('id') == post_id), None)

        if not selected_post_obj:
            logger.error(f"handle_field_selection_callback: Post with ID {post_id} not found when going back to actions.")
            await query.edit_message_text("Error: Selected post seems to have been deleted. Returning to main menu.")
            await start_command(update, context)
            return ConversationHandler.END

        context.user_data['selected_post_full_data'] = dict(selected_post_obj)
        # prompt_action_for_selected_post is not part of this ConversationHandler's states.
        # It typically shows a new message with its own inline keyboard.
        # We need to ensure the current message is cleaned up or appropriately handled.
        # For simplicity, we can edit the current message to indicate returning, then let prompt_action send a new one.
        await query.edit_message_text("Returning to post actions...") # Placeholder, prompt_action_for_selected_post will replace this
        await prompt_action_for_selected_post(update, context)
        return ConversationHandler.END # End current edit conversation gracefully

    # It's a field edit, e.g., 'editfield_title'
    try:
        field_to_edit = callback_data.split('_', 1)[1]
    except IndexError:
        logger.error(f"Invalid callback_data in handle_field_selection_callback: {callback_data}")
        await query.edit_message_text("Error: Invalid selection. Please try again.")
        return SELECT_FIELD_TO_EDIT # Ask again

    context.user_data['edit_post_data']['field_to_edit'] = field_to_edit
    user_friendly_field_name = field_to_edit.replace('_', ' ').capitalize()

    message_text = (
        f"You chose to edit: *{escape_markdown_v2(user_friendly_field_name)}*\\.\n\n"
        f"Please send the new {escape_markdown_v2(user_friendly_field_name)} for the post\\. "
        f"\\(Or type /cancel_editing to abort this edit\\)"
    )
    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
    await query.edit_message_text(text=message_text, parse_mode='MarkdownV2')
    return GET_NEW_FIELD_VALUE

async def receive_new_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the new value for the selected field and saves the post."""
    new_value = update.message.text
    edit_data = context.user_data.get('edit_post_data')

    if not edit_data or not edit_data.get('post_id') or not edit_data.get('field_to_edit'):
        logger.error("receive_new_field_value: Session data (post_id or field_to_edit) missing.")
        await update.message.reply_text("Error: Critical session data is missing. Please start the edit process again.", parse_mode='MarkdownV2')
        # Clean up potentially inconsistent state
        context.user_data.pop('edit_post_data', None)
        context.user_data.pop('selected_post_uuid', None)
        context.user_data.pop('selected_post_full_data', None)
        await start_command(update, context) # Send back to main menu
        return ConversationHandler.END

    post_id = edit_data['post_id']
    field_to_edit = edit_data['field_to_edit']

    all_posts = load_blog_posts()
    post_to_update = None
    post_index = -1

    for i, post in enumerate(all_posts):
        if post.get('id') == post_id:
            post_to_update = post
            post_index = i
            break

    if not post_to_update:
        logger.error(f"receive_new_field_value: Post with ID {post_id} not found for update.")
        await update.message.reply_text("Error: The post you were editing could not be found. It might have been deleted. Please start over.", parse_mode='MarkdownV2')
        await start_command(update, context)
        return ConversationHandler.END

    # Update the field
    post_to_update[field_to_edit] = new_value
    all_posts[post_index] = post_to_update

    if save_blog_posts(all_posts):
        user_friendly_field_name = field_to_edit.replace('_', ' ').capitalize()
        success_message = f"Successfully updated the {escape_markdown_v2(user_friendly_field_name)} of the post!"
        await update.message.reply_text(success_message, parse_mode='MarkdownV2')
    else:
        await update.message.reply_text("Error: Could not save the updated post. Your changes have not been applied.", parse_mode='MarkdownV2')
        # Don't necessarily end the whole conversation, but the current edit attempt failed to save.
        # For robustness, returning to main menu.
        await start_command(update, context)
        return ConversationHandler.END

    # Clear the specific field being edited, but keep 'post_id' and 'original_post' in 'edit_post_data' if further edits are desired
    context.user_data['edit_post_data'].pop('field_to_edit', None)

    # Restore context to allow further actions on the same post (like editing another field or deleting)
    context.user_data['selected_post_uuid'] = post_id
    # Update selected_post_full_data with the newly modified post
    context.user_data['selected_post_full_data'] = dict(post_to_update)

    # Send a new message with the post actions menu
    await prompt_action_for_selected_post(update, context)
    return ConversationHandler.END # End current edit conversation gracefully

async def handle_do_delete_post_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, post_uuid = query.data.split(':')
    except ValueError:
        logger.error(f"Invalid callback data for handle_do_delete_post_prompt_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid delete parameters. Returning to main menu.")
        await start_command(update, context)
        return

    post_title_to_delete = "N/A"
    if context.user_data.get('selected_post_uuid') == post_uuid and context.user_data.get('selected_post_full_data'):
        post_title_to_delete = context.user_data['selected_post_full_data'].get('title', 'N/A')
    else:
        all_posts = load_blog_posts()
        post_to_confirm = next((post for post in all_posts if post.get('id') == post_uuid), None)
        if post_to_confirm:
            post_title_to_delete = post_to_confirm.get('title', 'N/A')
        else:
            logger.warning(f"Post UUID {post_uuid} for delete confirmation not found.")
            await query.edit_message_text(text=f"Error: Post with ID '{post_uuid}' not found. Returning to main menu.")
            await start_command(update, context)
            return

    escaped_title = escape_markdown_v2(str(post_title_to_delete))
    escaped_uuid = escape_markdown_v2(str(post_uuid))

    message_text = f"Are you sure you want to delete the post titled '{escaped_title}' \\(ID: {escaped_uuid}\\)?"

    keyboard = [[
        InlineKeyboardButton("Yes, Delete It", callback_data=f"do_delete_post_confirm:{post_uuid}"),
        InlineKeyboardButton("No, Cancel", callback_data="show_main_menu")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_text}<<<")
    await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')

async def handle_do_delete_post_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await start_command(update, context)
        return

    try:
        _, post_uuid = query.data.split(':')
    except ValueError:
        logger.error(f"Invalid callback data for handle_do_delete_post_confirm_callback: {query.data}")
        await query.edit_message_text("Error: Invalid delete confirmation. Returning to main menu.")
        await start_command(update, context)
        return

    posts = load_blog_posts()
    post_index_to_delete = -1
    deleted_post_title_val = "N/A"

    for i, post in enumerate(posts):
        if post.get('id') == post_uuid:
            post_index_to_delete = i
            deleted_post_title_val = post.get('title', 'N/A')
            break

    message_to_user = ""
    escaped_post_uuid = escape_markdown_v2(str(post_uuid))

    if post_index_to_delete != -1:
        del posts[post_index_to_delete]
        if save_blog_posts(posts):
            escaped_deleted_post_title = escape_markdown_v2(str(deleted_post_title_val))
            message_to_user = f"Post '*{escaped_deleted_post_title}*' \\(ID: `{escaped_post_uuid}`\\) has been deleted\\."
            logger.info(f"Post {post_uuid} deleted by {query.from_user.id}")
        else:
            message_to_user = "Error: Could not save changes after deleting post\\. Please check logs\\."
            logger.error(f"Failed to save posts after deleting {post_uuid}")
    else:
        message_to_user = f"Error: Post with ID `{escaped_post_uuid}` not found \\(maybe already deleted\\)\\."
        logger.warning(f"Post {post_uuid} for deletion not found by {query.from_user.id}")

    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('current_action_type', None)

    # logger.info(f"Attempting to edit message with MarkdownV2. Text: >>>{message_to_user}<<<")
    await query.edit_message_text(text=message_to_user, parse_mode='MarkdownV2')
    await start_command(update, context)


async def handle_show_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
    await start_command(update, context)

# --- Main Bot Setup (main() function) ---
async def main() -> None:
    if not BLOG_BOT_TOKEN:
        logger.error("FATAL: BLOG_BOT_TOKEN not found in environment variables.")
        return
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("WARNING: BLOG_ADMIN_CHAT_ID not found. Bot will be usable by anyone.")

    application = (
        ApplicationBuilder()
        .token(BLOG_BOT_TOKEN)
        .connect_timeout(20)
        .read_timeout(30)
        .build()
    )
    await application.initialize()

    newpost_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('newpost', newpost_start),
            CallbackQueryHandler(handle_menu_new_post_callback, pattern='^menu_new_post$')
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            CONTENT_CHOICE: [
                CallbackQueryHandler(handle_content_type_direct_callback, pattern='^content_type_direct$'),
                CallbackQueryHandler(handle_content_type_file_callback, pattern='^content_type_file$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, unexpected_text_in_content_choice)
            ],
            RECEIVE_TYPED_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_typed_content_message)],
            RECEIVE_CONTENT_FILE: [
                MessageHandler(filters.Document.TEXT | filters.Document.FileExtension('md'), receive_content_file_upload),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unexpected_message_in_file_state)
            ],
            AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_author)],
            IMAGE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_image_url_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_newpost)],
    )
    application.add_handler(newpost_conv_handler)

    editpost_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_do_edit_post_init_callback, pattern='^do_edit_post_init:')
        ],
        states={
            SELECT_FIELD_TO_EDIT: [CallbackQueryHandler(handle_field_selection_callback, pattern='^editfield_')],
            GET_NEW_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_field_value)],
        },
        fallbacks=[
            CommandHandler('cancel_editing', cancel_editing),
            CommandHandler('cancel', cancel_editing),
            CallbackQueryHandler(cancel_editing, pattern='^editfield_cancel_current_edit$')
        ],
    )
    application.add_handler(editpost_conv_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_menu_help_callback, pattern='^menu_help$'))

    application.add_handler(CallbackQueryHandler(handle_show_main_menu_callback, pattern='^show_main_menu$'))
    # Updated handler for manage posts init
    application.add_handler(CallbackQueryHandler(initiate_post_selection_callback, pattern='^manage_posts_init:'))
    # Kept select_post_init for Choose Different Post button, but ensure it sets action to 'manage' or handle appropriately
    application.add_handler(CallbackQueryHandler(initiate_post_selection_callback, pattern='^select_post_init:'))


    application.add_handler(CallbackQueryHandler(handle_select_post_page_callback, pattern='^select_post_page:'))
    application.add_handler(CallbackQueryHandler(handle_post_selection_callback, pattern='^post_selected:'))

    application.add_handler(CallbackQueryHandler(handle_readonly_list_posts_callback, pattern='^list_posts_page:'))
    application.add_handler(CallbackQueryHandler(handle_readonly_pagination_callback, pattern='^readonly_list_page:'))

    application.add_handler(CallbackQueryHandler(handle_do_delete_post_prompt_callback, pattern='^do_delete_post_prompt:'))
    application.add_handler(CallbackQueryHandler(handle_do_delete_post_confirm_callback, pattern='^do_delete_post_confirm:'))

    application.add_handler(CommandHandler("cancel", cancel_newpost))

    logger.info("Blog Bot starting...")
    await application.start()
    await application.updater.start_polling()
    try:
        while application.running:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("Bot process interrupted by user (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Unexpected error during main loop: {e}", exc_info=True)
    finally:
        if hasattr(application.updater, 'is_polling') and application.updater.is_polling():
            logger.info("Stopping updater...")
            await application.updater.stop()

        if application.running:
             logger.info("Application still marked as running, initiating stop sequence...")
             await application.stop()
        else:
             logger.info("Application already stopped or stopping.")

        logger.info("Bot shutdown process complete.")

if __name__ == '__main__':
    asyncio.run(main())

[end of blog_bot.py]
