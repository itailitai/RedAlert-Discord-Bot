# RedAlert Discord Bot
---
## Overview
RedAlert is a Discord bot designed to monitor and notify users of real-time alerts from the Israel Home Front Command. The bot fetches alerts, processes the data, and posts updates in a specified Discord channel. Additionally, the bot can provide alert statistics over a specified period.

## Features
- Fetches real-time alerts from the Israel Home Front Command.
- Posts alerts in a Discord channel with detailed information.
- Provides a static map with markers and polygon paths.
- Tracks and saves alert history.
- Supports fetching alert statistics for different periods.

## Configuration
The bot configuration is stored in `config.json`. Here is an example of the configuration file:

```json
{
  "discord_token": "YOUR_DISCORD_BOT_TOKEN",
  "google_maps_api_key": "YOUR_GOOGLE_MAPS_API_KEY",
  "alert_source_url": "https://www.oref.org.il/WarningMessages/alert/alerts.json",
  "data_files": {
    "english_cities": "../data/englishCities.json",
    "area_to_polygon": "area_to_polygon.json",
    "targets": "targets.json"
  },
  "channel_id": YOUR_CHANNEL_ID,
  "test_mode": false
}
```

## Setup and Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/redalert-bot.git
   cd redalert-bot
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure the `config.json` file with your Discord bot token, Google Maps API key, and other necessary information.

4. Run the bot:
   ```bash
   python main.py
   ```

## Usage
### Register the Bot to a Channel
To register the bot to a specific Discord channel, use the following command:
```
/registerAlertsBot
```
The bot will save the channel ID in the configuration file and start posting alerts in this channel.

### Fetch Alert Statistics
You can fetch alert statistics for a specified period using:
```
/alerts_stats [period]
```
Where `[period]` can be in the format of `Xd`, `Xh`, or `Xw` (e.g., `1d` for one day, `2h` for two hours).

## Files and Structure
- `main.py`: Main script to run the Discord bot.
- `config.json`: Configuration file for the bot.
- `alert_history.json`: File to save the history of alerts.
- `targets.json`: Contains the target areas for alerts.
- `area_to_polygon.json`: Maps areas to their polygon coordinates.
- `englishCities.json`: Maps city IDs to their English names.

## Contributing
If you would like to contribute to this project, please follow these steps:
1. Fork the repository.
2. Create a new branch (`git checkout -b feature-branch`).
3. Commit your changes (`git commit -m 'Add new feature'`).
4. Push to the branch (`git push origin feature-branch`).
5. Open a pull request.

## License
This project is licensed under the MIT License. See the `LICENSE` file for details.

## Acknowledgements
Special thanks to all the contributors and the open-source community for their support and resources.

---

Feel free to customize this `README.md` according to your project's specific details and requirements.
