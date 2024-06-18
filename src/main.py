
import logging
import random
import math
import re
from datetime import timedelta
from io import BytesIO

import aiohttp
from shapely.geometry import Polygon
import requests
import json
import time
import discord
import asyncio
from discord.ext import commands

FRONT_COMMAND_ALERT_TITLE = "Israel Home Front Command Alert 🚨"

# Load configuration
with open('config.json') as config_file:
    config = json.load(config_file)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Example usage of config
TOKEN = config['discord_token']
CHANNEL_ID = config.get('channel_id', None)
GOOGLE_MAPS_API_KEY = config['google_maps_api_key']
ALERT_SOURCE_URL = config['alert_source_url']
TEST_MODE = config['test_mode']
DATA_FILES = config['data_files']


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
    def __init__(self, test_mode=False):
        self.locations = self.get_locations_list(DATA_FILES['targets'])
        self.area_to_polygon = self.load_area_to_polygon(DATA_FILES['area_to_polygon'])
        self.cookies = None
        self.headers = {
            "Host": "www.oref.org.il",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "charset": "utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua-mobile": "?0",
            "User-Agent": "",
            "sec-ch-ua-platform": "macOS",
            "Accept": "*/*",
            "sec-ch-ua": '".Not/A)Brand";v="99", "Google Chrome";v="103", "Chromium";v="103"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.oref.org.il/12481-he/Pakar.aspx",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
        self.get_cookies()
        self.test_mode = test_mode
        self.alert_history = self.load_alert_history()

    def get_cookies(self):
        """Retrieve cookies from the server."""
        HOST = "https://www.oref.org.il/"
        response = requests.get(HOST, headers=self.headers)
        self.cookies = response.cookies

    def get_coordinates(self, location_names):
        """Get city coordinates by given city names."""
        coordinates = {}
        for location_name in location_names.split(","):
            try:
                params = {"address": location_name.strip(), "key": GOOGLE_MAPS_API_KEY}
                response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params)
                response.raise_for_status()
                data = response.json()
                if data["status"] == "OK":
                    coordinates[location_name.strip()] = data["results"][0]["geometry"]["location"]
                else:
                    logging.warning(f"Error fetching coordinates for {location_name.strip()}: {data['status']}")
            except requests.RequestException as e:
                logging.error(f"Error fetching coordinates: {e}")
            except json.JSONDecodeError as e:
                logging.error(f"JSON decode error for {location_name.strip()}: {e}")
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

    def get_locations_list(self, file_path):
        """Build a list of locations and their response times for alerts."""
        with open(file_path, encoding="utf-8") as file:
            return json.load(file)

    def get_red_alerts(self):
        """Retrieve the current red alerts."""
        if self.test_mode:
            with open("../resources/example.json", encoding="utf-8") as file:
                data = json.load(file)
            if not data["data"]:
                return None
            data["timestamp"] = time.time()
            return data
        else:
            response = requests.get(ALERT_SOURCE_URL, headers=self.headers, cookies=self.cookies)
            alerts = response.content.decode("UTF-8").replace("\n", "").replace("\r", "")
            if len(alerts) <= 1:
                return None
            data = json.loads(response.content)
            if not data["data"]:
                return None
            data["timestamp"] = time.time()
            return data

    def encode_polygon_path(self, coordinates):
        """Encode a list of latitude and longitude tuples into a path string for Google Static Maps."""
        return "|".join(f"{lat},{lng}" for lat, lng in coordinates)

    def get_map_url(self, coordinates, hebrew_region):
        """Generate a static map URL with markers and polygon paths."""
        base_url = "https://maps.googleapis.com/maps/api/staticmap"
        markers = []
        paths = []
        cities_list = []

        for region, cities in coordinates.items():
            cities_list = cities
            for city, coord in cities.items():
                lat = f"{coord['lat']:.6f}"
                lng = f"{coord['lng']:.6f}"
                markers.append(f"color:red|{lat},{lng}")

        for region in hebrew_region:
            if region in self.area_to_polygon:
                coordinates = self.area_to_polygon[region]
                simplified_coordinates = simplify_polygon(coordinates)
                path = self.encode_polygon_path(simplified_coordinates)
                paths.append(f"fillcolor:0xff00001a|color:0xff0000ff|weight:2|{path}")

        markers_param = "&markers=" + "&markers=".join(markers) if markers else ""
        paths_param = "&path=" + "&path=".join(paths) if paths else ""

        params = {
            "size": "800x400",
            "maptype": "roadmap",
            "key": GOOGLE_MAPS_API_KEY,
        }

        if len(cities_list) == 1:
            params["zoom"] = 12

        url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}{markers_param}{paths_param}"
        if len(url) > 8192:
            url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}{markers_param}"
        return url

    def load_alert_history(self):
        try:
            with open('alert_history.json', 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            return []

    def save_alert_history(self):
        with open('alert_history.json', 'w') as file:
            json.dump(self.alert_history, file)

    def add_to_alert_history(self, alert):
        self.alert_history.append(alert)
        self.save_alert_history()

    def get_alert_stats(self, period):
        now = time.time()
        delta = timedelta(hours=1)  # default to 1 hour
        if period.endswith('d'):
            delta = timedelta(days=int(period[:-1]))
        elif period.endswith('h'):
            delta = timedelta(hours=int(period[:-1]))
        elif period.endswith('w'):
            delta = timedelta(weeks=int(period[:-1]))

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


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

posted_alert_ids = set()
last_message_id = None
last_alert_time = None
recent_alerts = []


def html_to_discord(html):
    """Convert HTML to Discord markdown."""
    html = html.replace("<br>", "\n")
    html = html.replace("<b>", "**").replace("</b>", "**")
    html = html.replace("<i>", "*").replace("</i>", "*")
    return re.sub(r"<.*?>", "", html)


async def fetch_and_send_alerts(test_mode=TEST_MODE):
    await bot.wait_until_ready()
    alert = RedAlert(test_mode=test_mode)

    global last_message_id
    global last_alert_time
    global recent_alerts

    alert_categories = {
        "ירי רקטות וטילים": (discord.Colour.red(), "Missiles 🚀"),
        "חדירת כלי טיס עוין": (discord.Colour.orange(), "Hostile aircraft intrusion 🛩️"),
        "רעידת אדמה": (discord.Colour.purple(), "Earthquake 🌍"),
        "אירוע חומרים מסוכנים": (discord.Colour.yellow(), "Hazardous Materials Incident ☣️"),
        "צונאמי": (discord.Colour.blue(), "Tsunami 🌊"),
        "חדירת מחבלים": (discord.Colour.red(), "Terrorist Infiltration ⚠️"),
        "אירוע רדיולוגי": (discord.Colour.green(), "Radiological Incident ☢️"),
    }

    while not bot.is_closed():
        red_alerts = alert.get_red_alerts()
        channel = bot.get_channel(CHANNEL_ID)
        if red_alerts:
            new_alerts = []
            alert_category = alert_categories.get(red_alerts["title"], (None, "Unknown"))[1]
            alert_color = alert_categories.get(red_alerts["title"], (None, "Unknown"))[0]
            if alert_category == "Unknown":
                print(f"Unknown alert category: {red_alerts['title']}")
                continue
            for alert_city in red_alerts["data"]:
                for obj in alert.locations:
                    if obj["label_he"] == alert_city and alert_city not in posted_alert_ids:
                        posted_alert_ids.add(alert_city)
                        migun_time = obj["migun_time"]
                        coordinates = alert.get_coordinates(alert_city)
                        english_city = html_to_discord(obj["mixname"])
                        new_alerts.append((english_city, alert_city, migun_time, coordinates))
                        recent_alerts.append((english_city, alert_city, migun_time, coordinates, time.time()))
                        alert.add_to_alert_history((english_city, alert_city, migun_time, coordinates, time.time()))

            recent_alerts = [alert for alert in recent_alerts if time.time() - alert[4] < 60]

            if new_alerts:
                description = f"Last updated: {time.strftime('%H:%M:%S')}\n\n"
                all_alerts = "\n• ".join(f"{city} ({migun_time}s)" for city, _, migun_time, _, _ in recent_alerts)
                all_alerts = f"• {all_alerts}"  # Add the first bullet point manually
                description += f"**Locations**:\n```{all_alerts}```\n**Type:**\n```\n{alert_category}```\n"

                if last_alert_time and (time.time() - last_alert_time < 60):
                    try:
                        message = await channel.fetch_message(last_message_id)
                        embed = message.embeds[0] if message.embeds else discord.Embed(
                            title=FRONT_COMMAND_ALERT_TITLE, color=alert_color)
                        embed.description = description
                        map_url = alert.get_map_url(
                            {city: coords for city, _, migun_time, coords, _ in recent_alerts},
                            {city for _, city, _, _, _ in recent_alerts},
                        )
                        await update_embed_with_image(embed, map_url, channel)
                        await message.edit(embed=embed)
                    except discord.NotFound:
                        await send_embed(alert, channel, description, recent_alerts, alert_color)
                else:
                    await send_embed(alert, channel, description, recent_alerts, alert_color)
                last_alert_time = time.time()

        await asyncio.sleep(2)


async def send_embed(alert, channel, description, recent_alerts, alert_color):
    global last_message_id
    embed = discord.Embed(title=FRONT_COMMAND_ALERT_TITLE, color=alert_color)
    embed.description = description
    map_url = alert.get_map_url(
        {city: coords for city, _, migun_time, coords, _ in recent_alerts},
        {city for _, city, _, _, _ in recent_alerts},
    )
    await update_embed_with_image(embed, map_url, channel)
    # print message formatting to send manually in case of an error
    print(f"{description}")



async def update_embed_with_image(embed, map_url, channel):
    global last_message_id
    async with aiohttp.ClientSession() as session:
        async with session.get(map_url) as response:
            if response.status == 200:
                image_data = await response.read()
                image_file = discord.File(BytesIO(image_data), filename="map.png")
                embed.set_image(url="attachment://map.png")
                message = await channel.send(embed=embed, file=image_file)
                last_message_id = message.id
            else:
                await channel.send("Failed to download the map image.")


@bot.command(name='registerAlertsBot')
@commands.is_owner()
async def register_alerts_bot(ctx):
    global CHANNEL_ID
    CHANNEL_ID = ctx.channel.id
    config['channel_id'] = CHANNEL_ID
    with open('config.json', 'w') as config_file:
        json.dump(config, config_file)
    await ctx.send(f"Alerts bot registered to this channel: {ctx.channel.name}")


@bot.command(name='alerts_stats')
async def alerts_stats(ctx, period: str = "1h"):
    alert = RedAlert()
    stats = alert.get_alert_stats(period)
    description = f"Alert stats for the past {period}:\n\n"
    if stats:
        for city, times in stats.items():
            times_str = ", ".join(times)
            description += f"- {city} at {times_str}\n"
    else:
        description += "No alerts in the given period."
    await ctx.send(description)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.loop.create_task(fetch_and_send_alerts())
    print("Bot is ready and listening for commands and alerts")


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
