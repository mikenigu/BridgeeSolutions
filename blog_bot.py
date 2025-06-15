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
# import asyncio # No longer explicitly needed here as run_polling manages its own loop

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


# --- Helper Functions ---
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
        "New post creation cancelled.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- Start and Help Commands (and their Callbacks) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return

    keyboard = [
        [InlineKeyboardButton("➕ Add New Post", callback_data='menu_new_post')],
        [InlineKeyboardButton("📄 List Posts", callback_data='menu_list_posts')],
        [InlineKeyboardButton("✏️ Edit Post", callback_data='menu_edit_post_start')],
        [InlineKeyboardButton("🗑️ Delete Post", callback_data='menu_delete_post_start')],
        [InlineKeyboardButton("❓ Help", callback_data='menu_help')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.effective_message.reply_text( # Use effective_message
        "Welcome, Blog Admin! I'm here to help you manage your website's blog posts. Use the menu below to get started or type /help for a list of commands.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return
    help_text = (
        "I can help you manage your blog posts. Here are the available commands:\n\n"
        "*/start* - Shows the main menu.\n\n"
        "*/newpost* - Start a conversation to create a new blog post. I'll guide you through providing a title, content, author, and an optional image URL.\n\n"
        "*/listposts* - Displays a list of all current blog posts with their unique IDs and titles. This is useful for finding the ID needed for editing or deleting.\n\n"
        "*/editpost [post_id]* - Allows you to edit an existing blog post. \n"
        "  - If you provide a `post_id` (e.g., `/editpost your-post-id`), editing will start for that post.\n"
        "  - If you use `/editpost` without an ID, I will list recent posts and ask you to provide an ID. You can also use `/listposts` first to find the ID.\n"
        "  You can then choose to edit the title, content, author, or image URL.\n\n"
        "*/deletepost <post_id>* - Deletes a blog post. You must provide the `post_id` (e.g., `/deletepost your-post-id`). You can use `/listposts` to find the ID. You will be asked for confirmation before deletion.\n\n"
        "*/cancel* - During any multi-step operation (like `/newpost` or `/editpost`), use this command to stop the current process and return to the main state."
    )
    await update.effective_message.reply_text(help_text) # Use effective_message

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
        if post_id_to_save and edited_post_data:
            post_index = next((i for i, p in enumerate(posts) if p.get('id') == post_id_to_save), -1)
            if post_index != -1:
                posts[post_index] = edited_post_data
                if not save_blog_posts(posts):
                    await query.edit_message_text(text="Error: Could not save changes.")
                    context.user_data.pop('edit_post_data', None)
                    return ConversationHandler.END
        await query.edit_message_text(text="Finished editing post.")
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
            await update.callback_query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.info(f"Could not remove inline keyboard on cancel_editing: {e}")
    return ConversationHandler.END

# --- Menu Callback Handlers ---
async def handle_menu_new_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data['new_post'] = {}
    await query.edit_message_text(text="Let's create a new blog post! First, what's the title of the post?", reply_markup=None)
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

# --- Main Bot Setup (main() function) ---
def main() -> None:
    if not BLOG_BOT_TOKEN:
        logger.error("FATAL: BLOG_BOT_TOKEN not found in environment variables.")
        return
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("WARNING: BLOG_ADMIN_CHAT_ID not found. Bot will be usable by anyone.")

    application = ApplicationBuilder().token(BLOG_BOT_TOKEN).connect_timeout(20).read_timeout(20).build()

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
            CommandHandler('editpost', editpost_start_command),
            CallbackQueryHandler(handle_menu_edit_post_start_callback, pattern='^menu_edit_post_start$')
        ],
        states={
            SELECT_POST_TO_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_post_id_for_editing)],
            SELECT_FIELD_TO_EDIT: [CallbackQueryHandler(handle_field_selection_callback, pattern='^editfield_')],
            GET_NEW_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_field_value)],
        },
        fallbacks=[CommandHandler('cancel_editing', cancel_editing), CommandHandler('cancel', cancel_editing)],
    )
    application.add_handler(editpost_conv_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command)) # Keep direct /help command
    application.add_handler(CommandHandler("listposts", listposts_command))
    application.add_handler(CommandHandler("deletepost", deletepost_command))

    # Handlers for other menu buttons not starting conversations
    application.add_handler(CallbackQueryHandler(handle_menu_list_posts_callback, pattern='^menu_list_posts$'))
    application.add_handler(CallbackQueryHandler(handle_menu_delete_post_start_callback, pattern='^menu_delete_post_start$'))
    application.add_handler(CallbackQueryHandler(handle_menu_help_callback, pattern='^menu_help$'))

    # Handler for deletepost confirmation buttons
    application.add_handler(CallbackQueryHandler(deletepost_callback_handler, pattern="^deletepost_confirm_"))

    # A global cancel command handler.
    # It's important this doesn't interfere with conversation-specific cancels if they have different logic.
    # cancel_newpost is relatively generic.
    application.add_handler(CommandHandler("cancel", cancel_newpost))

    logger.info("Blog Bot starting...")
    application.run_polling()
    logger.info("Blog Bot has stopped.")

if __name__ == '__main__':
    main()
