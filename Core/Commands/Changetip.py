# -*- coding: utf-8 -*-
from changetip.bots.base import BaseBot

import hangups
from hangups import hangouts_pb2

import hashlib
import datetime

class ChangeTipHangups(BaseBot):
   channel = "googleplus"
   changetip_api_key = os.getenv("CHANGETIP_API_KEY")

   def test(self, bot, event, word):
      bot.send_message(event.conv, word)


   def process_command(self, bot, event, sender, receiver, meta_sender, meta_receiver):
      print(sender)
      print(receiver)
      message = event.text
      tip_data = {
          "sender": "%s" % sender,
          "receiver": "%s" % receiver,
          "message": message,
          "context_uid": self.unique_id("googleplus"+" "+sender+": "+message),
          "meta": { "sender_display": meta_sender, "receiver_display": meta_receiver }
      }
      print(tip_data)
      response = self.send_tip(**tip_data)
      if response.get("error_code") == "invalid_sender":
        message_response = "@%s To send your first tip, login with your Twitch.tv account on ChangeTip: %s" % (sender.capitalize(), self.info_url)
        bot.send_message(event.conv, message_response)
      elif response.get("error_code") == "duplicate_context_uid":
        message_response = "@%s That looks like a duplicate tip." % sender.capitalize()
        bot.send_message(event.conv, message_response)
      elif response.get("error_message"):
        bot.send_message(event.conv, response.get("error_message"))
      elif response.get("state") in ["ok", "accepted"]:
        tip = response["tip"]
        message_response = "@ you've been successfully tipped! Collect it "
        segments = [hangups.ChatMessageSegment("@{} you've been successfully tipped! Collect it ".format(meta_receiver),
                                           is_bold=True)]
        segments.append(hangups.ChatMessageSegment("here", hangouts_pb2.SEGMENT_TYPE_LINK,
                                                   link_target=tip["collect_url"]))
        bot.send_message_segments(event.conv, segments)
      print(response)

   def unique_id(self, post_data):
      checksum = hashlib.md5()
      checksum.update(str(post_data).encode("utf8"))
      checksum.update(datetime.datetime.now().strftime('%Y-%m-%d:%H:%M:00').encode("utf8"))
      return checksum.hexdigest()[:16]
