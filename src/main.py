import base64
import io
import logging
import random
import math
import re
import sys
import os
from PIL import Image
from matplotlib import pyplot as plt
from datetime import timedelta, datetime
from io import BytesIO
from fuzzywuzzy import process
from telethon import TelegramClient, events
import configparser
import aiofiles
import aiohttp
from shapely.geometry import Polygon
import json
import time
import discord
import asyncio
from discord.ext import commands
import seaborn as sns
import contextily as cx
import pandas as pd
from telethon.errors import SessionPasswordNeededError

FRONT_COMMAND_ALERT_TITLE = "Israel Home Front Command Alert 🚨"

# Load configuration
with open('config.json') as config_file:
    config = json.load(config_file)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Example usage of config
TOKEN = config['discord_token']
CHANNEL_IDS = config.get('channel_ids', [])
GOOGLE_MAPS_API_KEY = config['google_maps_api_key']
WEBSOCKET_URL = "wss://ws.tzevaadom.co.il:8443/socket?platform=WEB"  # WebSocket URL
TEST_MODE = config.get('test_mode', False)  # Ensure default value if not set
DATA_FILES = config['data_files']
# Telegram API credentials (replace with your own credentials)
api_id = config['telegram_api_id']
api_hash = config['telegram_api_hash']
api_phone = config.get('telegram_phone')  # Optional: Required for user client

# Telegram channel to monitor
TELEGRAM_CHANNEL_ID = config['telegram_channel']
print(f"TELEGRAM_CHANNEL: {TELEGRAM_CHANNEL_ID}")
# Alert categories mapped by threat level
alert_categories = {
    0: (discord.Colour.red(), "Rockets 🚀"),
    5: (discord.Colour.orange(), "Hostile aircraft intrusion 🛩️"),
    3: (discord.Colour.purple(), "Earthquake 🌍"),
    1: (discord.Colour.yellow(), "Hazardous Materials Incident ☣️"),
    4: (discord.Colour.blue(), "Tsunami 🌊"),
    2: (discord.Colour.dark_red(), "Terrorist Infiltration ⚠️"),
    6: (discord.Colour.green(), "Radiological Incident ☢️"),
    7: (discord.Colour.magenta(), "Non-conventional missile 🚀"),
    8: (discord.Colour.pink(), "Non-conventional threat ⚠️"),
}

# Initialize Discord intents and bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Global variables to track alerts and messages
posted_alert_ids = set()
last_messages = {}  # Dictionary to track last message info per channel
recent_alerts = []


async def start_telethon_client():
    """
    Initializes and starts the Telethon client to listen to the specified Telegram channel.
    """
    # Initialize the Telegram client
    client = TelegramClient('discord_bot_telethon', api_id, api_hash)

    await client.start(phone=api_phone)  # Only needed for user client

    # If using a user client and two-factor authentication is enabled
    if await client.is_user_authorized() is False:
        try:
            await client.send_code_request(api_phone)
            code = input('Enter the Telegram code: ')
            await client.sign_in(api_phone, code)
        except SessionPasswordNeededError:
            password = input('Two-step verification enabled. Please enter your password: ')
            await client.sign_in(password=password)

    # Define the target Telegram channel
    target_channel = -1001441886157  # Use the channel's username without 't.me/'

    @client.on(events.NewMessage(chats=[target_channel]))
    async def handler(event):
        """
        Event handler for new messages in the target Telegram channel.
        """
        message_text = event.message.message
        logging.info(f"New message from Telegram: {message_text}")

        # Check if the message contains the target phrase
        if "בהמשך לדיווח על" in message_text:
            logging.info("Target phrase found in the message. Forwarding to Discord...")
            await send_conclusion_message()

    logging.info("Telethon client is listening to Telegram channel...")
    await client.run_until_disconnected()


async def send_conclusion_message():
    """Sends a conclusion message with an image to a Discord channel."""
    image_url = "https://i.imgur.com/xU6KpnC.jpeg"
    embed = discord.Embed(
        title="Following the report of an alert about a hostile aircraft entering Israeli airspace - the incident has ended",
        color=discord.Color.blue()
    )
    embed.set_image(url=image_url)  # Attach the image URL
    for channel_id in CHANNEL_IDS:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(embed=embed)
                logging.info(f"Sent conclusion message to channel: {channel.name}")
            except Exception as e:
                logging.error(f"Failed to send conclusion message to channel: {channel.name}. Error: {e}")
        else:
            logging.error(f"Channel ID {channel_id} not found. Skipping conclusion message.")


