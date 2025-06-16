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
TITLE, CONTENT, AUTHOR, IMAGE_URL = range(4)
# Conversation states for /editpost
SELECT_POST_TO_EDIT, SELECT_FIELD_TO_EDIT, GET_NEW_FIELD_VALUE = range(4, 7)

# --- Constants ---
POSTS_PER_PAGE = 3

# --- Helper Functions ---
def escape_markdown_v2(text: str) -> str:
    """Escapes characters for Telegram MarkdownV2."""
    if not text:
        return ""
    # Chars to escape: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f'\\{char}' if char in escape_chars else char for char in text)

def load_blog_posts() -> list:
    if not os.path.exists(BLOG_POSTS_FILE):
        logger.info(f"{BLOG_POSTS_FILE} not found. Returning empty list.")
        return []
    try:
        with open(BLOG_POSTS_FILE, 'r', encoding='utf-8') as f:
            file_content = f.read() # Renamed to avoid conflict
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

    context.user_data.clear() # Clear previous context
    context.user_data['new_post'] = {}

    message_text = "Let's create a new blog post! First, what's the title of the post?"
    if update.callback_query: # Called from menu button
        await update.callback_query.message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    else: # Called by /newpost command
        await update.message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    return TITLE

# ... (receive_title, receive_content, receive_author, receive_image_url_and_save remain unchanged) ...
async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text
    context.user_data['new_post']['title'] = title
    await update.message.reply_text(f"Great! Title set to: '{title}'.\n\nNow, please send me the full content of the blog post.")
    return CONTENT

async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    content = update.message.text
    context.user_data['new_post']['content'] = content
    await update.message.reply_text("Content received.\n\nWho is the author? (Type 'skip' if you want to omit this)")
    return AUTHOR

