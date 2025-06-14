import os
import json
import logging
from datetime import datetime
import uuid # For generating unique post IDs

from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence # Optional: for persisting bot data across restarts
)

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
        "/cancel - Cancel the current operation (like creating a post)\n"
        # Add /listposts, /editpost <id>, /deletepost <id> here later
        "/help - Show this message"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update, context): # Pass context
        return
    await update.message.reply_text(
        "Available commands:\n\n"
        "/newpost - Start creating a new blog post.\n"
        "/cancel - During an operation like /newpost, use this to stop.\n\n"
        # Future commands:
        "# /listposts - Show all post titles and IDs.\n"
        "# /editpost <ID> - Edit an existing post.\n"
        "# /deletepost <ID> - Delete a post."
    )

# --- Main Bot Setup (main() function) ---
def main() -> None:
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

    application = ApplicationBuilder().token(BLOG_BOT_TOKEN).build()

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
    # A global cancel command handler, should be added *after* conversations
    # or ensure its filters don't preempt conversation messages.
    # For simplicity, ConversationHandler's fallbacks are often preferred for specific convos.
    # Adding a global one here for good measure if user types /cancel outside a convo.
    application.add_handler(CommandHandler("cancel", cancel_newpost))


    logger.info("Blog Bot starting...")
    application.run_polling()
    logger.info("Blog Bot has stopped.")

if __name__ == '__main__':
    main()
