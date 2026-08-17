#!/usr/bin/env zsh

DISPLAY_COUNT=$(aerospace list-monitors --count 2>/dev/null)
LAPTOP=$(( ${DISPLAY_COUNT:-1} == 1 ))

(( LAPTOP != ${1:-1} )) && sketchybar --reload
