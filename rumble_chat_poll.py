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

MINIMUM_OPTIONS = 2 #Should never be less than two poll options, right?

#Load config
CONFIG_PATH = "rumble_chat_poll.toml"
with open(CONFIG_PATH, "rb") as f:
    CONFIG=tomllib.load(f)

with open(CONFIG["apiURLFile"]) as f:
    API_URL = f.read().strip()

class Poll(object):
    def __init__(self, options, numeric = True, livestream_id = None, duration = 60, showupdate_method = None, showfinal_method = None):
        """Poll chat using Rumble's API. If numeric, accept number votes as well as exact matches. Runtime is in seconds"""
        self.numeric = numeric
        self.options = options
        self.voted = [] #List of users who voted
        self.livestream_id = livestream_id
        self.duration = duration
        self.showupdate_method = showupdate_method
        self.showfinal_method = showfinal_method

    def run_poll(self, init_ballot = True):
        """Run the poll until the time runs out or it is aborted"""
        self.manual_ended = False
        if init_ballot or not hasattr(self, "ballot"): #Will initialize the ballot if we don't have one even if init_ballot = False
            self.init_ballot() #Initialize the ballot

        self.start_time = time.time()
        while not self.manual_ended and time.time() - self.start_time < self.duration:
            time.sleep(CONFIG["refreshRate"])
            self.check_for_votes()
            if self.showupdate_method:
                self.showupdate_method(self)

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
        response = requests.get(API_URL, headers = CONFIG["APIHeaders"])
        if response.status_code != 200:
            print("HTTP error", response.status)
            return
        messages = self.get_livestream(response.json())["chat"]["recent_messages"]
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
        if len(json["livestreams"]) == [0]:
            raise Exception("You are not live")

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

class PollOption(object):
    def __init__(self, master, option_name = "", enable_delete = True):
        """Poll option frame"""
        self.master = master
        self.row = 0
        self.option_name = StringVar(self.master, option_name) #Default option name
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
        self.option_field = Entry(self.master, textvariable = self.option_name)

        self.delete_button = Button(self.master, text = "Delete", command = lambda: self.master.delete_option(self))
        self.enable_delete = self.__enable_delete #Set the button's state

    def switch_to_viewstate(self):
        """Switch to poll option viewing state"""
        self.configstate_destroy()

        self.option_label = Label(self.master, textvariable = self.option_name)
        self.option_label.grid(row = self.row, column = 0, sticky = E + W)

        self.option_amount_pb = ttk.Progressbar(self.master, orient = HORIZONTAL, length = 100, mode = "determinate")
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
        self.option_frames = []
        self.configstate_build(firstrun = True)
        self.mainloop()

    def configstate_build(self, firstrun = False):
        """Build the GUI's configuration state view"""
        while len(self.option_frames) < MINIMUM_OPTIONS: #Make sure we have minimum options
            self.add_option(build = False)

        for i in range(len(self.option_frames)): #Pack all the option frames
            self.option_frames[i].place_on_row(i)
            self.rowconfigure(i, weight = 1) #Let option frames expand vertically

        if firstrun: #Create add option and start buttons
            self.add_option_button = Button(self, text = "+", command = self.add_option)
            self.start_button = Button(self, text = "Start poll", command = self.start_poll)
        #Pack the buttons
        self.add_option_button.grid(row = i + 1, columnspan = 2, sticky = NSEW)
        self.rowconfigure(i + 1, weight = 1)
        self.start_button.grid(row = i + 2, columnspan = 2, sticky = NSEW)
        self.rowconfigure(i + 2, weight = 1)

        self.columnconfigure(0, weight = 1)

    def add_option(self, build = True):
        """Add an option to our list and possibly rebuild GUI"""
        self.option_frames.append(PollOption(self))
        if build:
            self.configstate_build()

    def delete_option(self, option_frame):
        """Delete an option"""
        option_frame.configstate_destroy()
        self.option_frames.remove(option_frame)
        self.configstate_build()

    def start_poll(self):
        """Start the poll, and show results"""
        self.options = []
        for option_frame in self.option_frames:
            if not option_frame.option_name.get(): #Option field left blank
                mb.showerror("Blank option", "Options cannot be blank.")
                return
            if option_frame.option_name.get() in self.options:
                mb.showerror("Duplicate options", "Options must all be unique.")
                return
            self.options.append(option_frame.option_name.get())

        for option_frame in self.option_frames:
            option_frame.switch_to_viewstate()
        self.add_option_button.destroy()
        self.start_button.destroy()

        #Let the progress bars expand now
        self.columnconfigure(0, weight = 0)
        self.columnconfigure(1, weight = 1)

        self.poll = Poll(self.options, showupdate_method = self.update_percentages, showfinal_method = self.show_finals)
        self.pollthread = threading.Thread(target = self.poll.run_poll)
        self.pollthread.start()

        abort_button_row = len(self.options)
        self.abort_button = Button(self, text = "Abort poll", command = self.abort_poll)
        self.abort_button.grid(row = abort_button_row, columnspan = 2, sticky = NSEW)
        self.rowconfigure(abort_button_row, weight = 1)
        self.rowconfigure(abort_button_row + 1, weight = 0) #Retighten the row below the abort button that was used for the start button

    def abort_poll(self):
        """End the poll prematurely"""
        self.poll.manual_ended = True
        self.abort_button["state"] = "disabled"
        self.abort_button["text"] = "Aborting..."

    def update_percentages(self, poll):
        """Update the displayed percentages"""
        if not poll.total_votes: #There are no votes yet
            return
        for option_frame in self.option_frames:
            option_frame.percentage = 100 * len(poll.ballot[option_frame.option_name.get()]) / poll.total_votes

    def show_finals(self, poll):
        """Show the poll winner"""
        self.abort_button["state"] = "disabled"
        self.abort_button["text"] = "Ended"

        if self.poll.manual_ended:
            mb.showinfo("Poll aborted", "The current lead was " + poll.current_winner)
        else:
            mb.showinfo("Poll complete", "The winner was " + poll.current_winner)

PollWindow()