def simplify_polygon(coordinates, tolerance=0.001):
    """Simplifies a polygon using the Douglas-Peucker algorithm."""
    polygon = Polygon(coordinates)
    simplified = polygon.simplify(tolerance, preserve_topology=True)
    return list(simplified.exterior.coords)


def get_city_english_name(city_id):
    """Retrieve the English name of a city given its ID."""
    with open(DATA_FILES["english_cities"], encoding="utf-8") as file:
        english_cities_json = json.load(file)
    for city in english_cities_json:
        if city["id"] == city_id:
            return city["label"]


class RedAlert:
    def __init__(self, session: aiohttp.ClientSession, test_mode=False):
        self.session = session  # Shared aiohttp session
        self.locations = self.get_locations_list(DATA_FILES['targets'])
        self.area_to_polygon = self.load_area_to_polygon(DATA_FILES['area_to_polygon'])
        self.area_to_coordinates = self.load_area_to_coordinates(DATA_FILES['area_to_coordinates'])
        self.test_mode = test_mode
        self.alert_history = self.load_alert_history()
        with open('locality_residents.json', 'r') as json_file:
            self.locality_data = json.load(json_file)
        self.headers = {
            "Host": "ws.tzevaadom.co.il:8443",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "charset": "utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua-mobile": "?0",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.89 Safari/537.36",
            "sec-ch-ua-platform": "macOS",
            "Accept": "*/*",
            "sec-ch-ua": '".Not/A)Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Origin": "https://www.tzevaadom.co.il",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,he-IL;q=0.8,he;q=0.7",
        }
        self.cookies = {}
        # if not self.test_mode:
        #     asyncio.create_task(self.get_cookies())  # Fetch cookies asynchronously only in production

    async def get_cookies(self):
        """Retrieve cookies from the server using aiohttp."""
        HOST = "https://www.oref.org.il/"
        try:
            async with self.session.get(HOST, headers=self.headers) as response:
                if response.status == 200:
                    self.cookies = response.cookies.get_dict()
                    logging.info("Successfully retrieved cookies.")
                else:
                    logging.error(f"Failed to retrieve cookies. Status code: {response.status}")
        except Exception as e:
            logging.error(f"Exception occurred while fetching cookies: {e}")

    def find_closest_match(self, query):
        """Find the closest match for a locality and return its population."""
        localities = list(self.locality_data.keys())
        query = query.split("|")[0].strip()
        closest_match, score = process.extractOne(query, localities)
        if score >= 70:  # Threshold for similarity
            return closest_match, self.locality_data[closest_match]
        else:
            logging.warning(f"No close match found for {query}. Similarity Score: {score}")
            return None, 0

    def calculate_total_population(self, alert_cities):
        """Calculate the total affected population, counting each city once."""
        total_population = 0
        counted_cities = set()

        for city_en, city_he in alert_cities:
            # Assuming city_en is the English name; adjust if necessary
            if city_en.lower() not in counted_cities:
                match, population = self.find_closest_match(city_en)
                if match:
                    total_population += population
                    counted_cities.add(city_en.lower())

        return total_population

    def get_coordinates(self, location_names):
        """Get city coordinates by given city names from local JSON."""
        coordinates = {}

        location_name = location_names.strip()
        if location_name in self.area_to_coordinates:
            coord = self.area_to_coordinates[location_name]
            coordinates[location_name] = {
                'lat': coord['lat'],
                'lng': coord['long']  # Rename 'long' to 'lng' to match Google Maps API format
            }

        return coordinates

    def random_coordinates(self, latitude, longitude):
        """Generate random coordinates within a city for visualization."""
        circle_r = 1
        alpha = 2 * math.pi * random.random()
        r = circle_r * random.random()
        x = r * math.cos(alpha) + latitude
        y = r * math.sin(alpha) + longitude
        return {"latitude": x, "longitude": y}

    def count_alerts(self, alerts_data):
        """Count the number of alerts currently active."""
        return len(alerts_data)

    def load_area_to_polygon(self, file_path):
        """Load area to polygon mappings from a JSON file."""
        with open(file_path, encoding="utf-8") as file:
            return json.load(file)

    def load_area_to_coordinates(self, file_path):
        """Load area to coordinates mappings from a JSON file."""
        with open(file_path, encoding="utf-8") as file:
            return json.load(file)

    def get_locations_list(self, file_path):
        """Build a list of locations and their response times for alerts."""
        with open(file_path, encoding="utf-8") as file:
            return json.load(file)

    def encode_polygon_path(self, coordinates):
        """Encode a list of latitude and longitude tuples into a path string for Google Static Maps."""
        return "|".join(f"{lat},{lng}" for lat, lng in coordinates)

    # Haversine formula to calculate distance between two points
    def haversine_distance(self, coord1, coord2):
        R = 6371000  # Radius of the Earth in meters
        lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
        lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    # Function to determine the appropriate zoom level based on maximum distance
    def calculate_zoom_level(self, max_distance):
        logging.debug(f"Max distance: {max_distance}")
        if max_distance < 5000:
            return 12
        elif max_distance < 15000:  # City
            return 11
        else:  # World
            return -1

    def get_map_url(self, coordinates, hebrew_region, threat):
        """Generate a static map URL with markers and polygon paths."""
        base_url = "https://maps.googleapis.com/maps/api/staticmap"
        markers = []
        paths = []
        all_coords = []

        for region, cities in coordinates.items():
            for city, coord in cities.items():
                lat = f"{coord['lat']:.6f}"
                lng = f"{coord['lng']:.6f}"
                if threat == 5:
                    markers.append(f"icon:https://i.imgur.com/5VH1kVg.png|{lat},{lng}")
                elif threat == 0:
                    markers.append(f"icon:https://i.imgur.com/S3qDKKI.png|{lat},{lng}")
                elif threat == 2:
                    markers.append(f"icon:https://i.imgur.com/NVPjahE.png|{lat},{lng}")
                elif threat == 3:
                    markers.append(f"icon:https://i.imgur.com/QAvgOIo.png|{lat},{lng}")
                else:
                    markers.append(f"color:red|{lat},{lng}")
                all_coords.append((coord['lat'], coord['lng']))

        for region in hebrew_region:
            if region in self.area_to_polygon:
                coordinates = self.area_to_polygon[region]
                simplified_coordinates = simplify_polygon(coordinates)
                path = self.encode_polygon_path(simplified_coordinates)
                if threat == 5:
                    paths.append(f"fillcolor:0xffa5001a|color:0xffa500ff|weight:2|{path}")
                else:
                    paths.append(f"fillcolor:0xff00001a|color:0xff0000ff|weight:2|{path}")

        markers_param = "&markers=" + "&markers=".join(markers) if markers else ""
        paths_param = "&path=" + "&path=".join(paths) if paths else ""

        params = {
            "size": "800x400",
            "maptype": "roadmap",
            "key": GOOGLE_MAPS_API_KEY,
        }

        if len(hebrew_region) == 1:
            params["zoom"] = 12

        max_distance = 0
        if len(all_coords) > 1:
            for i in range(len(all_coords)):
                for j in range(i + 1, len(all_coords)):
                    distance = self.haversine_distance(all_coords[i], all_coords[j])
                    max_distance = max(max_distance, distance)

        zoom_level = self.calculate_zoom_level(max_distance)
        if zoom_level != -1 and "zoom" not in params:
            params["zoom"] = zoom_level

        url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}{markers_param}{paths_param}"
        if len(url) > 8192:
            url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}{markers_param}"
        return url

    def load_alert_history(self):
        try:
            with open('alert_history.json', 'r') as file:
                return json.load(file)
        except Exception as e:
            logging.error(f"Failed to load alert history: {e}")
            return []

    def save_alert_history(self):
        try:
            with open('alert_history.json', 'w') as file:
                json.dump(self.alert_history, file)
        except Exception as e:
            logging.error(f"Failed to save alert history: {e}")

    def add_to_alert_history(self, alert):
        self.alert_history.append(alert)
        self.save_alert_history()

    def get_alert_stats(self, period):
        now = time.time()
        delta = parse_period(period)  # Use the helper function for parsing
        start_time = now - delta.total_seconds()
        alerts = [alert for alert in self.alert_history if alert[4] >= start_time]

        city_alerts = {}
        for city, city_he, migun_time, coords, alert_time in alerts:
            alert_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert_time))
            if city in city_alerts:
                city_alerts[city].append(alert_time_str)
            else:
                city_alerts[city] = [alert_time_str]

        return city_alerts

    def get_alerts_within_period(self, period: str):
        """
        Retrieve alerts within the specified period.

        Args:
            period (str): Time period string (e.g., '1h', '2d', '3w').

        Returns:
            List[dict]: List of alerts with their coordinates and timestamps.
        """
        delta = parse_period(period)
        start_time = time.time() - delta.total_seconds()
        filtered_alerts = [
            {
                "english_city": alert[0],
                "city_he": alert[1],
                "migun_time": alert[2],
                "coordinates": alert[3],
                "timestamp": alert[4]
            }
            for alert in self.alert_history
            if alert[4] >= start_time
        ]
        return filtered_alerts


