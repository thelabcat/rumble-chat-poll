# Rumble Chat Poll program
Use the Rumble Livestream API to poll your chat. Users can vote with exact matches to option names, or numerically (the top option is 1).

## Dependencies
This program depends on the following Python libraries:
- [Requests](https://pypi.org/project/requests/)

## Usage
To run from source, install Python >= 3.9 and the listed dependencies, then run rumble_chat_poll.pyw. Don't edit config_template.toml.

When running from source or binary:
1. When the program first runs, it will copy config_template.toml (bundled in the executables) to config.toml (which you can edit later), and try to read your Rumble Livestream API URL from api_url.txt. If it can't find api_url.txt, it will ask you to enter the URL, and then it will save it. You can get that URL [here](https://rumble.com/account/livestream-api).
2. Select the desired poll duration from the "Duration" menu.
3. Enter your poll options in the empty fields. You can delete an option if you decide you don't want it, but you must have at least two options.
4. Click "Start" to start the poll. The GUI will switch to displaying the option names as labels, next to percentage bars. If the HTTPS connection(s) fails for any reason, the poll will abort. Chat votes by either typing an option exactly, or entering a number from 1 to however many options you have, 1 being the topmost.
5. Click "Abort" to stop the poll prematurely. Note that it will wait until the next refresh to stop.
6. When the poll completes, an alert will pop up showing the winner.
7. The poll window will stay open to show you the various ratios until you close it.
Hope this helps!

S.D.G.
