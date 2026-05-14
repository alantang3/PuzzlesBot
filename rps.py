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

# What each choice beats
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

MOVES = {"rock", "paper", "scissors"}
# Emoji for each choice
EMOJI = {"rock:": "🪨", "paper": "📄", "scissors": "✂️"}

class RPSButton(discord.ui.View):
  def __init__(self, game, player1, player2, timeout=30):  
    super().__init__(timeout=timeout)
    self.player1 = player1
    self.player2 = player2
    self.picks = {player1: None, player2: None}
    self.game = game

  def disable_all(self):
    for child in self.children:
      child.disabled = True
    
  @discord.ui.button(label="Rock🪨", style=discord.ButtonStyle.primary)
  async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
    await self.handle_pick(interaction, "rock")

  @discord.ui.button(label="Paper📄", style=discord.ButtonStyle.primary)
  async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
    await self.handle_pick(interaction, "paper")

  @discord.ui.button(label="Scissors✂️",   style=discord.ButtonStyle.primary)
  async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
    await self.handle_pick(interaction, "scissors")

  async def hande_pick(self, interaction, choice):
    if self.game.mode == "Single Player":
      bot_choice = random.choice(list(MOVES))
      result = decide_winner(choice, bot_choice)

      if result == "tie":
        message = f"You: {EMOJI[choice]} | Bot: {EMOJI[bot_choice]} | Result: Tie!"
      elif result == "Player 1":
        message = f"You: {EMOJI[choice]} | Bot: {EMOJI[bot_choice]} | Result: You win!"
      elif result == "Player 2":
        message = f"You: {EMOJI[choice]} | Bot: {EMOJI[bot_choice]} | Result: You lose!"

      self.disable_all()
    
    elif self.game.mode == "Multiplayer":
      #keep track of each player's choices
      self.game.picks[interaction.user.id] = choice
      await interaction.response.send_message(f"You chose {choice}!", ephemeral=True)
      
      
def decide_winner(player_1_choice, player_2_choice):
    if player_1_choice == player_2_choice: 
      return "Tie"
    if player_2_choice == BEATS[player_1_choice]:
      return "Player 1"
    else: return "Player 2"
      
    
    
     