def html_to_discord(html):
    """Convert HTML to Discord markdown."""
    html = html.replace("<br>", "\n")
    html = html.replace("<b>", "**").replace("</b>", "**")
    html = html.replace("<i>", "*").replace("</i>", "*")
    return re.sub(r"<.*?>", "", html)


# Initialize a dictionary to hold locks for each channel
channel_locks = {}


async def send_embed(alert, channel, description, recent_alerts, alert_color, map_url):
    """Send or update an embed message in a Discord channel."""

    # Ensure a lock exists for the channel
    if channel.id not in channel_locks:
        channel_locks[channel.id] = asyncio.Lock()

    # Acquire the lock for the channel
    async with channel_locks[channel.id]:
        embed = discord.Embed(title=FRONT_COMMAND_ALERT_TITLE, color=alert_color)
        embed.description = description

        # Generate the map image
        map_image = await fetch_map_image(map_url)

        if map_image is None:
            await channel.send("Failed to download the map image.")
            return

        # Check if a message was sent in the last 30 seconds
        current_time = time.time()
        last_message_info = last_messages.get(channel.id)
        if last_message_info:
            elapsed_time = current_time - last_message_info['timestamp']
            last_alert_category = last_message_info['alert_category']
            logging.info(f"Elapsed time since last message in {channel.name}: {elapsed_time:.2f} seconds")
            if elapsed_time < 30 and last_alert_category == alert_color:
                # Attempt to edit the existing message
                try:
                    message = await channel.fetch_message(last_message_info['message_id'])
                    embed_copy = embed.copy()
                    embed_copy.set_image(url="attachment://map.png")
                    await message.edit(embed=embed_copy, attachments=[discord.File(map_image, filename="map.png")])
                    logging.info(f"Updated existing message in {channel.name}")
                    return
                except discord.NotFound:
                    logging.warning(f"Last message not found in channel {channel.name}. Sending a new message.")
                except discord.HTTPException as e:
                    logging.error(f"Failed to edit message in {channel.name}: {e}")

        # If no recent message exists, or elapsed time is over 30 seconds, send a new message
        try:
            embed.set_image(url="attachment://map.png")
            message = await channel.send(embed=embed, file=discord.File(map_image, filename="map.png"))
            # Update the last_messages dictionary with message ID, timestamp, and alert category
            last_messages[channel.id] = {
                'message_id': message.id,
                'timestamp': current_time,
                'alert_category': alert_color  # Store the alert category
            }
            logging.info(f"Sent new message in {channel.name}")
        except discord.HTTPException as e:
            logging.error(f"Failed to send message in {channel.name}: {e}")


