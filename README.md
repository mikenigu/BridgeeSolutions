## Running the Application

This project consists of a Flask web server and two Telegram bots.

### Environment Variables

Before running, create a `.env` file in the root directory by copying `.env.example` and filling in the required values:

*   `TELEGRAM_BOT_TOKEN`: Your Telegram bot token from BotFather. This is used by both `hr_bot.py` and `blog_bot.py`.
*   `HR_CHAT_ID`: The Telegram Chat ID for the HR personnel who will receive job application notifications and manage them via `hr_bot.py`.
*   `BLOG_ADMIN_CHAT_ID`: The Telegram Chat ID for the admin who will manage blog content via `blog_bot.py`.

### Running the Components

Each component needs to be run in a separate terminal:

1.  **Flask Web Server (`app.py`):**
    ```bash
    python app.py
    ```
    This will typically start the server on `http://127.0.0.1:5000/`.

2.  **HR Telegram Bot (`hr_bot.py`):**
    ```bash
    python hr_bot.py
    ```

3.  **Blog Content Telegram Bot (`blog_bot.py`):**
    ```bash
    python blog_bot.py
    ```

Ensure you have installed all dependencies from `requirements.txt`:
```bash
pip install -r requirements.txt
```