async def receive_author(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    author = update.message.text
    if author.lower() != 'skip':
        context.user_data['new_post']['author'] = author
    else:
        context.user_data['new_post']['author'] = None
    await update.message.reply_text("Author noted.\n\nPlease provide a URL for the post's image. (Type 'skip' if no image)")
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
        await update.message.reply_text(f"Blog post '{post_data['title']}' successfully saved with ID: {post_data['id']}!")
    else:
        await update.message.reply_text("Error: Could not save the blog post to the file.")

    context.user_data.pop('new_post', None)
    return ConversationHandler.END

async def cancel_newpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'new_post' in context.user_data:
        context.user_data.pop('new_post', None)
    await update.effective_message.reply_text( # Use effective_message
        "New post creation cancelled.", reply_markup=ReplyKeyboardRemove() # Keep this immediate feedback
    )
    # Clean up new post specific context
    context.user_data.pop('new_post', None)
    # Show the main menu
    await start_command(update, context)
    return ConversationHandler.END

# --- Start and Help Commands (and their Callbacks) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return

    keyboard = [
        [InlineKeyboardButton("➕ Add New Post", callback_data='menu_new_post')],
        [InlineKeyboardButton("📄 List All Posts", callback_data='list_posts_page:0')],
        [InlineKeyboardButton("✏️ Edit a Post", callback_data='select_post_init:edit:0')],
        [InlineKeyboardButton("🗑️ Delete a Post", callback_data='select_post_init:delete:0')],
        [InlineKeyboardButton("❓ Help", callback_data='menu_help')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Clear relevant user_data when showing main menu, especially if coming from a callback
    if update.callback_query:
        query = update.callback_query # Make sure query is defined
        context.user_data.pop('current_action_type', None)
        context.user_data.pop('paginated_posts_cache', None)
        context.user_data.pop('current_page_num', None)
        context.user_data.pop('selected_post_uuid', None)
        context.user_data.pop('selected_post_full_data', None)
        await query.edit_message_text(
            text="Welcome, Blog Admin! I'm here to help you manage your website's blog posts. Use the menu below to get started or type /help for a list of commands.",
            reply_markup=reply_markup
        )
    else:
        await update.effective_message.reply_text(
            "Welcome, Blog Admin! I'm here to help you manage your website's blog posts. Use the menu below to get started or type /help for a list of commands.",
            reply_markup=reply_markup
        )

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
        "   - <b>➕ Add New Post</b>: Starts the process to create a new blog post. I'll ask you for the title, content, author (optional), and an image URL (optional).\n\n"
        "   - <b>📄 List All Posts</b>: Shows a paginated view of all your blog posts with their titles and publication dates for quick reference.\n\n"
        "   - <b>✏️ Edit a Post</b>: Allows you to modify an existing blog post.\n"
        "     1. I'll show you a paginated list of your posts.\n"
        "     2. Press '[Select]' next to the post you want to edit.\n"
        "     3. Confirm your choice, then select which part of the post (title, content, etc.) you want to change using the buttons provided.\n\n"
        "   - <b>🗑️ Delete a Post</b>: Lets you remove a blog post.\n"
        "     1. I'll show you a paginated list of your posts.\n"
        "     2. Press '[Select]' next to the post you want to delete.\n"
        "     3. You'll be asked to confirm the deletion before the post is permanently removed.\n\n"
        "🔹 <b>Cancelling an Operation:</b>\n"
        "   - If you start an operation (like adding or editing a post) and want to stop, type /cancel. This will take you back to the main menu.\n\n"
        "Type /start at any time to see the main menu."
    )
    # Make sure to use HTML for parsing this help text
    effective_message = update.effective_message
    if update.callback_query: # If called from menu_help callback
        effective_message = update.callback_query.message

    await effective_message.reply_text(help_text, parse_mode='HTML')

# --- Other Command Handlers ---
async def listposts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    posts = load_blog_posts()
    if not posts:
        await update.effective_message.reply_text("There are no blog posts yet.") # Use effective_message
        return
    message = "Here are your blog posts:\n\n"
    for post in posts:
        post_id = post.get('id', 'N/A')
        title = post.get('title', 'No Title')
        message += f"ID: {str(post_id)}\nTitle: {str(title)}\n--------------------\n"
    if posts: # Only add hint if there are posts
        message += "\n\nTip: Use these IDs with `/editpost <ID>` or `/deletepost <ID>`."
    if len(message) > 4096:
        await update.effective_message.reply_text("The list of posts is very long and might be truncated by Telegram.")
    await update.effective_message.reply_text(message) # Use effective_message

async def deletepost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Please provide the ID of the post to delete. Usage: /deletepost <post_id>") # Use effective_message
        return
    post_id_to_delete = context.args[0]
    posts = load_blog_posts()
    post_to_delete = next((post for post in posts if post.get('id') == post_id_to_delete), None)
    if not post_to_delete:
        await update.effective_message.reply_text(f"Post with ID '{post_id_to_delete}' not found.") # Use effective_message
        return
    keyboard = [[ InlineKeyboardButton("Yes, Delete It", callback_data=f"deletepost_confirm_yes:{post_id_to_delete}"), InlineKeyboardButton("No, Cancel", callback_data=f"deletepost_confirm_no:{post_id_to_delete}"),]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    title = post_to_delete.get('title', 'N/A')
    await update.effective_message.reply_text(f"Are you sure you want to delete the post titled '{title}' (ID: {post_id_to_delete})?", reply_markup=reply_markup) # Use effective_message

async def deletepost_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action_parts = query.data.split(':', 1)
    action_prefix = action_parts[0]
    post_id_from_callback = action_parts[1] if len(action_parts) > 1 else None
    if not post_id_from_callback:
        await query.edit_message_text(text="Error processing delete confirmation: Post ID missing.")
        return
    if action_prefix == "deletepost_confirm_yes":
        posts = load_blog_posts()
        post_index_to_delete = -1
        for i, post in enumerate(posts):
            if post.get('id') == post_id_from_callback:
                post_index_to_delete = i
                break
        if post_index_to_delete != -1:
            deleted_post_title = posts[post_index_to_delete].get('title', 'N/A')
            del posts[post_index_to_delete]
            if save_blog_posts(posts):
                await query.edit_message_text(text=f"Post '{deleted_post_title}' (ID: {post_id_from_callback}) has been deleted.")
            else:
                await query.edit_message_text(text="Error: Could not save changes after deleting post.")
        else:
            await query.edit_message_text(text=f"Error: Post with ID '{post_id_from_callback}' not found (maybe deleted already).")
    elif action_prefix == "deletepost_confirm_no":
        await query.edit_message_text(text="Post deletion cancelled.")
    else:
        await query.edit_message_text(text="Error: Unknown delete confirmation action.")

# --- /editpost Conversation Handler Functions ---
async def editpost_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update, context):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data['edit_post_data'] = {}

    reply_target = update.effective_message # This will be query.message if from callback

    if not context.args:
        posts = load_blog_posts()
        if not posts:
            await reply_target.reply_text("There are no blog posts to edit.")
            return ConversationHandler.END
        message_text = "Here are your blog posts:\n\n"
        for post in posts:
            message_text += f"ID: {post.get('id', 'N/A')}\nTitle: {post.get('title', 'No Title')}\n--------------------\n"
        message_text += "\nPlease send the ID of the post you want to edit, or type /cancel."
        if len(message_text) > 4096:
            await reply_target.reply_text("List is too long. Use /listposts and /editpost <ID>.")
            return ConversationHandler.END
        await reply_target.reply_text(message_text)
        return SELECT_POST_TO_EDIT
    else:
        post_id_to_edit = context.args[0]
        posts = load_blog_posts()
        post_to_edit = next((post for post in posts if post.get('id') == post_id_to_edit), None)
        if not post_to_edit:
            await reply_target.reply_text(f"Post with ID '{post_id_to_edit}' not found.")
            return ConversationHandler.END
        context.user_data['edit_post_data']['post_id'] = post_id_to_edit
        context.user_data['edit_post_data']['original_post'] = dict(post_to_edit)
        title_to_edit = post_to_edit.get('title', 'N/A')
        await reply_target.reply_text(f"Selected post '{title_to_edit}' (ID: {post_id_to_edit}) for editing.")
        return await prompt_select_field_to_edit(update, context)

async def receive_post_id_for_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post_id_to_edit = update.effective_message.text.strip()
    posts = load_blog_posts()
    post_to_edit = next((post for post in posts if post.get('id') == post_id_to_edit), None)
    if not post_to_edit:
        await update.effective_message.reply_text(f"Post with ID '{post_id_to_edit}' not found. Send valid ID or /cancel.")
        return SELECT_POST_TO_EDIT
    context.user_data['edit_post_data']['post_id'] = post_id_to_edit
    context.user_data['edit_post_data']['original_post'] = dict(post_to_edit)
    title_to_edit = post_to_edit.get('title', 'N/A')
    await update.effective_message.reply_text(f"Selected post '{title_to_edit}' (ID: {post_id_to_edit}) for editing.")
    return await prompt_select_field_to_edit(update, context)

async def prompt_select_field_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post_title = context.user_data.get('edit_post_data', {}).get('original_post', {}).get('title', 'Selected Post')
    keyboard = [
        [InlineKeyboardButton("Title", callback_data='editfield_title')],
        [InlineKeyboardButton("Content", callback_data='editfield_content')],
        [InlineKeyboardButton("Author", callback_data='editfield_author')],
        [InlineKeyboardButton("Image URL", callback_data='editfield_image_url')],
        [InlineKeyboardButton("<< Finish Editing >>", callback_data='editfield_finish')],
        [InlineKeyboardButton("Cancel Editing", callback_data='editfield_cancel_current_edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = f"Editing post: '{post_title}'.\nWhich field would you like to edit?"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=message_text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error editing message in prompt_select_field_to_edit: {e}")
            await update.effective_chat.send_message(text=message_text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text=message_text, reply_markup=reply_markup)
    return SELECT_FIELD_TO_EDIT

async def handle_field_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field_choice = query.data
    if field_choice == 'editfield_finish':
        posts = load_blog_posts()
        post_id_to_save = context.user_data.get('edit_post_data', {}).get('post_id')
        edited_post_data = context.user_data.get('edit_post_data', {}).get('original_post')
        save_success = False # Flag to track save
        if post_id_to_save and edited_post_data:
            post_index = next((i for i, p in enumerate(posts) if p.get('id') == post_id_to_save), -1)
            if post_index != -1:
                posts[post_index] = edited_post_data
                if save_blog_posts(posts):
                    save_success = True

        if save_success:
            await query.edit_message_text(text="Finished editing post. Returning to main menu...")
        else:
            # Edit the message to inform about the error, but still offer to go to main menu
            await query.edit_message_text(text="Error: Could not save changes. Returning to main menu...")

        # Explicitly call start_command to show main menu
        # start_command will edit the current message to show the main menu
        await start_command(update, context)

        context.user_data.pop('edit_post_data', None)
        return ConversationHandler.END
    if field_choice == 'editfield_cancel_current_edit':
         await query.edit_message_text(text="Post editing cancelled.")
         context.user_data.pop('edit_post_data', None)
         return ConversationHandler.END
    field_map = {
        'editfield_title': {'name': 'title', 'prompt': 'What is the new title?'},
        'editfield_content': {'name': 'content', 'prompt': 'What is the new content?'},
        'editfield_author': {'name': 'author', 'prompt': "New author? ('None' or 'skip' to remove)"},
        'editfield_image_url': {'name': 'image_url', 'prompt': "New image URL? ('None' or 'skip' to remove)"}
    }
    if field_choice not in field_map:
        await query.edit_message_text(text="Invalid selection.")
        return await prompt_select_field_to_edit(update, context) # Re-show options
    selected_field = field_map[field_choice]
    context.user_data['edit_post_data']['field_to_edit'] = selected_field['name']
    prompt_message = selected_field['prompt'] + "\n\nOr type /cancel (or /cancel_editing) to stop editing this post."
    await query.edit_message_text(text=prompt_message)
    return GET_NEW_FIELD_VALUE

async def receive_new_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_value = update.effective_message.text
    edit_data = context.user_data.get('edit_post_data', {})
    field_name = edit_data.get('field_to_edit')
    original_post_copy = edit_data.get('original_post')
    if not all([field_name, original_post_copy]):
        await update.effective_message.reply_text("Error: State lost. Cancelling edit.")
        context.user_data.pop('edit_post_data', None)
        return ConversationHandler.END
    if new_value.lower() in ['none', 'skip'] and field_name in ['author', 'image_url']:
        original_post_copy[field_name] = None
    else:
        original_post_copy[field_name] = new_value
    await update.effective_message.reply_text(f"Field '{field_name}' set to: '{original_post_copy[field_name]}'. Choose another field or finish.")
    return await prompt_select_field_to_edit(update, context)

async def cancel_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'edit_post_data' in context.user_data:
        context.user_data.pop('edit_post_data', None)
    await update.effective_message.reply_text("Post editing cancelled.", reply_markup=ReplyKeyboardRemove())
    # Also attempt to remove inline keyboard if the cancel was from a state where it was shown
    if update.callback_query:
        try:
            # Try to edit the message to indicate cancellation before showing main menu
            await update.callback_query.edit_message_text(text="Post editing cancelled.", reply_markup=None)
        except Exception as e:
            logger.info(f"Could not edit message text on cancel_editing: {e}")
            # Fallback if edit fails (e.g. message too old, or not from a callback that can be edited)
            await update.effective_message.reply_text("Post editing cancelled.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.effective_message.reply_text("Post editing cancelled.", reply_markup=ReplyKeyboardRemove())

    # Clean up edit-specific context
    context.user_data.pop('edit_post_data', None)
    # Show the main menu
    await start_command(update, context)
    return ConversationHandler.END

# --- Menu Callback Handlers ---
async def handle_menu_new_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['new_post'] = {}
    await query.edit_message_text(text="Let's create a new blog post! First, please send me the title for the post. (Or type /cancel to go back to the main menu.)", reply_markup=None)
    return TITLE

async def handle_menu_list_posts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Fetching list of posts...", reply_markup=None)
    await listposts_command(update, context)

async def handle_menu_edit_post_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['edit_post_data'] = {}
    context.args = []
    await query.edit_message_text(text="Starting post edit process...", reply_markup=None)
    return await editpost_start_command(update, context)

async def handle_menu_delete_post_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="To delete a post, please type /deletepost <post_id>.\nUse /listposts to find the ID.", reply_markup=None)

async def handle_menu_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Displaying help...", reply_markup=None)
    await help_command(update, context)

# --- Post Selection and Paginated Display Functions ---

async def initiate_post_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initiates post selection for 'edit' or 'delete' actions."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context) # Show main menu
        return

    try:
        _, action, page_str = query.data.split(':')
        page_num = int(page_str)
    except ValueError:
        logger.error(f"Invalid callback data for initiate_post_selection_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid action parameters. Returning to main menu.")
        await start_command(update, context)
        return

    context.user_data['current_action_type'] = action
    context.user_data.pop('paginated_posts_cache', None) # Clear cache for fresh start
    context.user_data['current_page_num'] = page_num

    await display_post_selection_page(update, context, page_num)

async def display_post_selection_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page_num: int) -> None:
    """Displays a paginated list of posts for selection (edit/delete)."""
    query = update.callback_query
    # query.answer() should have been called by the calling function

    action = context.user_data.get('current_action_type')
    if not action:
        logger.warning("display_post_selection_page called without current_action_type set.")
        await query.edit_message_text("Error: Action context lost. Returning to main menu.")
        await start_command(update, context)
        return

    if 'paginated_posts_cache' not in context.user_data or page_num == 0 and query.data.startswith("select_post_init:"):
        all_posts = load_blog_posts()
        if not all_posts:
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
            await query.edit_message_text(
                text=f"There are no posts to {action}. Would you like to create one?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        # Cache only essential details, sorted by date descending
        context.user_data['paginated_posts_cache'] = sorted(
            [{'id': p['id'], 'title': p.get('title', 'No Title'), 'date_published': p.get('date_published', '')} for p in all_posts],
            key=lambda x: x.get('date_published', ''),
            reverse=True
        )

    cached_posts = context.user_data.get('paginated_posts_cache', [])
    if not cached_posts: # Should be caught above, but as a safeguard
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
        await query.edit_message_text(
            text=f"There are no posts to {action}. Would you like to create one?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    total_posts = len(cached_posts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    page_num = max(0, min(page_num, total_pages - 1)) # Ensure page_num is valid
    context.user_data['current_page_num'] = page_num

    start_index = page_num * POSTS_PER_PAGE
    end_index = start_index + POSTS_PER_PAGE
    posts_on_page = cached_posts[start_index:end_index]

    message_text = f"Please select a post to {escape_markdown_v2(action)} (Page {page_num + 1}/{total_pages}):\n\n"
    keyboard_buttons = []

    for post in posts_on_page:
        title = escape_markdown_v2(post.get('title', 'No Title'))
        # Safely format date if present
        date_str = post.get('date_published', '')
        if date_str:
            try:
                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                formatted_date = escape_markdown_v2(date_obj.strftime("%Y-%m-%d"))
                display_title = f"{title} ({formatted_date})"
            except ValueError:
                formatted_date = "Unknown Date"
                display_title = f"{title} ({formatted_date})"
        else:
            display_title = title

        message_text += f"*{display_title}*\nID: `{escape_markdown_v2(post['id'])}`\n\n"
        keyboard_buttons.append([InlineKeyboardButton(f"Select: {title[:30]}...", callback_data=f"post_selected:{post['id']}:{action}")])

    pagination_row = []
    if page_num > 0:
        pagination_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"select_post_page:{action}:{page_num - 1}"))
    if page_num < total_pages - 1:
        pagination_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"select_post_page:{action}:{page_num + 1}"))

    if pagination_row:
        keyboard_buttons.append(pagination_row)
    keyboard_buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    try:
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending paginated message (MarkdownV2 failed, trying plain): {e}")
        # Fallback to plain text if MarkdownV2 fails (e.g. due to unforeseen char)
        plain_message_text = f"Please select a post to {action} (Page {page_num + 1}/{total_pages}):\n\n"
        for post in posts_on_page:
            title = post.get('title', 'No Title')
            date_str = post.get('date_published', '')
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    formatted_date = date_obj.strftime("%Y-%m-%d")
                    display_title = f"{title} ({formatted_date})"
                except ValueError:
                    formatted_date = "Unknown Date"
                    display_title = f"{title} ({formatted_date})"
            else:
                display_title = title
            plain_message_text += f"{display_title}\nID: {post['id']}\n\n"
        await query.edit_message_text(text=plain_message_text, reply_markup=reply_markup)

async def handle_select_post_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles pagination for post selection list."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, action, page_str = query.data.split(':')
        page_num = int(page_str)
    except ValueError:
        logger.error(f"Invalid callback data for handle_select_post_page_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid pagination parameters. Returning to main menu.")
        await start_command(update, context)
        return

    # Ensure action is still in context, if not, it might be an old inline keyboard
    if 'current_action_type' not in context.user_data or context.user_data['current_action_type'] != action:
        logger.warning(f"Action mismatch or missing in handle_select_post_page_callback. Expected {action}, got {context.user_data.get('current_action_type')}")
        await query.edit_message_text(text="Error: Session context mismatch. Please restart the action from the main menu.")
        # Don't show main menu directly, let user click if they want to
        # await start_command(update, context)
        # Instead, just inform. Or, could be more aggressive & force main menu.
        # For now, let's assume the user might realize and click main menu themselves.
        # If paginated_posts_cache is also missing, then it's safer to go to main menu.
        if 'paginated_posts_cache' not in context.user_data:
            await start_command(update, context)
        return


    context.user_data['current_page_num'] = page_num
    # paginated_posts_cache should already be populated
    if 'paginated_posts_cache' not in context.user_data:
        logger.info("paginated_posts_cache is missing in handle_select_post_page_callback. Re-initializing.")
        # This implies a fresh start for this action if cache is gone
        await initiate_post_selection_callback(update, context) # This will re-parse query.data
        return

    await display_post_selection_page(update, context, page_num)

async def handle_post_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the event when a user selects a specific post for an action."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return

    try:
        _, post_uuid, action = query.data.split(':', 2) # action can be part of uuid if not careful, ensure maxsplit
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
        # Restart selection for the original action, from page 0
        context.user_data.pop('paginated_posts_cache', None) # Clear cache
        original_action = context.user_data.get('current_action_type', 'edit') # default to edit if somehow lost
        # Need to ensure current_action_type is set correctly before calling display
        context.user_data['current_action_type'] = original_action # Reset it just in case
        await display_post_selection_page(update, context, page_num=0)
        return

    context.user_data['selected_post_uuid'] = post_uuid
    context.user_data['selected_post_full_data'] = dict(selected_post_obj) # Store a copy
    context.user_data['current_action_type'] = action # Ensure this is the action from the button

    await prompt_action_for_selected_post(update, context)

async def prompt_action_for_selected_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Asks user for next step after a post has been selected (e.g., proceed to edit/delete)."""
    query = update.callback_query
    # await query.answer() # Answered by caller typically

    selected_post_data = context.user_data.get('selected_post_full_data')
    original_action = context.user_data.get('current_action_type') # This should be 'edit' or 'delete'

    if not selected_post_data or not original_action:
        logger.error("prompt_action_for_selected_post called without selected_post_data or original_action.")
        await query.edit_message_text("Error: Critical information missing. Returning to main menu.")
        await start_command(update, context)
        return

    post_title = escape_markdown_v2(selected_post_data.get('title', "N/A"))
    post_id = selected_post_data['id']

    message_text = f"You selected post: *{post_title}*\nID: `{escape_markdown_v2(post_id)}`\n\nWhat would you like to do?"

    keyboard = []
    if original_action == 'edit':
        keyboard.append([InlineKeyboardButton(f"✏️ Edit This Post", callback_data=f"do_edit_post_init:{post_id}")])
        # Option to delete instead, if they chose edit initially
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete This Post", callback_data=f"do_delete_post_prompt:{post_id}")])
    elif original_action == 'delete':
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete This Post", callback_data=f"do_delete_post_prompt:{post_id}")])
        # Option to edit instead, if they chose delete initially
        keyboard.append([InlineKeyboardButton(f"✏️ Edit This Post", callback_data=f"do_edit_post_init:{post_id}")])

    keyboard.append([InlineKeyboardButton("↩️ Choose Different Post", callback_data=f"select_post_init:{original_action}:0")])
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"MarkdownV2 failed in prompt_action_for_selected_post: {e}. Sending plain.")
        plain_text = f"You selected post: {selected_post_data.get('title', 'N/A')}\nID: {post_id}\n\nWhat would you like to do?"
        await query.edit_message_text(text=plain_text, reply_markup=reply_markup)

# --- Read-Only Post Listing Functions ---

async def handle_readonly_list_posts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the initial call to list posts in read-only mode."""
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

    if page_num == 0: # Clear cache only on initial call from main menu
        context.user_data.pop('paginated_posts_cache', None)

    context.user_data['current_page_num'] = page_num
    # current_action_type should not be relevant here, or set to 'view'
    context.user_data.pop('current_action_type', None)

    await display_readonly_posts_page(update, context, page_num)

async def display_readonly_posts_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page_num: int) -> None:
    """Displays a paginated list of posts in read-only mode."""
    query = update.callback_query
    # query.answer() should have been called by the calling function

    # If cache is empty (e.g. direct call or after clearing), load posts
    if 'paginated_posts_cache' not in context.user_data:
        all_posts = load_blog_posts()
        if not all_posts:
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
            await query.edit_message_text(
                text="There are no blog posts to display. Would you like to create one?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        context.user_data['paginated_posts_cache'] = sorted(
            [{'id': p['id'], 'title': p.get('title', 'No Title'), 'date_published': p.get('date_published', '')} for p in all_posts],
            key=lambda x: x.get('date_published', ''),
            reverse=True
        )

    cached_posts = context.user_data.get('paginated_posts_cache', [])
    if not cached_posts: # Safeguard
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data='show_main_menu')]]
        await query.edit_message_text(
            text="There are no blog posts to display. Would you like to create one?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    total_posts = len(cached_posts)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    page_num = max(0, min(page_num, total_pages - 1))
    context.user_data['current_page_num'] = page_num

    start_index = page_num * POSTS_PER_PAGE
    end_index = start_index + POSTS_PER_PAGE
    posts_on_page = cached_posts[start_index:end_index]

    message_text = f"📝 *Blog Posts* (Page {page_num + 1}/{total_pages}):\n\n"
    keyboard_buttons = []

    for post in posts_on_page:
        title = escape_markdown_v2(post.get('title', 'No Title'))
        date_str = post.get('date_published', '')
        if date_str:
            try:
                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                formatted_date = escape_markdown_v2(date_obj.strftime("%Y-%m-%d"))
                message_text += f"*{title}* ({formatted_date})\nID: `{escape_markdown_v2(post['id'])}`\n\n"
            except ValueError:
                message_text += f"*{title}* (Unknown Date)\nID: `{escape_markdown_v2(post['id'])}`\n\n"
        else:
            message_text += f"*{title}*\nID: `{escape_markdown_v2(post['id'])}`\n\n"
        # No "Select" button for read-only view

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
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"MarkdownV2 failed in display_readonly_posts_page: {e}. Sending plain.")
        plain_text = f"Blog Posts (Page {page_num + 1}/{total_pages}):\n\n"
        for post in posts_on_page:
            title = post.get('title', 'No Title')
            date_str = post.get('date_published', '')
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    formatted_date = date_obj.strftime("%Y-%m-%d")
                    plain_text += f"{title} ({formatted_date})\nID: {post['id']}\n\n"
                except ValueError:
                    plain_text += f"{title} (Unknown Date)\nID: {post['id']}\n\n"
            else:
                plain_text += f"{title}\nID: {post['id']}\n\n"
        await query.edit_message_text(text=plain_text, reply_markup=reply_markup)

