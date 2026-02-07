# Grocery Telegram Bot (Python + MongoDB)

A Telegram bot that keeps your grocery list, learns from what you add/accept, and sends weekly suggestions every Monday at 9:00.

## Features
- `/add <item>` add items
- `/remove <item>` remove an item
- `/removeall <item>` remove all of a specific item
- `/clear` clear the whole list
- `/list` show the current list
- `/suggest` send suggestions now
- `/recipe <url>` import ingredients from a recipe URL and pick what to add
- `/id` show this chat id
- `/help` show all commands
- Weekly suggestions every Monday at 09:00 (server timezone)

## Setup

1. Create and activate a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create your `.env`:
```bash
cp .env.example .env
```

Fill in:
- `BOT_TOKEN` from BotFather
- `MONGO_URI` (e.g. `mongodb://localhost:27017`)
- `MONGO_DB` (default is `grocery_bot`)
- `ADMIN_CHAT_ID` (your chat id, to restrict commands)
- `TIMEZONE` (e.g. `America/Chicago`)
- `OPENAI_API_KEY` (optional, enables smarter parsing and suggestions)
- `OPENAI_MODEL` (default `gpt-4.1`)
- `OPENAI_TEMPERATURE` (default `0.2`)

4. Run the bot:
```bash
python -m src.bot
```

## Ubuntu (systemd)

Create a service file:
```bash
sudo nano /etc/systemd/system/grocery-bot.service
```

Example:
```ini
[Unit]
Description=Grocery Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/path/to/Groceries
EnvironmentFile=/path/to/Groceries/.env
ExecStart=/path/to/Groceries/.venv/bin/python -m src.bot
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable grocery-bot
sudo systemctl start grocery-bot
sudo systemctl status grocery-bot
```

## Notes
- The bot learns by tracking what you add and what you accept/skip in suggestions.
- Suggestions exclude items already on the list.
- You can change the weekly schedule by editing `TIMEZONE` and the schedule in `src/bot.py`.
- Recipe imports use `recipe-scrapers`; ingredients are shown with checkboxes and saved without quantities when possible.
- When `OPENAI_API_KEY` is set, the bot uses the OpenAI API for smarter ingredient parsing and conservative suggestions.
