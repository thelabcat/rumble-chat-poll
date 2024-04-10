# Rumble Chat Poll program
Use the Rumble Livestream API to poll your chat. Users can vote with exact matches to option names, or numerically (the top option is 1).

## Dependencies
This program depends on the following Python libraries:
- [Requests](https://pypi.org/project/requests/)

## Usage
To run from source, install Python 3.12 and the listed dependencies, then run rumble_chat_poll.pyw *after setting up your API URL.*
See the TOML configuration file for various system settings you may want to alter, but be careful what you do there.

1. Create a new file called api_url.txt in the same directory as the program, and paste your desired channel's Rumble Livestream API URL (with the key) into it. You can get that URL [here](https://rumble.com/account/livestream-api). Then, run the program.
2. Select the desired poll duration from the "Duration" menu.
3. Enter your poll options in the empty fields. You can delete an option if you decide you don't want it, but you must have at least two options.
4. Click "Start" to start the poll. The GUI will switch to displaying the option names as labels, next to percentage bars.
5. Click "Abort" to stop the poll prematurely. Note that it will wait until the next refresh to stop.
6. When the poll completes, an alert will pop up showing the winner.
7. The poll window will stay open to show you the various ratios until you close it.
Hope this helps!

S.D.G.