async def fetch_map_image(map_url):
    """Fetch the map image from the provided URL."""
    async with bot.session.get(map_url) as response:
        try:
            if response.status == 200:
                image_data = await response.read()
                return BytesIO(image_data)
            else:
                logging.error(f"Failed to fetch map image. HTTP status: {response.status}")
                return None
        except Exception as e:
            logging.error(f"Exception occurred while fetching map image: {e}")
            return None


@bot.command(name='registerAlertsBot')
@commands.has_any_role("Manager", "Moderator", "Community Contributor")
async def register_alerts_bot(ctx):
    """Register the current channel to receive alerts."""
    global CHANNEL_IDS
    if ctx.channel.id not in CHANNEL_IDS:
        CHANNEL_IDS.append(ctx.channel.id)
        config['channel_ids'] = CHANNEL_IDS
        try:
            with open('config.json', 'w') as config_file:
                json.dump(config, config_file, indent=4)
            await ctx.send(f"Alerts bot registered to this channel: {ctx.channel.name}")
            logging.info(f"Registered channel {ctx.channel.name} for alerts.")
        except Exception as e:
            logging.error(f"Failed to register channel {ctx.channel.name}: {e}")
            await ctx.send(f"Failed to register this channel due to an error.")
    else:
        # Channel is already registered, remove it instead
        CHANNEL_IDS.remove(ctx.channel.id)
        config['channel_ids'] = CHANNEL_IDS
        try:
            with open('config.json', 'w') as config_file:
                json.dump(config, config_file, indent=4)
            await ctx.send(f"Alerts bot unregistered from this channel: {ctx.channel.name}")
            logging.info(f"Unregistered channel {ctx.channel.name} from alerts.")
        except Exception as e:
            logging.error(f"Failed to unregister channel {ctx.channel.name}: {e}")
            await ctx.send(f"Failed to unregister this channel due to an error.")


