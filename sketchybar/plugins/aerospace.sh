#!/usr/bin/env bash

if [ "$1" = "$FOCUSED_WORKSPACE" ]; then
  sketchybar --set $NAME \
    background.color=0xffc6a0f6 \
    icon.color=0xff24273a \
    label.color=0xff24273a \
    label.shadow.drawing=off \
    icon.shadow.drawing=off \
    background.border_width=2
else
  sketchybar --set $NAME \
    background.color=0xff363a4f \
    icon.color=0xffcad3f5 \
    label.color=0xffcad3f5 \
    label.shadow.drawing=off \
    icon.shadow.drawing=off \
    background.border_width=0
fi
