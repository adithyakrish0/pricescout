"""
Minimal Gradio wrapper to keep PriceScout bot alive on HF Spaces.
The bot runs in a background thread; Gradio just provides a keep-alive UI.
"""
import threading
import sys
import io

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def start_bot():
    """Import and run the Telegram bot in a background thread."""
    import time
    time.sleep(2)  # let Gradio start first
    from bot import main
    main()

# Start bot in background thread
bot_thread = threading.Thread(target=start_bot, daemon=True)
bot_thread.start()

# Minimal Gradio UI (just a status page)
try:
    import gradio as gr

    with gr.Blocks(title="PriceScout") as demo:
        gr.Markdown("""
# 🛒 PriceScout Bot

**Telegram bot is running!**

Open Telegram and search for **@my_pricescout_bot** to use it.

Send any Amazon.in or Flipkart product URL and get:
- Current price & details
- Price history chart
- All-time low/high
- Cross-platform comparison
        """)

    demo.launch(server_name="0.0.0.0", server_port=7860)
except Exception:
    # If Gradio fails, just keep the bot running without UI
    import time
    while True:
        time.sleep(60)
