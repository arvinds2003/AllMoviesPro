import os
import threading
import asyncio
from flask import Flask
import bot  # yaha apna bot.py import hoga

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Telegram Bot is running on Render!"

def run_bot():
    try:
        asyncio.run(bot.main())  # bot.py ka main() run hoga
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    # Bot ko background thread me chalao
    threading.Thread(target=run_bot).start()

    # Flask web server start karo
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
  
