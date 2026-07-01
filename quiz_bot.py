import os
import sqlite3
import json
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler, PollAnswerHandler
)

# Enable Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None

DB_FILE = "quiz_bot.db"

# Global dictionary for active group games memory
GROUP_GAMES = {}

# Conversation flow states
TITLE, DESCRIPTION, QUESTIONS, TIMER = range(4)
EDIT_TITLE, EDIT_DESC, EDIT_TIMER = range(4, 7)

def escape_markdown(text):
    """Escape special characters for Telegram Markdown"""
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_time(seconds):
    """Convert seconds to min:sec format (e.g., 1m 45s)"""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m {secs}s"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            title TEXT,
            description TEXT,
            timer INTEGER DEFAULT 30
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question_text TEXT,
            options TEXT,
            correct_answer TEXT,
            explanation TEXT,
            pre_message TEXT,
            FOREIGN KEY(quiz_id) REFERENCES quizzes(quiz_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Check if interaction is via callback button or command
    msg_obj = update.callback_query.message if update.callback_query else update.message
    user_id = update.callback_query.from_user.id if update.callback_query else update.message.from_user.id
    
    if update.callback_query:
        await update.callback_query.answer()
        
    await msg_obj.reply_text(
        "Let's create a new quiz. First, send me the title of your quiz (e.g., 'Aptitude Test' or '10 questions about bears').",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["quiz_build"] = {"title": "", "description": "", "questions": []}
    context.user_data["quiz_build_creator_id"] = user_id
    return TITLE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # Handle direct deep-linking tracking code
    if args and len(args) > 0 and args[0].startswith("quiz_"):
        quiz_id = args[0].split("_")[1]
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()
        
        if not quiz_data:
            await update.message.reply_text("❌ Quiz data not found.")
            return

        title, desc, timer = quiz_data
        time_disp = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        
        init_text = (
            f"🏁 **Quiz Setup Ready!**\n\n"
            f"📚 **Title:** {escape_markdown(title)}\n"
            f"ℹ️ **Description:** {escape_markdown(desc) if desc else 'No description'}\n"
            f"🙋‍♂️ **Questions:** {total_q[0]}\n"
            f"⏱ **Time per question:** {time_disp}\n\n"
            "⚠️ *Quiz shuru karne ke liye kam se kam 2 users ka Ready hona zaroori hai!*"
        )
        
        keyboard = [[InlineKeyboardButton("I am ready! 🎯 (0/2)", callback_data=f"ready_{quiz_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(init_text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    # Normal private chat initialization layout
    welcome_text = (
        "👋 **Welcome to Laado Quiz Bot!**\n\n"
        "Niche diye gaye buttons se aap apna naya quiz bana sakte hain ya pehle banaye huye quizzes dekh sakte hain:\n\n"
        "🚀 /newquiz - New Quiz Create Kare\n"
        "🖥️ /help - Help Menu"
    )
    keyboard = [
        [InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")],
        [InlineKeyboardButton("View My Quizzes 📚", callback_data="btn_viewquizzes")]
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **Help Menu**\n\n"
        "Aap is bot se quizzes bana kar apne dosto ke sath groups me realtime khel sakte hain.\n\n"
        "💡 **Available Actions:**"
    )
    keyboard = [
        [InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")],
        [InlineKeyboardButton("View My Quizzes 📚", callback_data="btn_viewquizzes")]
    ]
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["quiz_build"]["title"] = update.message.text
    await update.message.reply_text("Good. Now send me a description of your quiz. This is optional, you can /skip this step.")
    return DESCRIPTION

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data["quiz_build"]["description"] = "" if text.lower() == "/skip" else text
    await update.message.reply_text(
        f"Good. Your quiz '{context.user_data['quiz_build']['title']}' now has 0 questions. If you made a mistake, send /undo.\n\n"
        "💡 **Sawal jodne ke liye:**\nClick on 📎 (Attachment) -> Select **Poll**.\n"
        "Enable **Quiz Mode**, add 2-7 options, pick the correct one, and tap Create.\n\n"
        "Send /done when finished adding questions.",
        reply_markup=ReplyKeyboardRemove()
    )
    return QUESTIONS

async def receive_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    poll = update.message.poll
    if poll.type != "quiz":
        await update.message.reply_text("❌ Kripya Quiz mode wala poll hi send karein:")
        return QUESTIONS
    if len(poll.options) > 7:
        await update.message.reply_text("❌ Maximum 7 options allowed. Re-send poll:")
        return QUESTIONS

    opts = [o.text for o in poll.options]
    q_data = {
        "text": poll.question, "options": opts, "correct": opts[poll.correct_option_id],
        "explanation": poll.explanation if poll.explanation else "", "pre_message": ""
    }
    context.user_data["quiz_build"]["questions"].append(q_data)
    
    await update.message.reply_text(
        f"✅ Question added! Your quiz now has {len(context.user_data['quiz_build']['questions'])} question(s).\n\n"
        "Send next question or /done to finish."
    )
    return QUESTIONS

async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz = context.user_data.get("quiz_build")
    if quiz and quiz["questions"]:
        quiz["questions"].pop()
        await update.message.reply_text(f"↩️ Last question removed! Quiz now has {len(quiz['questions'])} question(s).\n\nSend next question or /done.")
    else:
        await update.message.reply_text("❌ No questions to remove!")
    return QUESTIONS

async def finish_quiz_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz = context.user_data.get("quiz_build", {})
    if not quiz or not quiz.get("questions"):
        await update.message.reply_text("❌ Error: Quiz must have at least 1 question!")
        return QUESTIONS
    
    await update.message.reply_text(
        "⏱️ **Please set a time limit for questions:**\n\n"
        "Type any of these: 15, 30, 40, 60\n\n"
        "Example: Type '30' for 30 seconds per question",
        reply_markup=ReplyKeyboardRemove()
    )
    return TIMER

async def handle_timer_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    time_map = {"15": 15, "30": 30, "40": 40, "60": 60}
    
    if text not in time_map:
        await update.message.reply_text("❌ Invalid time. Please enter: 15, 30, 40, or 60")
        return TIMER
    
    t_sec = time_map[text]
    quiz = context.user_data.get("quiz_build", {})
    
    if not quiz or not quiz.get("title"):
        await update.message.reply_text("❌ Error: Quiz data missing. Please start over with /newquiz")
        return ConversationHandler.END

    user_id = context.user_data.get("quiz_build_creator_id", update.message.from_user.id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO quizzes (creator_id, title, description, timer) VALUES (?, ?, ?, ?)", (user_id, quiz["title"], quiz["description"], t_sec))
    qid = cursor.lastrowid
    for q in quiz["questions"]:
        cursor.execute("INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation, pre_message) VALUES (?, ?, ?, ?, ?, ?)", 
                       (qid, q["text"], json.dumps(q["options"]), q["correct"], q["explanation"], q["pre_message"]))
    conn.commit()
    conn.close()
    
    context.user_data.pop("quiz_build", None)
    context.user_data.pop("quiz_build_creator_id", None)
    
    await update.message.reply_text("✅ Timer set! Creating your quiz summary...")
    await show_summary_panel_text(update, context, qid)
    return ConversationHandler.END

async def view_my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches and displays all quizzes created by the user"""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT quiz_id, title FROM quizzes WHERE creator_id = ? ORDER BY quiz_id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        keyboard = [[InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")]]
        await query.edit_message_text(
            text="❌ Aapne abhi tak koi quiz nahi banaya hai!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = "📚 **Aapke Banaye Huye Quizzes:**\n\nNiche kisi bhi quiz par click karke uska summary panel open karein:\n"
    keyboard = []
    for qid, title in rows:
        keyboard.append([InlineKeyboardButton(f"📝 {title}", callback_data=f"viewq_{qid}")])
    
    keyboard.append([InlineKeyboardButton("Back to Main Menu 🔙", callback_data="back_main")])
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_view_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles opening summary panel from the quiz list"""
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    await query.message.delete()
    await show_summary_panel(query, context, quiz_id)

async def handle_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns to the original main greeting menu"""
    query = update.callback_query
    await query.answer()
    welcome_text = (
        "👋 **Welcome to Laado Quiz Bot!**\n\n"
        "Niche diye gaye buttons se aap apna naya quiz bana sakte hain ya pehle banaye huye quizzes dekh sakte hain:\n\n"
        "🚀 /newquiz - Naya Quiz banana shuru karein\n"
        "🖥️ /help - Help Menu"
    )
    keyboard = [
        [InlineKeyboardButton("Create New Quiz 🚀", callback_data="btn_newquiz")],
        [InlineKeyboardButton("View My Quizzes 📚", callback_data="btn_viewquizzes")]
    ]
    await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_summary_panel(query, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            await query.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username if context.bot.username else "quiz_bot"
        escaped_title = escape_markdown(title)
        
        summary_text = (
            "👍 Here's your quiz:\n\n"
            f"📚 {escaped_title}\n"
            f"🙋‍♂️ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"`https://t.me/{bot_username}?start=quiz_{quiz_id}`"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("🏁 Start Solo Quiz", callback_data=f"runsolo_{quiz_id}")],
            [InlineKeyboardButton("👥 Start in Group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("📢 Share Quiz", url=f"https://t.me/share/url?url=https://t.me/{bot_username}?start=quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit", callback_data=f"edit_{quiz_id}"), InlineKeyboardButton("📊 Status", callback_data=f"status_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await query.message.reply_text(summary_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in show_summary_panel: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)}")

async def show_summary_panel_text(update, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            await update.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username if context.bot.username else "quiz_bot"
        escaped_title = escape_markdown(title)
        
        summary_text = (
            "👍 Quiz created successfully!\n\n"
            "🏁 Here's your quiz:\n"
            f"📚 {escaped_title}\n"
            f"🙋‍♂️ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"`https://t.me/{bot_username}?start=quiz_{quiz_id}`"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("🏁 Start Solo Quiz", callback_data=f"runsolo_{quiz_id}")],
            [InlineKeyboardButton("👥 Start in Group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("📢 Share Quiz", url=f"https://t.me/share/url?url=https://t.me/{bot_username}?start=quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit", callback_data=f"edit_{quiz_id}"), InlineKeyboardButton("📊 Status", callback_data=f"status_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await update.message.reply_text(summary_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Error in show_summary_panel_text: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def handle_run_solo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle solo quiz start"""
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    
    await query.edit_message_text(
        text="🎮 **Solo Mode**\n\nAap akele is quiz ko start karne ke liye ready ho gaye?\n\nClick 'Confirm' to begin!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Start", callback_data=f"confirm_solo_{quiz_id}")]
        ])
    )

async def handle_confirm_solo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and start solo quiz"""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    quiz_id = int(query.data.split("_")[2])
    
    await query.answer("🚀 Quiz shuru ho rahi hai!")
    await query.edit_message_text("⏳ Quiz loading... Please wait!")
    
    if chat_id not in GROUP_GAMES:
        GROUP_GAMES[chat_id] = {
            "quiz_id": quiz_id,
            "joined_users": {user_id: query.from_user.first_name or "Player"},
            "current_q": 0,
            "scores": {user_id: {"score": 0, "total_time": 0.0}},
            "poll_map": {},
            "start_time": None,
            "user_answers": {user_id: {}},
            "question_start_times": {},
            "ready_users": {user_id},
            "quiz_started": True
        }
    
    await asyncio.sleep(1)
    asyncio.create_task(send_next_group_poll(chat_id, context))

async def handle_quiz_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show quiz status/statistics"""
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    quiz_data = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
    total_q = cursor.fetchone()
    conn.close()
    
    if not quiz_data:
        await query.edit_message_text(text="❌ Quiz not found!")
        return
    
    title, desc, timer = quiz_data
    time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
    
    status_text = (
        f"📊 **Quiz Status**\n\n"
        f"📚 **Title:** {escape_markdown(title)}\n"
        f"ℹ️ **Description:** {escape_markdown(desc) if desc else 'No description'}\n"
        f"❓ **Total Questions:** {total_q[0]}\n"
        f"⏱️ **Time per Q:** {time_display}\n"
        f"✅ **Status:** Active"
    )
    
    await query.edit_message_text(
        text=status_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"viewq_{quiz_id}")]
        ]),
        parse_mode="Markdown"
    )

