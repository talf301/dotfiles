#!/usr/bin/env bash

temp=$(curl 'v2d.wttr.in/Klahanie')


sketchybar -m --set full_weather label="$temp"

