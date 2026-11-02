#!/bin/zsh
cd -- "$(dirname -- "$0")"
exec /usr/bin/python3 -B -u annotation_server.py