async def handle_readonly_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles pagination for the read-only post list."""
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
        # If cache is gone, restart the readonly list from page 0 to rebuild cache
        context.user_data.pop('paginated_posts_cache', None) # Ensure it's clear
        await display_readonly_posts_page(update, context, page_num=0) # Display page 0
        return

    await display_readonly_posts_page(update, context, page_num)

# --- Edit and Delete Action Handlers (Post-Selection) ---

async def handle_do_edit_post_init_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Initiates the edit process for a selected post, preparing for ConversationHandler."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        await query.edit_message_text(text="Unauthorized. Returning to main menu.")
        await start_command(update, context)
        return ConversationHandler.END # End if unauthorized

    try:
        _, post_uuid = query.data.split(':')
    except ValueError:
        logger.error(f"Invalid callback data for handle_do_edit_post_init_callback: {query.data}")
        await query.edit_message_text(text="Error: Invalid edit parameters. Returning to main menu.")
        await start_command(update, context)
        return ConversationHandler.END # End on error

    all_posts = load_blog_posts()
    post_to_edit = next((post for post in all_posts if post.get('id') == post_uuid), None)

    if not post_to_edit:
        logger.warning(f"Post UUID {post_uuid} for editing not found. Potentially deleted.")
        await query.edit_message_text(text=f"Error: Post with ID '{escape_markdown_v2(post_uuid)}' not found. It might have been deleted. Returning to main menu.", parse_mode='MarkdownV2')
        await start_command(update, context)
        return ConversationHandler.END # End if post not found

    # Prepare context for the editpost_conv_handler
    context.user_data['edit_post_data'] = {
        'post_id': post_uuid,
        'original_post': dict(post_to_edit) # Store a copy
    }

    # Clean up selection-specific context data
    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('current_action_type', None) # Clear as edit action is now confirmed

    # This function will send the message asking which field to edit
    # and returns the next state for the conversation handler.
    return await prompt_select_field_to_edit(update, context)

async def handle_do_delete_post_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Asks for confirmation before deleting a post."""
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

    # Load only title for confirmation to save resources if post is large
    # However, selected_post_full_data might still be in context if coming from prompt_action_for_selected_post
    post_title = "N/A"
    if context.user_data.get('selected_post_uuid') == post_uuid and context.user_data.get('selected_post_full_data'):
        post_title = context.user_data['selected_post_full_data'].get('title', 'N/A')
    else: # Fallback if context was lost or direct entry
        all_posts = load_blog_posts()
        post_to_confirm = next((post for post in all_posts if post.get('id') == post_uuid), None)
        if post_to_confirm:
            post_title = post_to_confirm.get('title', 'N/A')
        else:
            logger.warning(f"Post UUID {post_uuid} for delete confirmation not found.")
            await query.edit_message_text(text=f"Error: Post with ID '{escape_markdown_v2(post_uuid)}' not found. Returning to main menu.", parse_mode='MarkdownV2')
            await start_command(update, context)
            return

    escaped_title = escape_markdown_v2(post_title)
    escaped_uuid = escape_markdown_v2(post_uuid)
    message_text = f"Are you sure you want to delete the post titled '*{escaped_title}*' (ID: `{escaped_uuid}`)\\?"

    keyboard = [[
        InlineKeyboardButton("Yes, Delete It", callback_data=f"do_delete_post_confirm:{post_uuid}"),
        InlineKeyboardButton("No, Cancel", callback_data="show_main_menu") # Or back to post selection? Main menu is safer.
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode='MarkdownV2')

async def handle_do_delete_post_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the actual deletion of a post after confirmation."""
    query = update.callback_query
    await query.answer()

    if not await is_admin(update, context):
        # No message edit here as start_command will handle it
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
    deleted_post_title = "N/A"

    for i, post in enumerate(posts):
        if post.get('id') == post_uuid:
            post_index_to_delete = i
            deleted_post_title = post.get('title', 'N/A')
            break

    message_to_user = ""

    if post_index_to_delete != -1:
        del posts[post_index_to_delete]
        if save_blog_posts(posts):
            message_to_user = f"Post '*{escape_markdown_v2(deleted_post_title)}*' (ID: `{escape_markdown_v2(post_uuid)}`) has been deleted\\."
            logger.info(f"Post {post_uuid} deleted by {query.from_user.id}")
        else:
            message_to_user = "Error: Could not save changes after deleting post\\. Please check logs\\."
            logger.error(f"Failed to save posts after deleting {post_uuid}")
    else:
        message_to_user = f"Error: Post with ID `{escape_markdown_v2(post_uuid)}` not found (maybe already deleted)\\."
        logger.warning(f"Post {post_uuid} for deletion not found by {query.from_user.id}")

    # Clean up context data related to selection/action
    context.user_data.pop('selected_post_uuid', None)
    context.user_data.pop('selected_post_full_data', None)
    context.user_data.pop('paginated_posts_cache', None)
    context.user_data.pop('current_page_num', None)
    context.user_data.pop('current_action_type', None)

    await query.edit_message_text(text=message_to_user, parse_mode='MarkdownV2')
    # Wait a bit before showing main menu to let user read message (optional)
    # await asyncio.sleep(2) # Requires import asyncio

    # Regardless of outcome for the delete operation, show main menu.
    # Create a new message for main menu or edit the existing one.
    # For simplicity, let start_command create a new message or edit the current one.
    # The user will see the confirmation/error, then the menu will appear.
    # If we want the menu to replace the confirmation message, we'd call start_command with query.

    # To make it cleaner, let's make start_command always edit if query is present.
    # The current message text will be the delete confirmation.
    # We want the next action to be on this message.
    await start_command(update, context) # This should edit the message to show the main menu.


async def handle_show_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'show_main_menu' callback to display the main menu."""
    query = update.callback_query
    if query: # Should always be true for a callback
        await query.answer()
    # start_command will handle clearing data and editing the message
    await start_command(update, context)

# --- Main Bot Setup (main() function) ---
async def main() -> None: # Changed to async def
    if not BLOG_BOT_TOKEN:
        logger.error("FATAL: BLOG_BOT_TOKEN not found in environment variables.")
        return
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("WARNING: BLOG_ADMIN_CHAT_ID not found. Bot will be usable by anyone.")

    application = (
        ApplicationBuilder()
        .token(BLOG_BOT_TOKEN)
        .connect_timeout(20)  # Set connect timeout to 20 seconds
        .read_timeout(30)     # Set read timeout to 30 seconds
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
            CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_content)],
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
            # SELECT_POST_TO_EDIT is no longer needed here as selection happens before conversation starts.
            # The first state entered will be SELECT_FIELD_TO_EDIT,
            # returned by handle_do_edit_post_init_callback via prompt_select_field_to_edit.
            SELECT_FIELD_TO_EDIT: [CallbackQueryHandler(handle_field_selection_callback, pattern='^editfield_')],
            GET_NEW_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_field_value)],
        },
        fallbacks=[
            CommandHandler('cancel_editing', cancel_editing),
            CommandHandler('cancel', cancel_editing), # General cancel
            CallbackQueryHandler(cancel_editing, pattern='^editfield_cancel_current_edit$') # Ensure this specific cancel also works correctly
        ],
    )
    application.add_handler(editpost_conv_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command)) # Keep direct /help command
    # application.add_handler(CommandHandler("listposts", listposts_command)) # Superseded by list_posts_page:0 button
    # application.add_handler(CommandHandler("deletepost", deletepost_command)) # Superseded by select_post_init:delete:0 button

    # Handlers for other menu buttons not starting conversations
    # application.add_handler(CallbackQueryHandler(handle_menu_list_posts_callback, pattern='^menu_list_posts$')) # Replaced by paginated
    # application.add_handler(CallbackQueryHandler(handle_menu_delete_post_start_callback, pattern='^menu_delete_post_start$')) # Superseded by select_post_init:delete:0
    application.add_handler(CallbackQueryHandler(handle_menu_help_callback, pattern='^menu_help$')) # Keep menu help

    # --- New Paginated Post Selection & Listing Handlers ---
    application.add_handler(CallbackQueryHandler(handle_show_main_menu_callback, pattern='^show_main_menu$'))
    application.add_handler(CallbackQueryHandler(initiate_post_selection_callback, pattern='^select_post_init:'))
    application.add_handler(CallbackQueryHandler(handle_select_post_page_callback, pattern='^select_post_page:'))
    application.add_handler(CallbackQueryHandler(handle_post_selection_callback, pattern='^post_selected:'))

    application.add_handler(CallbackQueryHandler(handle_readonly_list_posts_callback, pattern='^list_posts_page:'))
    application.add_handler(CallbackQueryHandler(handle_readonly_pagination_callback, pattern='^readonly_list_page:'))

    # --- Callback Handlers for actions post-selection (Delete is direct, Edit enters conv) ---
    application.add_handler(CallbackQueryHandler(handle_do_delete_post_prompt_callback, pattern='^do_delete_post_prompt:'))
    application.add_handler(CallbackQueryHandler(handle_do_delete_post_confirm_callback, pattern='^do_delete_post_confirm:'))
    # Note: handle_do_edit_post_init_callback is registered as an entry point to editpost_conv_handler


    # Handler for OLD deletepost confirmation buttons (from /deletepost command) - consider removing/commenting out
    # application.add_handler(CallbackQueryHandler(deletepost_callback_handler, pattern="^deletepost_confirm_")) # Superseded

    # A global cancel command handler.
    # It's important this doesn't interfere with conversation-specific cancels if they have different logic.
    # cancel_newpost is relatively generic.
    application.add_handler(CommandHandler("cancel", cancel_newpost))

    logger.info("Blog Bot starting...")
    await application.start()
    await application.updater.start_polling()
    await application.running.wait() # Changed from application.updater.idle()
    logger.info("Blog Bot has stopped.")

if __name__ == '__main__':
    asyncio.run(main()) # Changed to asyncio.run

[end of blog_bot.py]
