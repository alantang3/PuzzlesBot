from typing import Final
import os
import discord
from dotenv import load_dotenv
from discord import Intents, Client, Message, TextChannel, app_commands, ChannelType
from discord.ext import commands
from discord.ui import View, Select
from responses import get_response
import random
import asyncio

BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

EMOJI = {"rock:": "🪨", "paper": "📄", "scissors": "✂️"}

class RPSButton(discord.ui.View):
  def __init__(self, game, timeout=30):  
    super().__init__(timeout=timeout)
    self.game = game

  @discord.ui.button(label="Rock🪨", style=discord.ButtonStyle.primary)
  async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
     await self.game.play(interaction, "rock")

  @discord.ui.button(label="Rock🪨",                style=discord.ButtonStyle.primary)
async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
   await self.game.play(interaction, "rock")

     