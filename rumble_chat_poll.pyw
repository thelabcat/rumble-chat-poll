#!/usr/bin/env python3
#Rumble Chat Poll
#S.D.G.

import tomllib
import requests
import time
import calendar
import threading
from tkinter import *
from tkinter import ttk
import tkinter.messagebox as mb
import tkinter.simpledialog as dialog
import sys
import os

OP_PATH = __file__[:__file__.rfind(os.sep)] #Path of the extracted exec contents

MINIMUM_OPTIONS = 2 #Should never be less than two poll options, right?
OPT_FRAME_ROW = 0
ADD_BTTN_ROW = 1
ABORT_BTTN_ROW = 1
START_BTTN_ROW = 2

#Load config
CONFIG_TEMPLATE_PATH = OP_PATH + os.sep + "config_template.toml"
CONFIG_PATH = "config.toml"
try:
    with open(CONFIG_PATH, "rb") as f:
        CONFIG=tomllib.load(f)

except FileNotFoundError: #No config file, create it
    f = open(CONFIG_TEMPLATE_PATH)
    config_text = f.read()
    f.close()
    f = open(CONFIG_PATH, "w")
    f.write(config_text)
    f.close()
    CONFIG = tomllib.loads(config_text)

class Poll(threading.Thread):
    def __init__(self, api_url, options, numeric = True, livestream_id = None, duration = 60, showupdate_method = None, showfinal_method = None, master = None):
        """Poll chat using Rumble's API. If numeric, accept number votes as well as exact matches. Runtime is in seconds"""
        super().__init__(daemon = True)
        self.api_url = api_url
        self.options = options #Options of the poll
        self.numeric = numeric
        self.voted = [] #List of users who voted
        self.livestream_id = livestream_id
        self.duration = duration
        self.showupdate_method = showupdate_method
        self.showfinal_method = showfinal_method
        self.master = master
        self.killswitch = False

    def run(self, init_ballot = True):
        """Run the poll until the time runs out or it is aborted"""
        if init_ballot or not hasattr(self, "ballot"): #Will initialize the ballot if we don't have one even if init_ballot = False
            self.init_ballot() #Initialize the ballot

        self.start_time = time.time()
        while not self.killswitch and time.time() - self.start_time < self.duration:
            self.check_for_votes()
            if self.showupdate_method:
                self.showupdate_method(self)
            time.sleep(CONFIG["refreshRate"])

        if self.showfinal_method:
            self.showfinal_method(self)

    @property
    def current_winner(self):
        """Get current winner in the poll"""
        return max(self.ballot.keys(), key = lambda x: len(self.ballot[x])) #Find the option with the longest list of voters

    @property
    def total_votes(self):
        """Get current number of votes"""
        return sum([len(x) for x in self.ballot.values()])

    def init_ballot(self):
        """Create a dict ballot with lists for each option, to add votes to"""
        self.ballot = {}
        for option in self.options:
            self.ballot[option] = []

    def check_for_votes(self):
        """Get recent messages from the Rumble API and check for new votes"""
        response = requests.get(self.api_url, headers = CONFIG["APIHeaders"])
        if response.status_code != 200:
            self.killswitch = True
            mb.showerror("Could not check for votes", "HTTP error " + str(response.status_code))
            return
        livestream = self.get_livestream(response.json())
        if not livestream:
            return
        messages = livestream["chat"]["recent_messages"]
        for message in messages:
            if self.parse_message_time(message) >= self.start_time and message["username"] not in self.voted and self.parse_vote(message["text"]): #This person has not voted and their message parses to a vote
                #Vote!
                self.ballot[self.parse_vote(message["text"])].append(message["username"])
                self.voted.append(message["username"])
                print(message["username"], "voted for", self.parse_vote(message["text"]))

    def parse_vote(self, text):
        """Parse if a message was a vote, return None if it was not, return the option if it was"""
        if text in self.options: #Exact option match
            return text

        if self.numeric and text.isnumeric() and 0 < int(text) <= len(self.options): #Valid numerical vote
            return self.options[int(text) - 1]

        return False #Not a valid vote

    def get_livestream(self, json):
        """Select the specific livestream the poll is supposed to run on from the API json"""
        if len(json["livestreams"]) == 0:
            mb.showerror("You are not live", "Could not find any livestreams at the configured API URL.")
            self.killswitch = True
            if self.master:
                self.master.destroy()
            return

        if not self.livestream_id: #No specific livestream was specified
            return json["livestreams"][0] #Return the first one

        for possible in json["livestreams"]:
            if possible["id"] == self.livestream_id:
                return possible

        #We went through all the livestreams, and none of them matched
        raise ValueError("No livestream matches the specified ID")

    def parse_message_time(self, message):
        """Parse a message's UTC timestamp to seconds since epoch"""
        return calendar.timegm(time.strptime(message["created_on"], CONFIG["rumbleTimestampFormat"]))

