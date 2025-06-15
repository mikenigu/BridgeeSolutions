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
    CallbackQueryHandler, # Added for Inline Keyboard
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence, # Optional: for persisting bot data across restarts
    Defaults
)
import asyncio

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
# IMPORTANT: User must set this in their .env file
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
            content = f.read()
            if not content:
                logger.info(f"{BLOG_POSTS_FILE} is empty. Returning empty list.")
                return []
            posts_data = json.loads(content)
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

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool: # Added context to signature
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("BLOG_ADMIN_CHAT_ID is not set. Access control is disabled.")
        if update.effective_message:
            # Store message to send later if needed, or use context.bot.send_message
            # For now, let's assume it's okay to send directly if effective_message exists
            await update.effective_message.reply_text("Warning: Bot admin chat ID is not configured. Access is open.")
        return True # Or False, depending on desired default behavior

    if str(update.effective_chat.id) == BLOG_ADMIN_CHAT_ID:
        return True
    else:
        if update.effective_message:
            await update.effective_message.reply_text("Sorry, you are not authorized to use this command.")
        logger.warning(f"Unauthorized access attempt by chat_id: {update.effective_chat.id}")
        return False

# --- /newpost Conversation Handler Functions ---
async def newpost_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update, context): # Pass context
        return ConversationHandler.END

    context.user_data['new_post'] = {}
    await update.message.reply_text(
        "Let's create a new blog post! First, what's the title of the post?",
        reply_markup=ReplyKeyboardRemove() # Remove any previous custom keyboards
    )
    return TITLE

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
        context.user_data['new_post']['author'] = None # Or a default like 'Admin'
    await update.message.reply_text("Author noted.\n\nPlease provide a URL for the post's image. (Type 'skip' if no image)")
    return IMAGE_URL

async def receive_image_url_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    image_url = update.message.text
    if image_url.lower() != 'skip':
        context.user_data['new_post']['image_url'] = image_url
    else:
        context.user_data['new_post']['image_url'] = None

    # Finalize post data
    post_data = context.user_data['new_post']
    post_data['id'] = str(uuid.uuid4())
    post_data['date_published'] = datetime.utcnow().isoformat() + 'Z'

    # Ensure all expected fields are present, even if None
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
    # Check if 'new_post' is in user_data before trying to pop it
    if 'new_post' in context.user_data:
        context.user_data.pop('new_post', None)
        await update.message.reply_text(
            "New post creation cancelled.", reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "No active new post operation to cancel.", reply_markup=ReplyKeyboardRemove()
        )
    return ConversationHandler.END

# --- Start and Help Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context): # Pass context
        return
    await update.message.reply_text(
        "Welcome, Blog Admin!\n\n"
        "You can use the following commands:\n"
        "/newpost - Create a new blog post\n"
        "/listposts - List all blog posts (ID and Title)\n"
        "/editpost [post_id] - Edit an existing post. If post_id is omitted, you'll be prompted.\n"
        "/deletepost <post_id> - Delete a post (you will be asked to confirm).\n"
        "/cancel - Cancel the current operation (like creating or editing a post)\n"
        "/help - Show this detailed help message"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context): # Pass context
        return
    help_text = (
        "Available commands:\n\n"
        "*/newpost* - Start a conversation to create a new blog post. The bot will guide you through providing a title, content, author, and an optional image URL.\n\n"
        "*/listposts* - Displays a list of all current blog posts with their unique IDs and titles. Useful for finding the ID needed for editing or deleting.\n\n"
        "*/editpost [post_id]* - Allows you to edit an existing blog post. \n"
        "  - If you provide a `post_id` (e.g., `/editpost 123e4567-e89b-12d3-a456-426614174000`), editing will start for that post.\n"
        "  - If you use `/editpost` without an ID, the bot will list posts and ask you to provide an ID.\n"
        "  You can then choose to edit the title, content, author, or image URL.\n\n"
        "*/deletepost <post_id>* - Deletes a blog post. You must provide the `post_id` of the post you want to delete (e.g., `/deletepost 123e4567-e89b-12d3-a456-426614174000`). You will be asked for confirmation before deletion.\n\n"
        "*/cancel* - Use this during any multi-step operation (like `/newpost` or `/editpost`) to stop the current process."
    )
    # Using plain text, no MarkdownV2 for this message as per prompt's example.
    await update.message.reply_text(help_text)

