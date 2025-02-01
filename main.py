import os
import sqlite3
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from urllib.parse import urlparse
from argon2 import PasswordHasher

# Read sensitive data from environment variables (no defaults)
ADMIN_USER_IDS = set(map(int, os.getenv("ADMIN_USER_IDS").split()))  # Space-separated user IDs
SHARED_USERNAME = os.getenv("SHARED_USERNAME")  # Shared username
SHARED_PASSWORD = os.getenv("SHARED_PASSWORD")  # Shared password
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Bot token

# Validate that required environment variables are set
if not all([ADMIN_USER_IDS, SHARED_USERNAME, SHARED_PASSWORD, TELEGRAM_BOT_TOKEN]):
    raise ValueError(
        "Missing required environment variables: ADMIN_USER_IDS, SHARED_USERNAME, SHARED_PASSWORD, TELEGRAM_BOT_TOKEN"
    )

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Argon2 hasher
ph = PasswordHasher()

# Database functions
def connect_db():
    return sqlite3.connect('links.db', check_same_thread=False)

def init_db():
    with connect_db() as conn:
        cursor = conn.cursor()
        # Drop the existing users table if it exists
        cursor.execute("DROP TABLE IF EXISTS users")
        # Create links table
        cursor.execute('''CREATE TABLE IF NOT EXISTS links (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            type TEXT,
                            name TEXT,
                            url TEXT,
                            deleted INTEGER DEFAULT 0,
                            UNIQUE(type, name))''')
        # Create users table with the correct schema
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER UNIQUE NOT NULL,
                            username TEXT NOT NULL,
                            password TEXT NOT NULL,
                            isLoggedIn BOOLEAN DEFAULT FALSE)''')
        conn.commit()

def hash_password(password):
    return ph.hash(password)

def verify_password(password, hashed_password):
    try:
        return ph.verify(hashed_password, password)
    except:
        return False

def add_admin_users():
    hashed_password = hash_password(SHARED_PASSWORD)
    with connect_db() as conn:
        cursor = conn.cursor()
        for user_id in ADMIN_USER_IDS:
            cursor.execute("INSERT OR IGNORE INTO users (user_id, username, password) VALUES (?, ?, ?)",
                           (user_id, SHARED_USERNAME, hashed_password))
        conn.commit()

def is_logged_in(user_id):
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT isLoggedIn FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result and result[0] == 1  # 1 = True, 0 = False

def add_link(link_type, name, url):
    if not is_valid_url(url):
        raise ValueError("Invalid URL")
    with connect_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO links (type, name, url) VALUES (?, ?, ?)", (link_type, name, url))
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"A link with the name '{name}' already exists in the '{link_type}' category.")

def delete_link(link_type, name):
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE links SET deleted=1 WHERE type=? AND name=?", (link_type, name))
        if cursor.rowcount == 0:
            raise ValueError(f"No link found with name '{name}' in the '{link_type}' category.")
        conn.commit()

def update_link(link_type, old_name, new_name, new_url):
    if not is_valid_url(new_url):
        raise ValueError("Invalid URL")
    with connect_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE links SET name=?, url=? WHERE type=? AND name=?", (new_name, new_url, link_type, old_name))
            if cursor.rowcount == 0:
                raise ValueError(f"No link found with name '{old_name}' in the '{link_type}' category.")
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"A link with the name '{new_name}' already exists in the '{link_type}' category.")

def get_links(link_type=None, include_deleted=False):
    with connect_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM links"
        params = []
        if link_type:
            query += " WHERE type=?"
            params.append(link_type)
            if not include_deleted:
                query += " AND deleted=0"
        elif not include_deleted:
            query += " WHERE deleted=0"
        cursor.execute(query, params)
        return cursor.fetchall()

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

# Command handlers
async def start(update: Update, context: CallbackContext):
    try:
        social_links = get_links(link_type="social")
        response = "Welcome! 🎉\n\nHere are my social media links. Please follow and subscribe:\n\n"
        for link in social_links:
            response += f"- [{link[2]}]({link[3]})\n"
        response += "\nUse /links to see all my projects and websites."
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in /start command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def links(update: Update, context: CallbackContext):
    try:
        project_links = get_links(link_type="project")
        website_links = get_links(link_type="website")
        response = "Here are all my links:\n\n"

        if project_links:
            response += "🔧 Projects:\n"
            for link in project_links:
                response += f"- [{link[2]}]({link[3]})\n"

        if website_links:
            response += "\n🌐 Websites:\n"
            for link in website_links:
                response += f"- [{link[2]}]({link[3]})\n"

        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in /links command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def add(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        if user_id not in ADMIN_USER_IDS or not is_logged_in(user_id):
            await update.message.reply_text("❌ You must be logged in to use this command.")
            return

        if len(context.args) != 3:
            await update.message.reply_text("Usage: /add <type> <name> <url>\nExample: /add social GitHub https://github.com")
            return

        link_type, name, url = context.args
        if link_type not in {"social", "project", "website"}:
            await update.message.reply_text("Invalid type. Use 'social', 'project', or 'website'.")
            return

        if not is_valid_url(url):
            await update.message.reply_text("Invalid URL. Please include a scheme (e.g., http:// or https://).")
            return

        add_link(link_type, name, url)
        await update.message.reply_text(f"✅ Link added: {name} ({link_type}) - {url}")
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")
    except Exception as e:
        logger.error(f"Error in /add command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def delete(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        if user_id not in ADMIN_USER_IDS or not is_logged_in(user_id):
            await update.message.reply_text("❌ You must be logged in to use this command.")
            return

        if len(context.args) != 2:
            await update.message.reply_text("Usage: /delete <type> <name>\nExample: /delete social GitHub")
            return

        link_type, name = context.args
        if link_type not in {"social", "project", "website"}:
            await update.message.reply_text("Invalid type. Use 'social', 'project', or 'website'.")
            return

        delete_link(link_type, name)
        await update.message.reply_text(f"✅ Link deleted: {name} ({link_type})")
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")
    except Exception as e:
        logger.error(f"Error in /delete command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def update_link_command(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        if user_id not in ADMIN_USER_IDS or not is_logged_in(user_id):
            await update.message.reply_text("❌ You must be logged in to use this command.")
            return

        if len(context.args) != 4:
            await update.message.reply_text("Usage: /update <type> <Old_Name> <New_Name> <New_link>\nExample: /update social OldGitHub NewGitHub https://newgithub.com")
            return

        link_type, old_name, new_name, new_url = context.args
        if link_type not in {"social", "project", "website"}:
            await update.message.reply_text("Invalid type. Use 'social', 'project', or 'website'.")
            return

        if not is_valid_url(new_url):
            await update.message.reply_text("Invalid URL. Please include a scheme (e.g., http:// or https://).")
            return

        update_link(link_type, old_name, new_name, new_url)
        await update.message.reply_text(f"✅ Link updated: {old_name} -> {new_name} ({link_type}) - {new_url}")
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")
    except Exception as e:
        logger.error(f"Error in /update command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

async def login(update: Update, context: CallbackContext):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /login <username> <password>")
        return

    user_id = update.message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ You are not authorized to log in.")
        return

    username, password = context.args
    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE user_id = ? AND username = ?", (user_id, username))
        result = cursor.fetchone()
        if result and verify_password(password, result[0]):
            cursor.execute("UPDATE users SET isLoggedIn = TRUE WHERE user_id = ?", (user_id,))
            conn.commit()
            await update.message.reply_text("✅ Login successful!")
        else:
            await update.message.reply_text("❌ Invalid username or password.")

async def logout(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ You are not authorized to log out.")
        return

    with connect_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET isLoggedIn = FALSE WHERE user_id = ?", (user_id,))
        conn.commit()
        await update.message.reply_text("✅ Logout successful!")

async def change_password(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        if user_id not in ADMIN_USER_IDS or not is_logged_in(user_id):
            await update.message.reply_text("❌ You must be logged in to use this command.")
            return

        if len(context.args) != 2:
            await update.message.reply_text("Usage: /change <old_password> <new_password>")
            return

        old_password, new_password = context.args
        with connect_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result and verify_password(old_password, result[0]):
                hashed_new_password = hash_password(new_password)
                cursor.execute("UPDATE users SET password = ? WHERE user_id = ?", (hashed_new_password, user_id))
                conn.commit()
                await update.message.reply_text("✅ Password changed successfully!")
            else:
                await update.message.reply_text("❌ Invalid old password.")
    except Exception as e:
        logger.error(f"Error in /change command: {e}")
        await update.message.reply_text("An error occurred. Please try again later.")

# Main function
def main():
    init_db()
    add_admin_users()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    commands = [
        ("start", start),
        ("links", links),
        ("add", add),
        ("delete", delete),
        ("update", update_link_command),
        ("login", login),
        ("logout", logout),
        ("change", change_password),
    ]
    for command, handler in commands:
        application.add_handler(CommandHandler(command, handler))

    application.run_polling()

if __name__ == '__main__':
    main()