@bot.command(name='alerts_stats', aliases=['stats', 'alerts'])
async def alerts_stats(ctx, period: str = "1h"):
    """Display alert statistics for a given period."""
    try:
        alert = RedAlert(session=bot.session, test_mode=TEST_MODE)
        stats = alert.get_alert_stats(period)
        if stats:
            # Generate a bar chart
            await generate_bar_chart(ctx, stats, period)
        else:
            description = f"**Alert stats for the past {period}:**\n\nNo alerts in the given period."
            await ctx.send(description)

    except ValueError as e:
        await ctx.send(f"Error: {str(e)}. Please use a valid time period format like '1h', '2d', '3w'.")


@bot.command(name='reds', aliases=['heatmap'])
async def alerts_heatmap(ctx, period: str = "1h"):
    """
    Display a heatmap of alert locations for a given period.

    Usage:
        /alerts_heatmap [period]

    Examples:
        /alerts_heatmap
        /alerts_heatmap 3h
        /alerts_heatmap 2d
    """
    try:
        alert = RedAlert(session=bot.session, test_mode=TEST_MODE)
        alerts = alert.get_alerts_within_period(period)
        if alerts:
            await generate_heatmap(ctx, alerts, period)
        else:
            await ctx.send(f"No alerts found for the past {period}.")
    except ValueError as e:
        await ctx.send(f"Error: {str(e)}. Please use a valid time period format like '1h', '2d', '3w'.")


@bot.command(name='population')
async def city_population(ctx, *, city_name: str):
    """Fetch the population of a specified city."""
    alert = RedAlert(session=bot.session, test_mode=TEST_MODE)
    city, population = alert.find_closest_match(city_name)
    if city:
        await ctx.send(f"The population of {city} is {population:,} people (as of 2022)")
    else:
        await ctx.send(f"Could not find population data for {city_name}.")


@bot.command(name='restart')
@commands.is_owner()
async def restart(ctx):
    """Restart the bot."""
    await ctx.send("Restarting the bot...")
    os.execv(sys.executable, ['python'] + sys.argv)


@bot.command(name='trigger_test_alert')
@commands.is_owner()
async def trigger_test_alert(ctx):
    """Manually trigger a test alert (only in test mode)."""
    if not TEST_MODE:
        await ctx.send("Test mode is not enabled. This command is unavailable.")
        return

    # Instantiate the RedAlert object first
    alert = RedAlert(session=bot.session, test_mode=TEST_MODE)

    # Create mock alert data after 'alert' is defined
    mock_alert = {
        "notificationId": f"test_{int(time.time())}",
        "threat": random.choice(list(alert_categories.keys())),
        "isDrill": False,
        "cities": random.sample(list(alert.area_to_coordinates.keys()), k=random.randint(1, 3)),
        "time": int(time.time())
    }

    channels = [bot.get_channel(channel_id) for channel_id in CHANNEL_IDS]
    await handle_alert(mock_alert, alert, channels)
    await ctx.send("Test alert triggered.")