class OptionWidgetGroup(object):
    def __init__(self, master, option_name = "", enable_delete = True):
        """Poll option frame"""
        self.master = master
        self.row = 0
        self.option_name = StringVar(self.master.option_frame, option_name) #Default option name
        self.__enable_delete = enable_delete
        self.configstate_build()

    def place_on_row(self, row = 0):
        """Grid our widgets onto a row of the master"""
        self.row = row
        self.option_field.grid(row = row, column = 0, sticky = NSEW)
        self.delete_button.grid(row = row, column = 1, sticky = NSEW)

    def configstate_destroy(self):
        """Destroy our widgets"""
        self.option_field.destroy()
        self.delete_button.destroy()

    @property
    def enable_delete(self):
        """Is delete enabled"""
        return self.__enable_delete

    @enable_delete.setter
    def enable_delete(self, value):
        """Enable or disable delete"""
        if value:
            self.delete_button["state"] = NORMAL
            self.__enable_delete = True
        else:
            self.delete_button["state"] = DISABLED
            self.__enable_delete = False

    def configstate_build(self):
        """Build the widgets for the configuration state"""
        self.option_field = Entry(self.master.option_frame, textvariable = self.option_name)

        self.delete_button = Button(self.master.option_frame, text = "Delete", command = lambda: self.master.delete_option(self))
        self.enable_delete = self.__enable_delete #Set the button's state

    def switch_to_viewstate(self):
        """Switch to poll option viewing state"""
        self.configstate_destroy()

        self.option_label = Label(self.master.option_frame, textvariable = self.option_name)
        self.option_label.grid(row = self.row, column = 0, sticky = E + W)

        self.option_amount_pb = ttk.Progressbar(self.master.option_frame, orient = HORIZONTAL, length = 100, mode = "determinate")
        self.option_amount_pb.grid(row = self.row, column = 1, sticky = NSEW)

    @property
    def percentage(self):
        return self.option_amount_pb["value"]

    @percentage.setter
    def percentage(self, new_percentage):
        if 0 <= int(new_percentage) <=100:
            self.option_amount_pb["value"] = int(new_percentage)
            #self.option_amount_label["text"] = str(int(new_percentage))+"%"