async def edit_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    
    keyboard = [
        [InlineKeyboardButton("📝 Edit title", callback_data=f"edtitle_{quiz_id}")],
        [InlineKeyboardButton("ℹ️ Edit description", callback_data=f"eddesc_{quiz_id}")],
        [InlineKeyboardButton("⏱ Edit timer settings", callback_data=f"edtime_{quiz_id}")],
        [InlineKeyboardButton("Back 🔙", callback_data=f"backto_{quiz_id}")]
    ]
    await query.edit_message_text(
        text="⚙️ **Edit Quiz Menu**\n\nAap is quiz ka kya badalna chahte hain? Niche se chunyein:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def back_to_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    await query.message.delete()
    await show_summary_panel(query, context, quiz_id)

# ==========================================
# ⚙️ FULLY OPERATIONAL QUIZ EDITOR HANDLERS
# ==========================================

async def edit_title_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    context.user_data["editing_quiz_id"] = quiz_id
    await query.message.reply_text("📝 Please send the **new title** for your quiz:")
    return EDIT_TITLE

async def save_edited_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_title = update.message.text.strip()
    quiz_id = context.user_data.get("editing_quiz_id")
    
    if not quiz_id:
        await update.message.reply_text("❌ Error: Session expired. Restart using menu.")
        return ConversationHandler.END
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET title = ? WHERE quiz_id = ?", (new_title, quiz_id))
    conn.commit()
    conn.close()
    
    context.user_data.pop("editing_quiz_id", None)
    await update.message.reply_text("✅ Quiz title successfully updated!")
    await show_summary_panel_text(update, context, quiz_id)
    return ConversationHandler.END

async def edit_desc_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    context.user_data["editing_quiz_id"] = quiz_id
    await query.message.reply_text("ℹ️ Please send the **new description** for your quiz (or type /skip to remove it):")
    return EDIT_DESC

async def save_edited_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    new_desc = "" if text.lower() == "/skip" else text
    quiz_id = context.user_data.get("editing_quiz_id")
    
    if not quiz_id:
        await update.message.reply_text("❌ Error: Session expired.")
        return ConversationHandler.END
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET description = ? WHERE quiz_id = ?", (new_desc, quiz_id))
    conn.commit()
    conn.close()
    
    context.user_data.pop("editing_quiz_id", None)
    await update.message.reply_text("✅ Quiz description successfully updated!")
    await show_summary_panel_text(update, context, quiz_id)
    return ConversationHandler.END

async def edit_timer_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split("_")[1])
    context.user_data["editing_quiz_id"] = quiz_id
    await query.message.reply_text("⏱ Please enter the new per-question timer limit: (15, 30, 40, or 60)")
    return EDIT_TIMER

