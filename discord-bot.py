import discord
from discord.ext import commands, tasks
import json
import asyncio
from datetime import datetime, time
import os
from dotenv import load_dotenv
from flask import Flask
import threading

load_dotenv()

# Flask web server to keep Render active
app = Flask(__name__)

@app.route('/')
def home():
    return "Sleep Enforcer Bot is running! 😴"

@app.route('/health')
def health():
    return {"status": "healthy", "bot": "online"}

def run_flask():
    """Run Flask server in a separate thread"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!sleep_', intents=intents)

# State tracking
last_ping_time = None
escalation_level = 0

def get_current_time():
    """Get current time in 24h format"""
    return datetime.now().time()

def should_send_reminder():
    """Check if we should send a reminder based on time and escalation"""
    current = datetime.now()
    bedtime_hour, bedtime_minute = config['bedtime'].split(':')
    bedtime = time(int(bedtime_hour), int(bedtime_minute))
    
    # Calculate time difference in minutes
    bedtime_datetime = datetime.combine(current.date(), bedtime)
    time_diff = (current - bedtime_datetime).total_seconds() / 60
    
    return time_diff

def get_escalation_level(minutes_past_bedtime):
    """Determine escalation level based on time past bedtime"""
    if minutes_past_bedtime < -15:
        return None  # Too early
    elif minutes_past_bedtime < 0:
        return 0  # Pre-warning (15 min before)
    elif minutes_past_bedtime < 5:
        return 1  # At bedtime
    elif minutes_past_bedtime < 15:
        return 2  # 5 min past - ping every 5 min
    elif minutes_past_bedtime < 25:
        return 3  # 15 min past - ping every 2 min
    else:
        return 4  # 25+ min past - ping EVERY MINUTE

def get_ping_interval(level):
    """Get ping interval in seconds based on escalation level"""
    intervals = {
        0: 900,  # 15 minutes (pre-warning)
        1: 300,  # 5 minutes (at bedtime)
        2: 300,  # 5 minutes
        3: 120,  # 2 minutes
        4: 60    # 1 minute (SPAM MODE)
    }
    return intervals.get(level, 300)

def get_message(level):
    """Get appropriate message based on escalation level"""
    messages = {
        0: "⏰ **15 minutes until bedtime!** Start wrapping up what you're doing.",
        1: "🛏️ **It's bedtime!** Time to sleep for a healthy tomorrow.",
        2: "😴 **You should be in bed by now.** Please head to sleep.",
        3: "⚠️ **SERIOUSLY, GO TO BED!** Your sleep schedule matters!",
        4: "🚨 **SLEEP NOW!!!** Every minute you delay affects your health! GO TO BED IMMEDIATELY!"
    }
    return messages.get(level, "Go to sleep!")

@bot.event
async def on_ready():
    print(f'{bot.user} is now running!')
    print(f'Bedtime set to: {config["bedtime"]}')
    check_bedtime.start()

@tasks.loop(seconds=30)
async def check_bedtime():
    """Check every 30 seconds if we need to send reminders"""
    global last_ping_time, escalation_level
    
    minutes_past = should_send_reminder()
    
    if minutes_past is None:
        # Reset state if we're outside the reminder window
        last_ping_time = None
        escalation_level = 0
        return
    
    current_level = get_escalation_level(minutes_past)
    
    if current_level is None:
        return
    
    # Update escalation level
    escalation_level = current_level
    
    # Check if enough time has passed since last ping
    now = datetime.now()
    interval = get_ping_interval(current_level)
    
    if last_ping_time is None or (now - last_ping_time).total_seconds() >= interval:
        await send_reminders(current_level)
        last_ping_time = now

async def send_reminders(level):
    """Send reminders via DM and server ping"""
    message = get_message(level)
    user_ids = config.get('user_ids', [])
    
    if not user_ids:
        print("No user IDs configured!")
        return
    
    # Send DMs to all users
    users = []
    for user_id in user_ids:
        try:
            user = await bot.fetch_user(int(user_id))
            users.append(user)
            await user.send(f"{user.mention} {message}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] DM sent to {user.name} - Level {level}")
        except discord.Forbidden:
            print(f"Cannot send DM to user {user_id}. Make sure DMs are enabled.")
        except Exception as e:
            print(f"Error sending DM to user {user_id}: {e}")
    
    # Send message in designated server channel with all user mentions
    if config.get('server_id') and config.get('channel_id') and users:
        try:
            guild = bot.get_guild(int(config['server_id']))
            if guild:
                channel = guild.get_channel(int(config['channel_id']))
                if channel:
                    mentions = " ".join([user.mention for user in users])
                    await channel.send(f"{mentions} {message}")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Server ping sent to {len(users)} users - Level {level}")
        except Exception as e:
            print(f"Error sending server message: {e}")

@bot.command(name='status')
async def status(ctx):
    """Check the current status of sleep reminders"""
    minutes_past = should_send_reminder()
    current_level = get_escalation_level(minutes_past) if minutes_past is not None else None
    
    if current_level is None:
        await ctx.send("✅ Not currently in reminder window.")
    else:
        level_names = ["Pre-warning", "Bedtime", "5 min interval", "2 min interval", "SPAM MODE"]
        await ctx.send(f"Current escalation: **{level_names[current_level]}** (Level {current_level})\n"
                      f"Minutes past bedtime: {int(minutes_past)}")

@bot.command(name='test')
@commands.is_owner()
async def test_reminder(ctx, level: int = 4):
    """Test a specific escalation level (bot owner only)"""
    if 0 <= level <= 4:
        await send_reminders(level)
        user_count = len(config.get('user_ids', []))
        await ctx.send(f"Sent test reminder at level {level} to {user_count} user(s)")
    else:
        await ctx.send("Level must be between 0 and 4")

# Run the bot
if __name__ == "__main__":
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not found in .env file!")
    else:
        # Start Flask server in background to keep Render alive
        print("Starting Flask web server...")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        print("Starting Discord bot...")
        bot.run(token)
