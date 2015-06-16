from collections import deque
from datetime import datetime
import logging
import shlex
import asyncio

import hangups

from Core.Dispatcher import DispatcherSingleton
from Core.Util.UtilBot import is_user_blocked, check_if_can_run_command

from Core.Commands import *  # Makes sure that all commands in the Command directory are imported and registered.
from Core.Autoreplies import *  # Makes sure that all autoreplies in the Autoreplies directory are imported and registered.


class MessageHandler(object):
    """Handle Hangups conversation events"""

    def __init__(self, bot, command_char='/'):
        self.bot = bot
        self.command_char = command_char
        self.command_cache = deque(maxlen=20)
        self.autoreply_cache = deque(maxlen=20)
        self.autoreply_list = set()
        self.TIME_OUT = 1

        # Run on_connect_listeners
        for listener in DispatcherSingleton.on_connect_listeners:
            listener(bot)

        # MessageHandler should keep track of the replies itself, but it should not be exposed, so we use the Dispatcher
        # as the fall guy.
        for reply in DispatcherSingleton.autoreplies:
            self.autoreply_list.add(reply)

        # Queue up autoreplies in memory.

        default_autoreplies_list = self.bot.get_config_suboption(None, 'autoreplies')
        if default_autoreplies_list:
            for triggers, response in default_autoreplies_list:
                label = None
                for trigger in triggers:
                    if "?label=" in trigger:
                        label = trigger[7:]
                        triggers.remove(trigger)

                self.autoreply_list.add(AutoReply(triggers, response, None, label=label))

        for conv in self.bot._conv_list.get_all():
            try:
                autoreplies_list = bot.config['conversations'][conv.id_]["autoreplies"]
            except (KeyError, TypeError):
                autoreplies_list = default_autoreplies_list  # This is the case that a conv didn't set any autoreplies

            if autoreplies_list != default_autoreplies_list:
                if len(autoreplies_list) == 0:
                    self.autoreply_list.add(NullAutoReply([], None, conv.id_))
                else:
                    for triggers, response in autoreplies_list:
                        self.autoreply_list.add(AutoReply(triggers, response, conv.id_))

    @asyncio.coroutine
    def handle(self, event):
        if event.user.is_self or is_user_blocked(event.conv_id, event.user_id):
            return
        try:
            muted = not self.bot.config['conversations'][event.conv_id]['autoreplies_enabled']
        except KeyError:
            muted = False
            try:
                self.bot.config['conversations'][event.conv_id]['autoreplies_enabled'] = True
            except KeyError:
                self.bot.config['conversations'][event.conv_id] = {}
                self.bot.config['conversations'][event.conv_id]['autoreplies_enabled'] = True
                self.bot.config.save()

        event.text = event.text.replace('\xa0', ' ')

        """Handle conversation event"""
        if logging.root.level == logging.DEBUG:
            event.print_debug()

        if not event.user.is_self and event.text:
            if event.text.startswith(self.command_char):
                # Run command
                if event.text[len(self.command_char)] == '?':
                    event.text = "{}help".format(self.command_char)
                yield from self.handle_command(event)
            else:
                # Forward messages
                yield from self.handle_forward(event)
                if not muted:
                    # Send automatic replies
                    yield from self.handle_autoreply(event)

    @asyncio.coroutine
    def handle_command(self, event):
        """Handle command messages"""
        # Test if command handling is enabled
        if not self.bot.get_config_suboption(event.conv_id, 'commands_enabled'):
            return

        # Parse message
        line_args = shlex.split(event.text, posix=False)
        i = 0
        while i < len(line_args):
            line_args[i] = line_args[i].strip()
            if line_args[i] == '' or line_args[i] == '':
                line_args.remove(line_args[i])
            else:
                i += 1

        # Test if command length is sufficient
        if len(line_args) < 1:
            self.bot.send_message(event.conv,
                                  '{}: Not a valid command.'.format(event.user.full_name))
            return

        for prev_command in self.command_cache:
            if prev_command[0] == event.user_id[0] and prev_command[1] == line_args[0] and (
                        datetime.now() - prev_command[2]).seconds < self.TIME_OUT:
                self.bot.send_message(event.conv, "Ignored duplicate command from %s." % event.user.full_name)
                return
        self.command_cache.append((event.user_id[0], line_args[0], datetime.now()))


        # Test if user has permissions for running command (and subcommand)
        if check_if_can_run_command(self.bot, event, line_args[0].lower().replace(self.command_char, '')):
            # Run command
            yield from DispatcherSingleton.run(self.bot, event, self.command_char, *line_args[0:])
        else:
            self.bot.send_message(event.conv,
                                  "Sorry {}, I can't let you do that.".format(event.user.full_name))

    @asyncio.coroutine
    def handle_forward(self, event):
        # Test if message forwarding is enabled
        if not self.bot.get_config_suboption(event.conv_id, 'forwarding_enabled'):
            return

        forward_to_list = self.bot.get_config_suboption(event.conv_id, 'forward_to')
        if forward_to_list:
            for dst in forward_to_list:
                try:
                    conv = self.bot._conv_list.get(dst)
                except KeyError:
                    continue

                # Prepend forwarded message with name of sender
                link = 'https://plus.google.com/u/0/{}/about'.format(event.user_id.chat_id)
                segments = [hangups.ChatMessageSegment(event.user.full_name, hangups.SegmentType.LINK,
                                                       link_target=link, is_bold=True),
                            hangups.ChatMessageSegment(': ', is_bold=True)]
                # Copy original message segments
                segments.extend(event.conv_event.segments)
                # Append links to attachments (G+ photos) to forwarded message
                if event.conv_event.attachments:
                    segments.append(hangups.ChatMessageSegment('\n', hangups.SegmentType.LINE_BREAK))
                    segments.extend([hangups.ChatMessageSegment(link, hangups.SegmentType.LINK, link_target=link)
                                     for link in event.conv_event.attachments])
                self.bot.send_message_segments(conv, segments)

    @asyncio.coroutine
    def handle_autoreply(self, event):
        """Handle autoreplies to keywords in messages"""
        # Test if autoreplies are enabled
        if not self.bot.get_config_suboption(event.conv_id, 'autoreplies_enabled'):
            return

        for prev_auto in self.autoreply_cache:
            if prev_auto[0] == event.user_id[0] and prev_auto[1] == event.text and (
                        datetime.now() - prev_auto[2]).seconds < self.TIME_OUT:
                self.bot.send_message(event.conv, "Ignored duplicate command from %s." % event.user.full_name)
                return

        for autoreply in self.autoreply_list:
            if autoreply.is_triggered(event.text, event.conv_id):
                yield from self.bot._client.settyping(event.conv_id)
                # If you register an autoreply with a function callback.
                if autoreply.is_func():
                    line_args = shlex.split(event.text, posix=False)

                    i = 0
                    while i < len(line_args):
                        line_args[i] = line_args[i].strip()
                        if line_args[i] == '' or line_args[i] == '':
                            line_args.remove(line_args[i])
                        else:
                            i += 1

                    asyncio.async(autoreply.response_func(self.bot, event, *line_args))
                    return

                # Replaces the "{}" in the response with the text entered (generally for commands)
                event.text = autoreply.response.format(event.text)

                # For autoreplies that call already set commands.
                if autoreply.is_command(self.command_char):                    
                        yield from self.handle_command(event)
                        return
                else:
                    self.autoreply_cache.append((event.user_id[0], event.text, datetime.now()))
                    self.bot.send_message(event.conv, autoreply.response)
