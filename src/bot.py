import logging
import asyncio
from math import ceil
from datetime import time as dtime
from zoneinfo import ZoneInfo

from recipe_scrapers import scrape_me
from bson import ObjectId
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from .config import BOT_TOKEN, ADMIN_CHAT_ID_INT, SUGGESTION_COUNT, TIMEZONE
from .db import get_db
from .llm import llm_enabled, llm_parse_ingredients
from .suggestions import build_suggestions, record_feedback
from .utils import normalize_item, parse_item, now_utc, simplify_ingredient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_authorized(update: Update) -> bool:
    if ADMIN_CHAT_ID_INT is None:
        return True
    chat = update.effective_chat
    return chat and chat.id == ADMIN_CHAT_ID_INT


async def guard(update: Update):
    if not is_authorized(update):
        if update.message:
            await update.message.reply_text("Sorry, this bot is restricted to the admin chat.")
        elif update.callback_query:
            await update.callback_query.answer("Not authorized.", show_alert=True)
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    db = get_db()
    chat = update.effective_chat
    await db.chats.update_one(
        {"chat_id": chat.id},
        {
            "$set": {
                "chat_id": chat.id,
                "title": chat.title,
                "username": chat.username,
                "updated_at": now_utc(),
            },
            "$setOnInsert": {"created_at": now_utc()},
        },
        upsert=True,
    )

    await update.message.reply_text(
        "Ready. Use /add <item> to add groceries, /list to see your list, and /suggest for weekly proposals."
    )


async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    text = " ".join(context.args)
    name_raw = parse_item(text)
    if not name_raw:
        await update.message.reply_text("Usage: /add <item>")
        return

    name = normalize_item(name_raw)
    db = get_db()
    items = db.items
    stats = db.stats

    await items.update_one(
        {"chat_id": update.effective_chat.id, "name": name},
        {
            "$set": {"display_name": name_raw, "updated_at": now_utc()},
            "$setOnInsert": {
                "chat_id": update.effective_chat.id,
                "name": name,
                "created_at": now_utc(),
            },
        },
        upsert=True,
    )

    await stats.update_one(
        {"chat_id": update.effective_chat.id, "name": name},
        {
            "$inc": {"accepts": 1},
            "$set": {"display_name": name_raw, "updated_at": now_utc()},
            "$setOnInsert": {
                "chat_id": update.effective_chat.id,
                "name": name,
                "created_at": now_utc(),
                "accepts": 0,
                "rejects": 0,
            },
        },
        upsert=True,
    )

    await update.message.reply_text(f"Added {name_raw}.")


async def list_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    db = get_db()
    cursor = db.items.find({"chat_id": update.effective_chat.id}).sort("display_name", 1)

    lines = []
    async for doc in cursor:
        display = doc.get("display_name", doc.get("name"))
        lines.append(f"- {display}")

    if not lines:
        await update.message.reply_text("Your list is empty.")
        return

    await update.message.reply_text("Your grocery list:\n" + "\n".join(lines))


async def remove_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    text = " ".join(context.args)
    name_raw = parse_item(text)
    if not name_raw:
        await start_remove_session_ui(update)
        return

    name = normalize_item(name_raw)
    db = get_db()
    items = db.items

    result = await items.delete_one({"chat_id": update.effective_chat.id, "name": name})
    if result.deleted_count == 0:
        await update.message.reply_text("Item not found in your list.")
    else:
        await update.message.reply_text(f"Removed {name_raw}.")


async def remove_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    text = " ".join(context.args)
    name_raw = text.strip()
    if not name_raw:
        await update.message.reply_text("Usage: /removeall <item>")
        return

    name = normalize_item(name_raw)
    db = get_db()
    result = await db.items.delete_one({"chat_id": update.effective_chat.id, "name": name})

    if result.deleted_count == 0:
        await update.message.reply_text("Item not found in your list.")
    else:
        await update.message.reply_text(f"Removed all of {name_raw}.")


