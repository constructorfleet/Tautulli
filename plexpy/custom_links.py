# -*- coding: utf-8 -*-

#  This file is part of Tautulli.
#
#  Tautulli is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Tautulli is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Tautulli.  If not, see <http://www.gnu.org/licenses/>.

from collections import OrderedDict
import json
from itertools import groupby
import os
import re

import arrow
from mako.lookup import TemplateLookup
from mako import exceptions

import plexpy
from plexpy import config
from plexpy import common
from plexpy import database
from plexpy import helpers
from plexpy import libraries
from plexpy import logger
from plexpy import newsletter_handler
from plexpy import pmsconnect
from plexpy.notifiers import send_notification, EMAIL


def get_custom_links(link_id=None, location=None, active=None):
    def filter_by_id(id):
        if link_id is not None:
            if isinstance(link_id, str):
                return id == link_id
            if isinstance(link_id, list):
                return id in link_spec["id"]
        return True

    def filter_by_location(link_spec):
        if location is not None:
            if isinstance(location, str):
                return link_spec["location"] == location
            if isinstance(location, list):
                return link_spec["location"] in location
        return True

    def filter_by_active(link_spec):
        if active is not None:
            return link_spec["active"] == config.bool_int(active)
        
        return True

    return [
        {
            "id": id
            **link_spec,
        }
        for id, link_spec
        in plexpy.CONFIG.CUSTOM_LINKS
        if filter_by_active(id) and filter_by_location(link_spec) and filter_by_active(link_spec)
    ]


def get_custom_link(link_id=None):
    links = get_custom_links(link_id=link_id)
    if links is None or len(links) == 0:
        return None
    return links[0]


def get_nav_links():
    return get_custom_links(location="nav", active=1)


def get_menu_links():
    return get_custom_links(location="menu", active=1)
    

def delete_custom_link(link_id=None):
    if link_id is None or link_id not in plexpy.CONFIG.CUSTOM_LINKS:
        return False
    
    plexpy.CONFIG.CUSTOM_LINKS.pop(link_id)
    plexpy.CONFIG.write()
    return True


def add_custom_link(href=None, icon=None, location=None, active=None, **kwargs):
    link_spec = config.custom_link({
        "href": href,
        "icon": icon,
        "location": location,
        "active": active,
        **kwargs
    })
    if link_spec is None:
        return None

    link_id = f"custom_link_{len(plexpy.CONFIG.CUSTOM_LINKS.keys() + 1)}"
    plexpy.CONFIG.CUSTOM_LINKS[link_id] = link_spec
    plexpy.CONFIG.write()

    return link_id


def update_custom_link(link_id=None, **kwargs):
    existing_spec = plexpy.CONFIG.CUSTOM_LINKS.get(link_id, None)
    if existing_spec is None
        return False

    link_spec = config.custom_link({
        **existing_spec,
        **kwargs
    })

    if link_spec is None:
        return False

    plexpy.CONFIG.CUSTOM_LINKS[link_id] = link_spec
    plexpy.CONFIG.CUSTOM_LINKS.write()

    return True