async def generate_bar_chart(ctx, stats, period):
    """Generate and send a bar chart of alert statistics."""
    cities = list(stats.keys())
    alert_counts = [len(times) for times in stats.values()]

    plt.figure(figsize=(10, 6), facecolor='#181818')
    bars = plt.barh(cities, alert_counts, color='#CB0000')  # Dark red bars

    # Add the number of alerts on the bars
    for bar, count in zip(bars, alert_counts):
        plt.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                 f'{count}', va='center', ha='left', color='red')  # Bright red text

    plt.xlabel('Number of Alerts', color='white')  # White axis label
    plt.ylabel('Cities', color='white')  # White axis label
    plt.title(f'Alert Stats for the Past {period}', color='white')  # White title
    plt.gca().tick_params(axis='both', colors='white')  # White tick labels
    plt.gca().spines['bottom'].set_color('white')  # White axis line
    plt.gca().spines['left'].set_color('white')  # White axis line
    plt.gca().spines['top'].set_color('white')  # White axis line
    plt.gca().spines['right'].set_color('white')  # White axis line
    plt.gca().set_facecolor('#181818')  # Set background color to black
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#181818')  # Save with black background
    buf.seek(0)
    file = discord.File(buf, filename='alert_stats.png')
    await ctx.send(file=file)
    buf.close()


# Helper function to handle time periods
def parse_period(period_str):
    number = int(period_str[:-1])
    unit = period_str[-1]
    if unit == 'h':
        return timedelta(hours=number)
    elif unit == 'd':
        return timedelta(days=number)
    elif unit == 'w':
        return timedelta(weeks=number)
    else:
        raise ValueError("Invalid time period format. Use 'h' for hours, 'd' for days, or 'w' for weeks.")


@bot.event
async def on_ready():
    """Event handler when the bot is ready."""
    print(f"Logged in as {bot.user}")
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash commands.")
    alert = RedAlert(session=bot.session, test_mode=TEST_MODE)
    channels = [bot.get_channel(channel_id) for channel_id in CHANNEL_IDS]
    if TEST_MODE:
        bot.loop.create_task(simulate_alerts(alert, channels))
        print("Bot is running in TEST MODE. Simulating alerts.")
    else:
        bot.loop.create_task(listen_to_websocket(alert, channels))
        print("Bot is ready and listening for commands and alerts")
    # Start the Telethon client as a background task
    bot.loop.create_task(start_telethon_client())