class PollWindow(Tk):
    def __init__(self):
        """Window to make and run a poll"""
        super().__init__()
        self.title("Rumble Chat Poll")
        self.option_wgs = []
        self.configstate_build(firstrun = True)
        self.api_url = self.get_api_url()
        if self.api_url:
            self.mainloop()
        else:
            self.destroy()

    def get_api_url(self):
        """Get the API URL, requesting it from the user if necessary"""
        try: #Load the API URL from memory, or ask the user for it later
            with open(CONFIG["apiURLFile"]) as f:
                api_url = f.read().strip()
        except FileNotFoundError:
            api_url = dialog.askstring("First time setup", "Paste your Rumble API URL from https://rumble.com/account/livestream-api")
            if not api_url:
                mb.showerror("Need API URL", "The program needs your Rumble API URL to access chat messages.")
            else:
                f = open(CONFIG["apiURLFile"], "w")
                f.write(api_url)
                f.close()
        return api_url

    def configstate_build(self, firstrun = False):
        """Build the GUI's initial configuration state view"""
        if firstrun:
            #Set up menu bar
            self.menubar = Menu(self)
            self["menu"] = self.menubar

            #Poll duration menu
            self.duration_menu = Menu(self.menubar)
            self.duration = IntVar(self)
            self.duration.set(CONFIG["durationDefault"])
            for dur_option in CONFIG["durationOptions"].keys():
                self.duration_menu.add_radiobutton(label = dur_option, variable = self.duration, value = CONFIG["durationOptions"][dur_option])
            self.menubar.add_cascade(label = "Duration", menu = self.duration_menu)

            #Set up option frame
            self.option_frame = Frame(self)
            self.option_frame.grid(row = OPT_FRAME_ROW, column = 0, sticky = NSEW)
            self.rowconfigure(OPT_FRAME_ROW, weight = 1)

        while len(self.option_wgs) < MINIMUM_OPTIONS: #Make sure we have minimum options
            self.add_option(build = False)

        for i in range(len(self.option_wgs)): #Pack all the option frames
            self.option_wgs[i].place_on_row(i)
            self.option_frame.rowconfigure(i, weight = 1) #Let option frames expand vertically
        self.option_frame.columnconfigure(0, weight = 1) #Let option fields expand horizontally

        if firstrun: #Create add option and start buttons
            self.add_option_button = Button(self, text = "+", command = self.add_option)
            self.add_option_button.grid(row = ADD_BTTN_ROW, sticky = NSEW)
            self.rowconfigure(ADD_BTTN_ROW, weight = 1)

            self.start_button = Button(self, text = "Start poll", command = self.start_poll)
            self.start_button.grid(row = START_BTTN_ROW, sticky = NSEW)
            self.rowconfigure(START_BTTN_ROW, weight = 1)

            self.columnconfigure(0, weight = 1)

    def add_option(self, build = True):
        """Add an option to our list and possibly rebuild GUI"""
        self.option_wgs.append(OptionWidgetGroup(self))
        if build:
            self.configstate_build()

    def delete_option(self, option_wg):
        """Delete an option"""
        option_wg.configstate_destroy()
        self.option_wgs.remove(option_wg)
        self.option_frame.rowconfigure(len(self.option_wgs), weight = 0) #Retighten the bottom row now that it is empty
        self.configstate_build()

    def start_poll(self):
        """Start the poll, and show results"""
        self.options = []
        for option_wg in self.option_wgs:
            if not option_wg.option_name.get(): #Option field left blank
                mb.showerror("Blank option", "Options cannot be blank.")
                return
            if option_wg.option_name.get() in self.options:
                mb.showerror("Duplicate options", "Options must all be unique.")
                return
            self.options.append(option_wg.option_name.get())

        for option_wg in self.option_wgs:
            option_wg.switch_to_viewstate()
        self.add_option_button.destroy()
        self.start_button.destroy()
        for row in (ADD_BTTN_ROW, START_BTTN_ROW):
            self.rowconfigure(row, weight = 0)

        #Let the progress bars expand now
        self.option_frame.columnconfigure(0, weight = 0)
        self.option_frame.columnconfigure(1, weight = 1)

        self.menubar.entryconfig("Duration", state = DISABLED) #Disable the duration menu once the poll starts

        #Create the poll
        self.poll = Poll(self.api_url, self.options, duration = self.duration.get(), showupdate_method = self.update_percentages, showfinal_method = self.show_finals, master = self)
        self.poll.start()


        self.abort_button = Button(self, text = "Abort poll", command = self.abort_poll)
        self.abort_button.grid(row = ABORT_BTTN_ROW, columnspan = 2, sticky = NSEW)

    def abort_poll(self):
        """End the poll prematurely"""
        self.poll.killswitch = True
        self.abort_button["state"] = "disabled"
        self.abort_button["text"] = "Aborting..."

    def update_percentages(self, poll):
        """Update the displayed percentages"""
        if not poll.total_votes: #There are no votes yet
            return
        for option_wg in self.option_wgs:
            option_wg.percentage = 100 * len(poll.ballot[option_wg.option_name.get()]) / poll.total_votes

    def show_finals(self, poll):
        """Show the poll winner"""
        self.abort_button["state"] = "disabled"
        self.abort_button["text"] = "Ended"

        if self.poll.killswitch:
            mb.showinfo("Poll aborted", "The current lead was " + poll.current_winner)
        else:
            mb.showinfo("Poll complete", "The winner was " + poll.current_winner)

PollWindow()
