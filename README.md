# Rumble Chat Poll program
Use the Rumble Livestream API to find your current stream, then tally chat votes from the internal chat SSE stream (via [Cocorum](https://github.com/thelabcat/cocorum)). Voters send an exact option name, or a number (`1` is the top option). Each account's first matching message counts.

## Dependencies
Python >= 3.12 and:

- [Cocorum](https://pypi.org/project/cocorum/)

## Usage
To run from source, install the listed dependency, then run `rumble_chat_poll.pyw`. Don't edit `config_template.toml`. The program copies it to `config.toml` beside the script (or beside the frozen executable) the first time it runs.

1. On first launch the program reads your Rumble Livestream API URL from `api_url.txt` in that same directory. If the file is missing, it asks for the URL and saves it. You can get the URL [here](https://rumble.com/account/livestream-api).
2. Select the poll duration from the Duration menu.
3. Enter poll options. Delete is unavailable while only two options remain. To pin a livestream when more than one is running, set `livestreamId` in `config.toml` to its base-36 id or its base-10 digits. Leave it blank to use the newest livestream.
4. Click "Start poll". The window switches to option labels and percentage bars as votes arrive. A failed connection leaves you on the setup form so you can try again.
5. Click "Abort poll" to stop early. The tally shown at that moment is the result.
6. When the timer finishes, an alert names the winner. A tie names every option that shares the lead. If nobody voted, it says so.
7. The window stays open with the final ratios until you close it.

Votes are the option text after trimming whitespace, or a plain integer from 1 through the number of options. Matching is case-sensitive. `01` and `+1` are not numeric votes.

S.D.G.