async def listposts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context): # Pass context to is_admin
        return

    posts = load_blog_posts()

    if not posts:
        await update.message.reply_text("There are no blog posts yet.")
        return

    message = "Here are your blog posts:\n\n"
    for post in posts:
        post_id = post.get('id', 'N/A')
        title = post.get('title', 'No Title')
        # Ensure post_id and title are strings for safe concatenation
        message += f"ID: {str(post_id)}\nTitle: {str(title)}\n--------------------\n"

    # Basic handling for Telegram message length limit (4096 characters)
    if len(message) > 4096:
        # Sending a truncated message or sending in chunks would be better.
        # For now, send a warning and then the potentially truncated message.
        await update.message.reply_text("The list of posts is very long and might be truncated by Telegram.")
        # A simple truncation strategy if needed:
        # message = message[:4050] + "\n... (list truncated)"

    await update.message.reply_text(message)

async def deletepost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Please provide the ID of the post to delete. Usage: /deletepost <post_id>")
        return

    post_id_to_delete = context.args[0]
    posts = load_blog_posts()
    post_to_delete = None

    for i, post in enumerate(posts):
        if post.get('id') == post_id_to_delete:
            post_to_delete = post
            break

    if not post_to_delete:
        await update.message.reply_text(f"Post with ID '{post_id_to_delete}' not found.")
        return

    keyboard = [
        [
            InlineKeyboardButton("Yes, Delete It", callback_data=f"deletepost_confirm_yes:{post_id_to_delete}"),
            InlineKeyboardButton("No, Cancel", callback_data=f"deletepost_confirm_no:{post_id_to_delete}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    title = post_to_delete.get('title', 'N/A')
    await update.message.reply_text(
        f"Are you sure you want to delete the post titled '{title}' (ID: {post_id_to_delete})?",
        reply_markup=reply_markup
    )

async def deletepost_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # Important to answer callback queries

    action_parts = query.data.split(':', 1) # Split only on the first colon
    action_prefix = action_parts[0]
    post_id_from_callback = action_parts[1] if len(action_parts) > 1 else None

    if not post_id_from_callback:
        await query.edit_message_text(text="Error processing delete confirmation: Post ID missing from callback.")
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
                await query.edit_message_text(text="Error: Could not save changes after deleting the post.")
        else:
            await query.edit_message_text(text=f"Error: Post with ID '{post_id_from_callback}' not found for deletion (it may have been deleted already).")

    elif action_prefix == "deletepost_confirm_no":
        await query.edit_message_text(text="Post deletion cancelled.")
    else:
        await query.edit_message_text(text="Error: Unknown delete confirmation action.")

# --- /editpost Conversation Handler Functions (Part 1: Post Selection) ---
async def editpost_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update, context):
        return ConversationHandler.END

    context.user_data['edit_post_data'] = {} # Initialize storage for editing

    if not context.args: # No post_id provided with /editpost command
        posts = load_blog_posts()
        if not posts:
            await update.message.reply_text("There are no blog posts to edit.")
            context.user_data.pop('edit_post_data', None) # Clean up
            return ConversationHandler.END

        message = "Here are your blog posts:\n\n"
        for post in posts:
            message += f"ID: {post.get('id', 'N/A')}\nTitle: {post.get('title', 'No Title')}\n--------------------\n"

        message += "\nPlease send the ID of the post you want to edit, or type /cancel."
        # Consider message length limits for very long lists here too.
        if len(message) > 4096: # Basic check
            await update.message.reply_text("The list of posts is very long. Please use /listposts to find the ID and then use /editpost <post_id>.")
            context.user_data.pop('edit_post_data', None) # Clean up
            return ConversationHandler.END

        await update.message.reply_text(message)
        return SELECT_POST_TO_EDIT
    else: # post_id is provided
        post_id_to_edit = context.args[0]
        posts = load_blog_posts()
        post_to_edit = next((post for post in posts if post.get('id') == post_id_to_edit), None)

        if not post_to_edit:
            await update.message.reply_text(f"Post with ID '{post_id_to_edit}' not found. Type /listposts to see available posts or /cancel.")
            context.user_data.pop('edit_post_data', None) # Clean up
            return ConversationHandler.END

        context.user_data['edit_post_data']['post_id'] = post_id_to_edit
        context.user_data['edit_post_data']['original_post'] = post_to_edit # Store the original post

        title_to_edit = post_to_edit.get('title', 'N/A')
        await update.message.reply_text(f"Selected post '{title_to_edit}' (ID: {post_id_to_edit}) for editing.")
        return await prompt_select_field_to_edit(update, context)

async def receive_post_id_for_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    post_id_to_edit = update.message.text.strip()
    posts = load_blog_posts()
    post_to_edit = next((post for post in posts if post.get('id') == post_id_to_edit), None)

    if not post_to_edit:
        await update.message.reply_text(f"Post with ID '{post_id_to_edit}' not found. Please send a valid ID, or type /cancel.")
        return SELECT_POST_TO_EDIT # Stay in this state to try again

    context.user_data['edit_post_data']['post_id'] = post_id_to_edit
    context.user_data['edit_post_data']['original_post'] = post_to_edit # Store the original post

    title_to_edit = post_to_edit.get('title', 'N/A')
    await update.message.reply_text(f"Selected post '{title_to_edit}' (ID: {post_id_to_edit}) for editing.")
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
        # If called from a callback (e.g., after editing a field or initial field selection)
        try:
            await update.callback_query.edit_message_text(text=message_text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error editing message in prompt_select_field_to_edit: {e}")
            # Fallback if edit fails (e.g., message not modified)
            await update.effective_chat.send_message(text=message_text, reply_markup=reply_markup)
    else:
        # If called after a text message (e.g., initial /editpost <id> or providing ID text)
        await update.message.reply_text(text=message_text, reply_markup=reply_markup)

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
            post_index = -1
            for i, p in enumerate(posts):
                if p.get('id') == post_id_to_save:
                    post_index = i
                    break
            if post_index != -1:
                posts[post_index] = edited_post_data
                if not save_blog_posts(posts): # Check if save failed
                    await query.edit_message_text(text="Error: Could not save changes to the blog post file.")
                    # Keep state or end? For now, end.
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
        'editfield_author': {'name': 'author', 'prompt': "What is the new author's name? (Type 'None' or 'skip' to remove author)"},
        'editfield_image_url': {'name': 'image_url', 'prompt': "What is the new image URL? (Type 'None' or 'skip' to remove image URL)"}
    }

    if field_choice not in field_map:
        await query.edit_message_text(text="Invalid selection. Please try again.")
        # Re-prompt with the field selection keyboard
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
        await query.edit_message_text(text=f"Editing post: '{post_title}'.\nWhich field would you like to edit?", reply_markup=reply_markup)
        return SELECT_FIELD_TO_EDIT

    selected_field = field_map[field_choice]
    context.user_data['edit_post_data']['field_to_edit'] = selected_field['name']

    await query.edit_message_text(text=selected_field['prompt'] + "\nOr send /cancel to stop editing this field and return to field selection.")
    return GET_NEW_FIELD_VALUE

async def receive_new_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_value = update.message.text
    edit_data = context.user_data.get('edit_post_data', {})
    field_name = edit_data.get('field_to_edit')
    post_id = edit_data.get('post_id')
    original_post_copy = edit_data.get('original_post') # This is the copy we update

    if not field_name or not post_id or original_post_copy is None:
        await update.message.reply_text("Error: Could not determine which field or post to update. Cancelling edit.")
        context.user_data.pop('edit_post_data', None)
        return ConversationHandler.END

    # Update the field in our working copy (original_post_copy)
    if new_value.lower() in ['none', 'skip'] and field_name in ['author', 'image_url']:
        original_post_copy[field_name] = None
    else:
        original_post_copy[field_name] = new_value

    # No need to load and save all posts here; we do that on "Finish Editing" or could do it field-by-field if preferred.
    # For now, changes are kept in context.user_data['edit_post_data']['original_post'] until "Finish"
    await update.message.reply_text(f"Field '{field_name}' ready to be updated to: '{original_post_copy[field_name]}'.")

    # Go back to selecting another field
    return await prompt_select_field_to_edit(update, context)

async def cancel_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'edit_post_data' in context.user_data:
        context.user_data.pop('edit_post_data', None)
    await update.message.reply_text("Post editing cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- Main Bot Setup (main() function) ---
async def main() -> None:
    if not BLOG_BOT_TOKEN:
        logger.error("FATAL: BLOG_BOT_TOKEN not found in environment variables.")
        return
    if not BLOG_ADMIN_CHAT_ID:
        logger.warning("WARNING: BLOG_ADMIN_CHAT_ID not found. The bot will be usable by anyone. Please set this in your .env file for security.")
        # Consider exiting if admin ID is mandatory:
        # logger.error("FATAL: BLOG_ADMIN_CHAT_ID is mandatory for security.")
        # return

    # Optional: Persistence for bot data (e.g., user_data across restarts)
    # persistence = PicklePersistence(filepath='./blog_bot_persistence')
    # application = ApplicationBuilder().token(BLOG_BOT_TOKEN).persistence(persistence).build()

    defaults = Defaults(connect_timeout=20, read_timeout=20, pool_timeout=20)
    application = ApplicationBuilder().token(BLOG_BOT_TOKEN).defaults(defaults).build()

    logger.info("Attempting to initialize application for get_me()...")
    await application.initialize() # Explicitly initialize before direct bot calls

    try:
        logger.info("Attempting to call get_me()...")
        bot_info = await application.bot.get_me()
        logger.info(f"Bot info received: {bot_info.username} (ID: {bot_info.id})")
    except Exception as e:
        logger.error(f"Error during get_me(): {e}", exc_info=True)
        # Decide if to return or continue to run_polling
        # For diagnostics, we'll let it try to run_polling anyway for now.

    # Conversation handler for /newpost
    newpost_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('newpost', newpost_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_content)],
            AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_author)],
            IMAGE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_image_url_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_newpost)],
    )

    application.add_handler(newpost_conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("listposts", listposts_command))
    application.add_handler(CommandHandler("deletepost", deletepost_command))
    application.add_handler(CallbackQueryHandler(deletepost_callback_handler, pattern="^deletepost_confirm_"))

    # Conversation handler for /editpost
    editpost_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('editpost', editpost_start_command)],
        states={
            SELECT_POST_TO_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_post_id_for_editing)],
            SELECT_FIELD_TO_EDIT: [
                CallbackQueryHandler(handle_field_selection_callback, pattern='^editfield_')
            ],
            GET_NEW_FIELD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_field_value)],
        },
        fallbacks=[CommandHandler('cancel_editing', cancel_editing), CommandHandler('cancel', cancel_editing)],
        # persistent=True, name="edit_post_conversation" # Optional
    )
    application.add_handler(editpost_conv_handler)

    # A global cancel command handler (ensure it's added after specific conversation fallbacks or is more generic)
    # For /newpost, it uses cancel_newpost. For /editpost, it uses cancel_editing.
    # If user types /cancel outside a specific context, cancel_newpost might be too specific.
    # Consider a more generic global cancel or ensure users use context-specific cancels.
    # For now, cancel_newpost is the general fallback for /cancel if not in a specific conversation handled by its own fallback.
    application.add_handler(CommandHandler("cancel", cancel_newpost))


    logger.info("Blog Bot starting...")
    application.run_polling()
    logger.info("Blog Bot has stopped.")

if __name__ == '__main__':
    asyncio.run(main())