async def save_edited_timer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    time_map = {"15": 15, "30": 30, "40": 40, "60": 60}
    
    if text not in time_map:
        await update.message.reply_text("❌ Invalid entry! Please type exactly 15, 30, 40, or 60:")
        return EDIT_TIMER
        
    quiz_id = context.user_data.get("editing_quiz_id")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quizzes SET timer = ? WHERE quiz_id = ?", (time_map[text], quiz_id))
    conn.commit()
    conn.close()
    
    context.user_data.pop("editing_quiz_id", None)
    await update.message.reply_text("✅ Quiz timer configuration updated!")
    await show_summary_panel_text(update, context, quiz_id)
    return ConversationHandler.END


# ==========================================
# 🎯 SINGLE READY BUTTON DRIVEN ACTIVATION
# ==========================================

async def handle_ready_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-joins users and sets dynamic counter to verify activation benchmarks"""
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    user_name = query.from_user.username if query.from_user.username else query.from_user.first_name
    quiz_id = query.data.split("_")[1]
    
    if chat_id not in GROUP_GAMES:
        GROUP_GAMES[chat_id] = {
            "quiz_id": quiz_id, 
            "joined_users": {}, 
            "current_q": 0, 
            "scores": {}, 
            "poll_map": {}, 
            "start_time": None,
            "user_answers": {},  
            "question_start_times": {},
            "ready_users": set(),  
            "quiz_started": False  
        }
        
    game = GROUP_GAMES[chat_id]

    if game["quiz_started"]:
        await query.answer("🚀 Quiz countdown pehle hi shuru ho chuka hai!")
        return

    # Auto-Join structure initialization execution
    if user_id not in game["joined_users"]:
        game["joined_users"][user_id] = f"@{user_name}" if query.from_user.username else user_name
        game["scores"][user_id] = {"score": 0, "total_time": 0.0}
        game["user_answers"][user_id] = {}

    game["ready_users"].add(user_id)
    ready_count = len(game["ready_users"])
    joined_count = len(game["joined_users"])
    names_list = ", ".join(game["joined_users"].values())

    if ready_count >= 2:
        game["quiz_started"] = True
        await query.answer("🎯 Target achieved! Quiz start ho rahi hai...")
        await query.edit_message_text("⚡ All requirements met! Initializing countdown...")
        
        # 5-second dynamic countdown deletion cycles
        for count in ["5️⃣", "4️⃣", "3️⃣", "2️⃣", "1️⃣"]:
            countdown_msg = await context.bot.send_message(chat_id=chat_id, text=count)
            await asyncio.sleep(1)
            await context.bot.delete_message(chat_id=chat_id, message_id=countdown_msg.message_id)

        # 5 seconds banner hold execution
        banner_msg = await context.bot.send_message(chat_id=chat_id, text="🔥 Get ready! Quiz shuru ho rahi hai... 🚀")
        await asyncio.sleep(5)
        await context.bot.delete_message(chat_id=chat_id, message_id=banner_msg.message_id)
        
        game["current_q"] = 0
        asyncio.create_task(send_next_group_poll(chat_id, context))
    else:
        # Dynamic inline text monitoring refresh updates
        keyboard = [[InlineKeyboardButton(f"I am ready! 🎯 ({ready_count}/2)", callback_data=f"ready_{quiz_id}")]]
        await query.edit_message_text(
            text=f"🏁 **Quiz Setup Active**\n\nJoined Users ({joined_count}): {names_list}\n\n*Waiting for 2 users to be ready. ({ready_count}/2 completed)*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        await query.answer("Aapne confirmation register kar di! 👍")

async def send_next_group_poll(chat_id, context):
    game = GROUP_GAMES.get(chat_id)
    if not game: return
        
    qid = game["quiz_id"]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT timer FROM quizzes WHERE quiz_id = ?", (qid,))
    timer_data = cursor.fetchone()
    cursor.execute("SELECT question_text, options, correct_answer, pre_message, explanation FROM questions WHERE quiz_id = ?", (qid,))
    questions = cursor.fetchall()
    conn.close()
    
    if game["current_q"] >= len(questions):
        await compile_group_leaderboard(chat_id, context)
        return

    # Tuple extraction verification execution
    timer = timer_data[0] if (timer_data and isinstance(timer_data, tuple)) else 30
    q = questions[game["current_q"]]
    q_text, options_json, correct_ans, pre_msg, explanation = q
    options = json.loads(options_json)
    correct_idx = options.index(correct_ans)
    
    if pre_msg:
        await context.bot.send_message(chat_id=chat_id, text=f"📢 Context: {pre_msg}")
        await asyncio.sleep(1)

    game["question_start_times"][game["current_q"]] = datetime.now()
    game["start_time"] = datetime.now()
    
    poll_msg = await context.bot.send_poll(
        chat_id=chat_id, question=f"❓ Q ({game['current_q'] + 1}/{len(questions)}): {q_text}",
        options=options, type="quiz", correct_option_id=correct_idx,
        explanation=explanation if explanation else None, is_anonymous=False
    )
    
    game["poll_map"][poll_msg.poll.id] = {
        "correct_idx": correct_idx, 
        "chat_id": chat_id,
        "correct_answer": correct_ans,
        "question_index": game["current_q"]
    }
    
    await asyncio.sleep(timer)
    game["current_q"] += 1
    asyncio.create_task(send_next_group_poll(chat_id, context))

async def track_poll_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track poll answers from ALL users who participate, even if they didn't click Ready"""
    ans = update.poll_answer
    pid = ans.poll_id
    uid = ans.user.id
    user_name = ans.user.first_name or "Player"
    
    for cid, game in list(GROUP_GAMES.items()):
        if pid in game["poll_map"]:
            poll_info = game["poll_map"][pid]
            correct_idx = poll_info["correct_idx"]
            question_idx = poll_info["question_index"]
            
            # 🔴 FIX: Auto-add user to joined_users if they participate
            if uid not in game["joined_users"]:
                game["joined_users"][uid] = user_name
                game["scores"][uid] = {"score": 0, "total_time": 0.0}
                game["user_answers"][uid] = {}
                logging.info(f"New participant added: {user_name} (ID: {uid})")
            
            if uid not in game["user_answers"]:
                game["user_answers"][uid] = {}
            
            # Numeric single index list matching evaluation mapping conversion
            selected_idx = ans.option_ids[0] if ans.option_ids else -1
            game["user_answers"][uid][question_idx] = {
                "selected": selected_idx,  
                "correct_idx": correct_idx,
                "timestamp": datetime.now()
            }

