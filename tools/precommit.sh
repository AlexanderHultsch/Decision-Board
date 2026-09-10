#!/bin/sh
# The gate before every commit: the example config parses, the whole suite is green.
set -e
cd "$(dirname "$0")/.."
python3 -c "import json;json.load(open('config/config.example.json'))"
python3 -m unittest discover -s tests -q
