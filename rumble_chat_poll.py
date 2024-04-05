#!/usr/bin/env python3
#Rumble Chat Poll
#S.D.G.

import tomllib
from urllib import requests
import time
#import threading
#from tkinter import *
#import tkinter.messagebox as mb

#Load config
CONFIG_PATH = "rumble_chat_poll.toml"
with open(CONFIG_PATH, "rb") as f:
    CONFIG=tomllib.load(f)

with open(CONFIG["apiURLFile"]) as f:
    API_URL = f.read().strip()

class Poll(object):
    def __init__(self, options, numeric = True, livestream_id = None, runtime = 5 * 60):
        """Poll chat using Rumble's API. If numeric, accept number votes as well as exact matches. Runtime is in seconds"""
        self.numeric = numeric
        self.options = options
        self.voted = [] #List of users who voted
        self.livestream_id = livestream_id
        self.duration = runtime

    def run_poll(self, init_ballot = True):
        """Run the poll until the time runs out"""

        if init_ballot or not hasattr(self, "ballot"): #Will initialize the ballot if we don't have one even if init_ballot = False
            self.init_ballot() #Initialize the ballot

        self.start_time = time.time()
        while time.time() - self.start_time < self.duration:
            time.sleep(CONFIG["refreshRate"])
            self.check_for_votes

    @property
    def current_winner(self):
        """Get current winner in the poll"""
        return max(self.ballot.keys(), key = lambda x: len(self.ballot[x])) #Find the option with the longest list of voters

    def init_ballot(self):
        """Create a dict ballot with lists for each option, to add votes to"""
        self.ballot = {}
        for option in self.options:
            self.ballot[option] = []

    def check_for_votes(self):
        """Get recent messages from the Rumble API and check for new votes"""
        response = requests.get(API_URL)
        if response.status != 200:
            print("HTTP error", response.status)
            return
        messages = self.get_livestream(response.json())["chat"]["recent_messages"]
        for message in messages:
            if self.parse_message_time(message) >= self.start_time and message["username"] not in self.voted and self.parse_vote(message["text"]): #This person has not voted and their message parses to a vote
                #Vote!
                self.ballot[self.parse_vote(message["text"])].append(message["username"])
                self.voted.append(message["username"])

    def parse_vote(self, text):
        """Parse if a message was a vote, return None if it was not, return the option if it was"""
        if text in self.options: #Exact option match
            return text

        if self.numeric and text.isNumeric() and 0 < int(text) <= len(self.options): #Valid numerical vote
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
        """Parse a message's timestamp to seconds since epoch"""
        return time.mktime(time.strptime(message["created_on"], CONFIG["rumbleTimestampFormat"]))
