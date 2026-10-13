#!/bin/sh
set -eu
pyinstaller -F --add-data "config_template.toml:." --collect-submodules cocorum rumble_chat_poll.pyw
rm -rf build
rm -f rumble_chat_poll.spec