async def listen_to_websocket(alert: RedAlert, channels):
    """Listen to the WebSocket for incoming alerts and handle reconnection."""
    while True:
        try:
            async with alert.session.ws_connect(
                    WEBSOCKET_URL,
                    headers={
                        "Origin": "https://www.tzevaadom.co.il",
                        "Host": "ws.tzevaadom.co.il:8443/socket?platform=WEB",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.89 Safari/537.36"
                    }
            ) as ws:
                logging.info("Connected to WebSocket.")

                # Ping the server every 30 seconds to check if connection is still alive
                while True:
                    try:
                        msg = await ws.receive(timeout=120)  # Timeout to detect dead connections

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                alert_data = json.loads(msg.data)
                                await handle_alert(alert_data, alert, channels)
                            except json.JSONDecodeError as e:
                                logging.error(f"Failed to decode JSON message: {e}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logging.error(f"WebSocket closed or error: {msg.type}")
                            break  # Exit to reconnect
                    except asyncio.TimeoutError:
                        # If no message is received in 120 seconds, send a ping to check the connection
                        await ws.ping()
                        logging.info("Sent a ping to the WebSocket server.")

        except aiohttp.ClientConnectorError as e:
            logging.error(f"WebSocket connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)  # Delay before retrying
        except Exception as e:
            logging.error(f"Unexpected error with WebSocket: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)


async def simulate_alerts(alert: RedAlert, channels):
    """Simulate incoming alerts for testing purposes."""
    while True:
        await asyncio.sleep(random.uniform(0, 1))
        mock_alert = generate_mock_alert(alert)
        await handle_alert(mock_alert, alert, channels)
        logging.info("Simulated a test alert.")


def generate_mock_alert(alert: RedAlert):
    """Generate a mock alert data structure."""
    threat_level = random.choice(list(alert_categories.keys()))
    is_drill = random.choice([False, True])
    cities = random.sample(list(alert.area_to_coordinates.keys()), k=random.randint(1, 3))
    timestamp = int(time.time())

    return {
        "data": {
            "notificationId": f"test_{timestamp}_{random.randint(1000, 9999)}",
            "threat": threat_level,
            "isDrill": 0,
            "cities": cities,
            "time": timestamp
        }
    }


async def handle_alert(alert_data, alert: RedAlert, channels):
    """
    Process the alert data received from the WebSocket and send it to Discord channels.
    """
    global recent_alerts
    print(f"Received alert data: {alert_data}")
    print(f"recent_alerts: {recent_alerts}")
    print(f"current time: {time.time()}")
    recent_alerts = [a for a in recent_alerts if time.time() - a[4] < 60]
    alert_data = alert_data.get("data", {})
    notification_id = alert_data.get("notificationId")
    last_alert_category = last_messages.get(channels[0].id, {}).get('alert_category', discord.Colour.default())
    current_alert_category = alert_categories.get(alert_data.get("threat", 0), (discord.Colour.default(), "Unknown "
                                                                                                          "Threat"))[0]
    if last_alert_category != current_alert_category:
        recent_alerts = []

    if not notification_id:
        logging.warning("Received alert without notificationId. Skipping.")
        return

    if notification_id in posted_alert_ids:
        logging.info("Duplicate alert ID received. Skipping.")
        return
    else:
        posted_alert_ids.add(notification_id)

    threat = alert_data.get("threat", 0)
    is_drill = alert_data.get("isDrill", False)
    cities = alert_data.get("cities", [])
    timestamp = int(time.time())

    if is_drill:
        alert_category = "Drill 🛡️"
        alert_color = discord.Colour.blue()
    else:
        category_info = alert_categories.get(threat, ("Unknown Threat", discord.Colour.default()))
        alert_category = category_info[1]
        alert_color = category_info[0]

    affected_cities = []  # List to store (city_he, city_en)
    new_alerts = []

    for city_he in cities:
        for obj in alert.locations:
            if obj["label_he"] == city_he:
                migun_time = obj["migun_time"]
                english_city = html_to_discord(obj["mixname"])
                coordinates = alert.get_coordinates(city_he)
                if not coordinates:
                    logging.warning(f"No coordinates available for {city_he}. Skipping this city.")
                    continue  # Skip this city if coordinates are missing
                # check if not already in recent alerts
                if (english_city, city_he) in [(city_en, city_he) for city_en, city_he, _, _, _ in recent_alerts]:
                    logging.info(f"City {english_city} already in recent alerts. Skipping.")
                    continue
                affected_cities.append((english_city, city_he))
                new_alerts.append((english_city, city_he, migun_time, coordinates, timestamp))
                alert.add_to_alert_history((english_city, city_he, migun_time, coordinates, timestamp))

    if not new_alerts:
        logging.info("No valid cities found in the alert. Skipping update.")
        recent_alerts = [a for a in recent_alerts if time.time() - a[4] < 60]  # Clean up recent alerts
        return

    # Clean up recent_alerts to only include alerts within the last 60 seconds
    recent_alerts = [a for a in recent_alerts if time.time() - a[4] < 60]
    logging.debug(f"Existing recent alert cities: { {city for _, city in affected_cities} }")

    # Add new alerts to recent_alerts with the current timestamp
    for alert_entry in new_alerts:
        recent_alerts.append(alert_entry)  # Ensure 5 elements

    # Aggregate all affected cities from recent_alerts
    all_affected_cities = [(city_en, city_he) for city_en, city_he, _, _, _ in recent_alerts]
    print(all_affected_cities)
    # Calculate total affected population from all recent alerts
    total_population = alert.calculate_total_population(all_affected_cities)

    # Prepare embed description
    all_alerts_list = [f"{city_he} ({migun_time}s)" for city_he, _, migun_time, _, _ in recent_alerts]
    all_alerts = "\n• ".join(all_alerts_list)
    all_alerts = f"• {all_alerts}"  # Add the first bullet point manually
    # Define a safe maximum length for the alerts section
    MAX_ALERTS_DESCRIPTION_LENGTH = 3500  # Reserve space for other parts of the description
    # Truncate the alerts list if necessary
    if len(all_alerts) > MAX_ALERTS_DESCRIPTION_LENGTH:
        truncated_alerts = all_alerts[:MAX_ALERTS_DESCRIPTION_LENGTH].rsplit('\n• ', 1)[0]
        num_truncated = len(all_alerts_list) - len(truncated_alerts.split('\n• '))
        all_alerts = truncated_alerts + f"\n• ...and {num_truncated} more alerts."
    description = (
        f"**Locations**:\n```{all_alerts}```\n"
        f"**Type:**\n```\n{alert_category}```\n"
        f"**Total Affected Population:** {total_population:,} 👥\n\n"
        f"**Time:** <t:{timestamp}:f>\n"
        f"-# Time is in your local timezone\n"
        f"-# Israel Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}"
    )

    # Final check to ensure the description is within the limit
    if len(description) > 4096:
        # Further truncate if necessary
        allowable_length = 4096 - (len(description) - len(all_alerts))
        all_alerts = all_alerts[:allowable_length]
        all_alerts += f"\n• ...and more alerts not displayed to fit within the limit."
        description = (
            f"**Locations**:\n```{all_alerts}```\n"
            f"**Type:**\n```\n{alert_category}```\n"
            f"**Total Affected Population:** {total_population:,} 👥\n\n"
            f"**Time:** <t:{timestamp}:f>\n"
            f"-# Time is in your local timezone\n"
            f"-# Israel Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}"
        )
    # Generate map URL for recent_alerts
    map_url = alert.get_map_url(
        {city: alert.get_coordinates(city) for _, city, _, _, _ in recent_alerts},
        set(city_he for _, city_he, _, _, _ in recent_alerts), threat
    )

    # Send embed to all registered channels
    for channel in channels:
        if channel is None:
            logging.warning("One of the channels in CHANNEL_IDS is None. Skipping.")
            continue
        await send_embed(alert, channel, description, recent_alerts, alert_color, map_url)


async def generate_heatmap(ctx, alerts, period):
    """
    Generate and send a heatmap of alert locations.

    Args:
        ctx: Discord context.
        alerts (List[dict]): List of alerts with coordinates.
        period (str): Time period string (e.g., '1h', '2d', '3w').
    """
    if not alerts:
        await ctx.send(f"No alert locations found for the past {period}.")
        return

    # Initialize lists to hold latitude and longitude values
    lats = []
    lngs = []

    for alert in alerts:
        coordinates = alert.get('coordinates', {})
        city_he = alert.get('city_he')

        if not city_he:
            logging.warning(f"Alert missing 'city_he': {alert}")
            continue

        city_coords = coordinates.get(city_he)

        if not city_coords:
            logging.warning(f"No coordinates found for city '{city_he}' in alert: {alert}")
            continue

        lat = city_coords.get('lat')
        lng = city_coords.get('lng')

        if lat is None or lng is None:
            logging.warning(f"Missing 'lat' or 'lng' for city '{city_he}' in alert: {alert}")
            continue

        lats.append(lat)
        lngs.append(lng)

    if not lats or not lngs:
        await ctx.send(f"No valid alert locations found for the past {period}.")
        return

    # Create a DataFrame
    df = pd.DataFrame({
        'Latitude': lats,
        'Longitude': lngs
    })

    # Increase the figure size and DPI for higher resolution
    plt.figure(figsize=(12, 10), dpi=300)

    # Plot the heatmap with increased resolution and semi-transparency
    sns.kdeplot(
        x=df['Longitude'],
        y=df['Latitude'],
        cmap="Reds",
        fill=True,
        alpha=0.5,  # Adjust this value for desired transparency
        n_levels=20,  # Increase the number of contour levels for smoother gradients
        bw_adjust=0.2,

    )

    # Optionally, add a map background
    try:
        # Define the extent of the map
        minx, maxx = df['Longitude'].min() - 0.1, df['Longitude'].max() + 0.1
        miny, maxy = df['Latitude'].min() - 0.1, df['Latitude'].max() + 0.1
        plt.xlim(minx, maxx)
        plt.ylim(miny, maxy)

        # Add basemap with higher zoom level
        cx.add_basemap(
            plt.gca(),
            crs="EPSG:4326",
            source=cx.providers.CartoDB.Voyager,
        )
    except Exception as e:
        logging.warning(f"Failed to add basemap: {e}")

    plt.title(f'Alert Heatmap for the Past {period}', fontsize=16)
    plt.xlabel('Longitude', fontsize=12, color='white')
    plt.ylabel('Latitude', fontsize=12, color='white')

    plt.tight_layout()

    # Save the heatmap to a BytesIO object with higher quality
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=300)
    buf.seek(0)
    file = discord.File(buf, filename='heatmap.png')
    await ctx.send(file=file)
    plt.close()
    buf.close()


async def main():
    """Main entry point for the bot."""
    async with aiohttp.ClientSession() as session:
        bot.session = session  # Attach the session to the bot instance
        try:
            await bot.start(TOKEN)
        finally:
            await session.close()


@bot.event
async def on_shutdown():
    """Handle bot shutdown and clean up resources."""
    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot is shutting down.")