async def clear_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    db = get_db()
    await db.items.delete_many({"chat_id": update.effective_chat.id})
    await update.message.reply_text("Cleared your grocery list.")


async def send_suggestions(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    items_cursor = db.items.find({"chat_id": chat_id})
    current_items = [doc async for doc in items_cursor]

    suggestions = await build_suggestions(db, chat_id, current_items, SUGGESTION_COUNT)
    if not suggestions:
        await context.bot.send_message(chat_id=chat_id, text="No suggestions yet. Add items over time and I’ll learn.")
        return

    batch = {
        "chat_id": chat_id,
        "items": suggestions,
        "created_at": now_utc(),
        "responses": {},
    }
    result = await db.suggestion_batches.insert_one(batch)
    batch_id = str(result.inserted_id)

    keyboard = []
    for idx, item in enumerate(suggestions):
        label = item.get("display_name", item.get("name"))
        keyboard.append([
            InlineKeyboardButton(f"Add {label}", callback_data=f"a:{batch_id}:{idx}"),
            InlineKeyboardButton("Skip", callback_data=f"r:{batch_id}:{idx}"),
        ])

    await context.bot.send_message(
        chat_id=chat_id,
        text="Weekly suggestions:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def fetch_recipe_ingredients(url: str):
    def _scrape():
        scraper = scrape_me(url)
        title = scraper.title() or "Recipe"
        ingredients = scraper.ingredients() or []
        return title, ingredients

    return await asyncio.to_thread(_scrape)


async def start_recipe_session(chat_id: int, url: str, title: str, ingredients: list[str]):
    db = get_db()
    session = {
        "chat_id": chat_id,
        "url": url,
        "title": title,
        "ingredients": ingredients,
        "selected": [],
        "page": 0,
        "created_at": now_utc(),
    }
    result = await db.recipe_sessions.insert_one(session)
    session["_id"] = result.inserted_id
    return session


def build_recipe_keyboard(session, page: int, page_size: int = 8):
    ingredients = session.get("ingredients", [])
    selected = set(session.get("selected", []))
    total_pages = max(1, ceil(len(ingredients) / page_size))
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    end = start + page_size
    rows = []

    for idx in range(start, min(end, len(ingredients))):
        label = ingredients[idx]
        prefix = "✓ " if idx in selected else ""
        rows.append([
            InlineKeyboardButton(f"{prefix}{label}", callback_data=f"ri:{session['_id']}:{idx}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"rp:{session['_id']}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next", callback_data=f"rp:{session['_id']}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("Select all", callback_data=f"ra:{session['_id']}"),
        InlineKeyboardButton("Clear all", callback_data=f"rc:{session['_id']}"),
    ])
    rows.append([
        InlineKeyboardButton("Save to list", callback_data=f"rs:{session['_id']}"),
    ])

    return InlineKeyboardMarkup(rows), page, total_pages, len(selected)


def recipe_header(title: str, page: int, total_pages: int, selected_count: int):
    return f"Ingredients for {title} (page {page + 1}/{total_pages}, selected {selected_count}):"


async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    await send_suggestions(update.effective_chat.id, context)


async def recipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    url = " ".join(context.args).strip()
    if not url:
        await update.message.reply_text("Usage: /recipe <url>")
        return

    try:
        title, ingredients = await fetch_recipe_ingredients(url)
    except Exception:
        await update.message.reply_text("I couldn't read that recipe URL. Try another one.")
        return

    if not ingredients:
        await update.message.reply_text("No ingredients found on that page.")
        return

    if llm_enabled():
        parsed = await llm_parse_ingredients(title, ingredients)
        if parsed:
            ingredients = parsed

    session = await start_recipe_session(update.effective_chat.id, url, title, ingredients)
    keyboard, page, total_pages, selected_count = build_recipe_keyboard(session, 0)
    await update.message.reply_text(
        recipe_header(title, page, total_pages, selected_count),
        reply_markup=keyboard,
    )


async def start_remove_session(chat_id: int, items: list[dict]):
    db = get_db()
    session = {
        "chat_id": chat_id,
        "items": items,
        "selected": [],
        "page": 0,
        "created_at": now_utc(),
    }
    result = await db.remove_sessions.insert_one(session)
    session["_id"] = result.inserted_id
    return session


def build_remove_keyboard(session, page: int, page_size: int = 8):
    items = session.get("items", [])
    selected = set(session.get("selected", []))
    total_pages = max(1, ceil(len(items) / page_size))
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    end = start + page_size
    rows = []

    for idx in range(start, min(end, len(items))):
        label = items[idx].get("display_name", items[idx].get("name", "item"))
        prefix = "✓ " if idx in selected else ""
        rows.append([
            InlineKeyboardButton(f"{prefix}{label}", callback_data=f"rmi:{session['_id']}:{idx}")
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"rmp:{session['_id']}:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next", callback_data=f"rmp:{session['_id']}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("Select all", callback_data=f"rma:{session['_id']}"),
        InlineKeyboardButton("Clear all", callback_data=f"rmc:{session['_id']}"),
    ])
    rows.append([
        InlineKeyboardButton("Remove selected", callback_data=f"rms:{session['_id']}"),
    ])

    return InlineKeyboardMarkup(rows), page, total_pages, len(selected)


def remove_header(page: int, total_pages: int, selected_count: int):
    return f"Select items to remove (page {page + 1}/{total_pages}, selected {selected_count}):"


async def start_remove_session_ui(update: Update):
    db = get_db()
    cursor = db.items.find({"chat_id": update.effective_chat.id}).sort("display_name", 1)
    items = [doc async for doc in cursor]
    if not items:
        await update.message.reply_text("Your list is empty.")
        return
    session = await start_remove_session(update.effective_chat.id, items)
    keyboard, page, total_pages, selected_count = build_remove_keyboard(session, 0)
    await update.message.reply_text(
        remove_header(page, total_pages, selected_count),
        reply_markup=keyboard,
    )


async def handle_suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    query = update.callback_query
    await query.answer()

    try:
        action, batch_id, idx_str = query.data.split(":", 2)
        idx = int(idx_str)
    except Exception:
        await query.answer("Invalid action.", show_alert=True)
        return

    db = get_db()
    try:
        batch_oid = ObjectId(batch_id)
    except Exception:
        await query.answer("Invalid batch.", show_alert=True)
        return

    batch = await db.suggestion_batches.find_one({"_id": batch_oid})
    if not batch:
        await query.answer("Suggestion batch expired.", show_alert=True)
        return

    items = batch.get("items", [])
    if idx < 0 or idx >= len(items):
        await query.answer("Invalid item.", show_alert=True)
        return

    responses = batch.get("responses", {})
    if str(idx) in responses:
        await query.answer("Already recorded.")
        return

    item = items[idx]
    name = item.get("name")
    display_name = item.get("display_name", name)

    if action == "a":
        await add_item_to_list(db, batch["chat_id"], name, display_name)
        await record_feedback(db, batch["chat_id"], name, display_name, True)
        response_text = f"Added {display_name}."
    else:
        await record_feedback(db, batch["chat_id"], name, display_name, False)
        response_text = f"Skipped {display_name}."

    responses[str(idx)] = {"action": action, "at": now_utc()}
    await db.suggestion_batches.update_one({"_id": batch_oid}, {"$set": {"responses": responses}})

    await query.message.reply_text(response_text)


async def handle_recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) < 2:
        await query.answer("Invalid action.", show_alert=True)
        return

    action = parts[0]
    session_id = parts[1]

    try:
        session_oid = ObjectId(session_id)
    except Exception:
        await query.answer("Invalid session.", show_alert=True)
        return

    db = get_db()
    session = await db.recipe_sessions.find_one({"_id": session_oid})
    if not session:
        await query.answer("Session expired.", show_alert=True)
        return

    ingredients = session.get("ingredients", [])
    selected = set(session.get("selected", []))

    if action == "ri":
        if len(parts) < 3:
            await query.answer("Invalid item.", show_alert=True)
            return
        try:
            idx = int(parts[2])
        except Exception:
            await query.answer("Invalid item.", show_alert=True)
            return
        if idx < 0 or idx >= len(ingredients):
            await query.answer("Invalid item.", show_alert=True)
            return
        if idx in selected:
            selected.remove(idx)
        else:
            selected.add(idx)
        await db.recipe_sessions.update_one({"_id": session_oid}, {"$set": {"selected": sorted(selected)}})
        session["selected"] = sorted(selected)

    elif action == "ra":
        selected = set(range(len(ingredients)))
        await db.recipe_sessions.update_one({"_id": session_oid}, {"$set": {"selected": sorted(selected)}})
        session["selected"] = sorted(selected)

    elif action == "rc":
        selected = set()
        await db.recipe_sessions.update_one({"_id": session_oid}, {"$set": {"selected": []}})
        session["selected"] = []

    elif action == "rp":
        if len(parts) < 3:
            await query.answer("Invalid page.", show_alert=True)
            return
        try:
            page = int(parts[2])
        except Exception:
            await query.answer("Invalid page.", show_alert=True)
            return
        keyboard, page, total_pages, selected_count = build_recipe_keyboard(session, page)
        await db.recipe_sessions.update_one({"_id": session_oid}, {"$set": {"page": page}})
        await query.edit_message_text(
            recipe_header(session.get("title", "Recipe"), page, total_pages, selected_count),
            reply_markup=keyboard,
        )
        return

    elif action == "rs":
        if not selected:
            await query.message.reply_text("No ingredients selected.")
            return
        for idx in sorted(selected):
            raw = ingredients[idx]
            simplified = simplify_ingredient(raw)
            name = normalize_item(simplified)
            await add_item_to_list(db, session["chat_id"], name, simplified)
        await db.recipe_sessions.delete_one({"_id": session_oid})
        await query.message.delete()
        await query.message.reply_text("Selected ingredients added to your list.")
        return

    keyboard, page, total_pages, selected_count = build_recipe_keyboard(session, session.get("page", 0))
    await db.recipe_sessions.update_one({"_id": session_oid}, {"$set": {"page": page}})
    await query.edit_message_text(
        recipe_header(session.get("title", "Recipe"), page, total_pages, selected_count),
        reply_markup=keyboard,
    )


async def handle_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) < 2:
        await query.answer("Invalid action.", show_alert=True)
        return

    action = parts[0]
    session_id = parts[1]

    try:
        session_oid = ObjectId(session_id)
    except Exception:
        await query.answer("Invalid session.", show_alert=True)
        return

    db = get_db()
    session = await db.remove_sessions.find_one({"_id": session_oid})
    if not session:
        await query.answer("Session expired.", show_alert=True)
        return

    items = session.get("items", [])
    selected = set(session.get("selected", []))

    if action == "rmi":
        if len(parts) < 3:
            await query.answer("Invalid item.", show_alert=True)
            return
        try:
            idx = int(parts[2])
        except Exception:
            await query.answer("Invalid item.", show_alert=True)
            return
        if idx < 0 or idx >= len(items):
            await query.answer("Invalid item.", show_alert=True)
            return
        if idx in selected:
            selected.remove(idx)
        else:
            selected.add(idx)
        await db.remove_sessions.update_one({"_id": session_oid}, {"$set": {"selected": sorted(selected)}})
        session["selected"] = sorted(selected)

    elif action == "rma":
        selected = set(range(len(items)))
        await db.remove_sessions.update_one({"_id": session_oid}, {"$set": {"selected": sorted(selected)}})
        session["selected"] = sorted(selected)

    elif action == "rmc":
        selected = set()
        await db.remove_sessions.update_one({"_id": session_oid}, {"$set": {"selected": []}})
        session["selected"] = []

    elif action == "rmp":
        if len(parts) < 3:
            await query.answer("Invalid page.", show_alert=True)
            return
        try:
            page = int(parts[2])
        except Exception:
            await query.answer("Invalid page.", show_alert=True)
            return
        keyboard, page, total_pages, selected_count = build_remove_keyboard(session, page)
        await db.remove_sessions.update_one({"_id": session_oid}, {"$set": {"page": page}})
        await query.edit_message_text(
            remove_header(page, total_pages, selected_count),
            reply_markup=keyboard,
        )
        return

    elif action == "rms":
        if not selected:
            await query.message.reply_text("No items selected.")
            return
        names = []
        for idx in sorted(selected):
            if idx < len(items):
                name = items[idx].get("name")
                if name:
                    names.append(name)
        if names:
            await db.items.delete_many({"chat_id": session["chat_id"], "name": {"$in": names}})
        await db.remove_sessions.delete_one({"_id": session_oid})
        await query.message.delete()
        await query.message.reply_text("Selected items removed.")
        return

    keyboard, page, total_pages, selected_count = build_remove_keyboard(session, session.get("page", 0))
    await db.remove_sessions.update_one({"_id": session_oid}, {"$set": {"page": page}})
    await query.edit_message_text(
        remove_header(page, total_pages, selected_count),
        reply_markup=keyboard,
    )


async def add_item_to_list(db, chat_id: int, name: str, display_name: str):
    await db.items.update_one(
        {"chat_id": chat_id, "name": name},
        {
            "$set": {"display_name": display_name, "updated_at": now_utc()},
            "$setOnInsert": {
                "chat_id": chat_id,
                "name": name,
                "created_at": now_utc(),
            },
        },
        upsert=True,
    )


async def weekly_job(context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    cursor = db.chats.find({})
    async for chat in cursor:
        chat_id = chat.get("chat_id")
        if not chat_id:
            continue
        try:
            await send_suggestions(chat_id, context)
        except Exception as exc:
            logger.exception("Failed to send weekly suggestions to %s: %s", chat_id, exc)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text("Unknown command. Try /help to see available commands.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = (
        "Available commands:\n"
        "/add <item> — add an item\n"
        "/remove — select items to remove\n"
        "/remove <item> — remove a specific item\n"
        "/removeall <item> — remove all of a specific item\n"
        "/clear — clear the whole list\n"
        "/list — show the current list\n"
        "/suggest — get weekly suggestions now\n"
        "/recipe <url> — import ingredients from a recipe URL\n"
        "/help — show this help"
    )
    await update.message.reply_text(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_item))
    app.add_handler(CommandHandler("list", list_items))
    app.add_handler(CommandHandler("remove", remove_item))
    app.add_handler(CommandHandler("removeall", remove_all))
    app.add_handler(CommandHandler("clear", clear_list))
    app.add_handler(CommandHandler("suggest", suggest_command))
    app.add_handler(CommandHandler("recipe", recipe_command))
    app.add_handler(CallbackQueryHandler(handle_suggestion_callback, pattern=r"^(a|r):"))
    app.add_handler(CallbackQueryHandler(handle_recipe_callback, pattern=r"^(ri|ra|rc|rs|rp):"))
    app.add_handler(CallbackQueryHandler(handle_remove_callback, pattern=r"^(rmi|rmp|rma|rmc|rms):"))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)

    tz = ZoneInfo(TIMEZONE)
    app.job_queue.run_daily(weekly_job, time=dtime(hour=9, minute=0, tzinfo=tz), days=(0,))

    app.run_polling()


if __name__ == "__main__":
    main()
