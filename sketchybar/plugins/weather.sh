#!/usr/bin/env bash

temp=$(curl 'v2d.wttr.in/Klahanie?format=%t&m')
condition=$(curl 'v2d.wttr.in/Klahanie?format=%c&m')


sketchybar -m --set weather icon="$condition" \
              --set weather label="$temp"