async def compile_group_leaderboard(chat_id, context):
    game = GROUP_GAMES.get(chat_id)
    if not game: return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM quizzes WHERE quiz_id = ?", (game["quiz_id"],))
    quiz_title_data = cursor.fetchone()
    quiz_title = quiz_title_data[0] if quiz_title_data else "Quiz"
    
    cursor.execute("SELECT question_text, options, correct_answer FROM questions WHERE quiz_id = ?", (game["quiz_id"],))
    questions = cursor.fetchall()
    conn.close()
    
    correct_answers = {}
    for idx, (q_text, options_json, correct_ans) in enumerate(questions):
        options = json.loads(options_json)
        correct_answers[idx] = options.index(correct_ans)
    
    final_scores = {}
    # Include ALL users who attempted the quiz
    for uid in game["user_answers"].keys():
        final_scores[uid] = {"score": 0, "wrong": 0, "total_time": 0.0}

    for uid, user_answers in game["user_answers"].items():
        score = 0
        wrong = 0
        total_time = 0.0
        
        for question_idx, answer_data in user_answers.items():
            selected_idx = answer_data["selected"]  
            correct_idx = correct_answers.get(question_idx, -1)
            
            if selected_idx == correct_idx:
                score += 1
                start_time = game["question_start_times"].get(question_idx, answer_data["timestamp"])
                if isinstance(start_time, datetime):
                    elapsed = (answer_data["timestamp"] - start_time).total_seconds()
                    total_time += elapsed
            else:
                wrong += 1
        
        final_scores[uid] = {"score": score, "wrong": wrong, "total_time": total_time}
    
    sorted_scores = sorted(final_scores.items(), key=lambda item: (-item[1]["score"], item[1]["total_time"]))[:50]
    
    # ============ NEW RESULT DESIGN ============
    header = f"🏁 The quiz '{escape_markdown(quiz_title)}' has finished!\n\n"
    
    # Count total questions answered
    total_questions_answered = len(questions)
    subheader = f"📋 {total_questions_answered} questions answered\n"
    subheader += f"👥 Total Participants: {len(final_scores)}\n\n"
    
    # Build leaderboard with new design
    leaderboard = ""
    for idx, (uid, meta) in enumerate(sorted_scores, 1):
        user_name = game["joined_users"].get(uid, "Unknown User")
        score = meta["score"]
        total_time = format_time(meta["total_time"])
        
        # Determine rank/medal
        if idx == 1:
            rank_icon = "🥇"
        elif idx == 2:
            rank_icon = "🥈"
        elif idx == 3:
            rank_icon = "🥉"
        else:
            rank_icon = f"{idx}."
        
        # Format entry with new design
        leaderboard += f"{rank_icon} 💗 {user_name}\n"
        leaderboard += f"   📊 Total Score: {score}/{total_questions_answered}\n"
        leaderboard += f"   ⏱️ Total Time: ({total_time})\n\n"
    
    # Add congratulations footer
    footer = "🏆 Congratulations to all participants!"
    
    # Combine all parts
    full_message = header + subheader + leaderboard + footer
    
    kb = [[InlineKeyboardButton("📢 Share Score", url="https://t.me/share/url?url=I%20played%20Laado%20Quiz%20Bot%20Challenge!")]]
    
    await context.bot.send_message(chat_id=chat_id, text=full_message, reply_markup=InlineKeyboardMarkup(kb))
    GROUP_GAMES.pop(chat_id, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Setup cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    if not BOT_TOKEN: return
    app = Application.builder().token(BOT_TOKEN).build()
    
    # 🔁 COMPREHENSIVE DUAL CONVERSATION ROUTER MAPS (Creation + Live Editing)
    new_quiz_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newquiz", new_quiz_start),
            CallbackQueryHandler(new_quiz_start, pattern="^btn_newquiz$")
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc), CommandHandler("skip", receive_desc)],
            QUESTIONS: [CommandHandler("undo", handle_undo), CommandHandler("done", finish_quiz_creation), MessageHandler(filters.POLL, receive_poll)],
            TIMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timer_text)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    quiz_edit_flow_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_title_trigger, pattern="^edtitle_"),
            CallbackQueryHandler(edit_desc_trigger, pattern="^eddesc_"),
            CallbackQueryHandler(edit_timer_trigger, pattern="^edtime_")
        ],
        states={
            EDIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_title)],
            EDIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_desc)],
            EDIT_TIMER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_edited_timer)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Registering core structures hooks
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    
    app.add_handler(new_quiz_handler)
    app.add_handler(quiz_edit_flow_handler)

    # Core system triggers binding maps
    app.add_handler(CallbackQueryHandler(view_my_quizzes, pattern="^btn_viewquizzes$"))
    app.add_handler(CallbackQueryHandler(handle_back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(handle_view_quiz_callback, pattern="^viewq_"))
    
    app.add_handler(CallbackQueryHandler(handle_ready_click, pattern="^ready_"))
    app.add_handler(CallbackQueryHandler(handle_run_solo, pattern="^runsolo_"))
    app.add_handler(CallbackQueryHandler(handle_confirm_solo, pattern="^confirm_solo_"))
    app.add_handler(CallbackQueryHandler(handle_quiz_status, pattern="^status_"))
    app.add_handler(CallbackQueryHandler(edit_quiz_menu, pattern="^edit_"))
    app.add_handler(CallbackQueryHandler(back_to_summary, pattern="^backto_"))
    
    app.add_handler(PollAnswerHandler(track_poll_answers))
    
    print("🚀 Advanced Telegram Quiz-Bot UI Active...")
    app.run_polling()

if __name__ == "__main__":
    main()
