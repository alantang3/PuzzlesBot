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
#Load token 
load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')

#Bot setup
intents: Intents = Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


#Message functionality
async def send_message(message: Message, user_message: str) -> None:
  if not user_message:
    print('(Message was empty because intents were not enabled')
    return


#Dropdown menu
class GameModeSelectView(View):

  def __init__(self):
    super().__init__()
    self.add_item(GameModeSelect(self))


#Choose game mode
class GameModeSelect(Select):

  def __init__(self, parent_view: GameModeSelectView):
    self.parent_view = parent_view
    self.has_selected = False
    options = [
        discord.SelectOption(label="Single Player", description="Play alone"),
        discord.SelectOption(label="Multiplayer",
                             description="Play with friends"),
    ]

    super().__init__(
        placeholder="Choose a game mode...",
        options=options,
        min_values=1,
        max_values=1,
    )

  async def callback(self, interaction: discord.Interaction):
    if self.has_selected:
      await interaction.response.send_message(
          "You have already selected a game mode. Please restart the command to choose a new game mode",
          ephemeral=True)
      return
    self.has_selected = True
    mode = self.values[0]
    await interaction.response.send_message(
        f"Game mode set to {mode}. Now choose a game to play:",
        view=GameSelectView(mode),
        ephemeral=True)


class GameSelectView(View):

  def __init__(self, mode: str):
    super().__init__()
    self.mode = mode
    self.add_item(GameSelect(mode))


#Choose game
class GameSelect(Select):

  def __init__(self, mode: str):
    self.mode = mode
    self.has_selected = False
    options = [
        discord.SelectOption(label="Cryptograms",
                             description="Decode the cryptograms."),
        discord.SelectOption(label="Wordle", description="Guess the word"),
        discord.SelectOption(label="Sports Trivia",
                             description="Test your sports knowledge!"),
        discord.SelectOption(label="Guess the Flag",
                             description="Guess the country flag."),
        discord.SelectOption(label="Who's That Pokemon?",
                             description="Guess the Pokemon."),
        discord.SelectOption(label="Higher or Lower?",
                             description="Higher or lower?"),
        discord.SelectOption(label="Hangman", description="Guess the word."),
        discord.SelectOption(label="Who Sent the Message?",
                             description="Guess the message sender."),
        discord.SelectOption(label="Guess the Number!",
                             description="Guess the number."),
        discord.SelectOption(label="Rock Paper Scissors",
                             description="Play Rock Paper Scissors.")
    ]

    super().__init__(
        placeholder="Choose a game to play...",
        options=options,
        min_values=1,
        max_values=1,
    )

  async def callback(self, interaction: discord.Interaction):
    if self.has_selected:
      await interaction.response.send_message(
          "You have already selected a game. Please restart the command to choose a new game",
          ephemeral=True)
      return

    self.has_selected = True
    selected_game = self.values[0]
      
    if self.mode == "Single Player":
      if isinstance(interaction.channel, TextChannel):
        #Create private threaad
        thread = await interaction.channel.create_thread(
            name=f"{interaction.user.name}'s {selected_game} Lobby",
            type=discord.ChannelType.private_thread,
            invitable=True)
        await thread.add_user(interaction.user)
        await thread.send(
            f"{interaction.user.mention}, Welcome to your private game lobby! Type '!start' to begin!"
        )
        # Schedule deletion after 20 minutes (1200 seconds)
        async def delete_thread_after_delay():
          await asyncio.sleep(1200)
          await thread.delete()

        # Start the deletion task
        asyncio.create_task(delete_thread_after_delay())

      else:
        await interaction.response.send_message(
            "This command must be used in a regular text channel.",
            ephemeral=True)

    elif self.mode == "Multiplayer":
      if isinstance(interaction.channel, TextChannel):
        #Create private threaad
        thread = await interaction.channel.create_thread(
            name=f"{interaction.user.name}'s {selected_game} Lobby",
            type=discord.ChannelType.private_thread,
            invitable=True)
        await thread.add_user(interaction.user)
        await thread.send(
            f"{interaction.user.mention}, Welcome to your private game lobby! Ping your friends to join the game. Once everyone's here type '!start' to begin!"
        )
        # Schedule deletion after 20 minutes (1200 seconds)
        async def delete_thread_after_delay():
          await asyncio.sleep(1200)
          await thread.delete()

        # Start the deletion task
        asyncio.create_task(delete_thread_after_delay())

    else:
      await interaction.response.send_message(
          "This command must be used in a regular text channel.",
          ephemeral=True)


#Slash command: /game
@bot.tree.command(name="game",
                  description="Start a game",
                  guild=discord.Object(id=1353035066082721896))
async def game(interaction: discord.Interaction):
  await interaction.response.send_message("Choose a gamemode:",
                                          view=GameModeSelectView(),
                                          ephemeral=True)


#Startup bot
@bot.event
async def on_ready() -> None:
  await bot.wait_until_ready()
  test_guild = discord.Object(id=1353035066082721896)
  await bot.tree.sync(guild=test_guild)
  print(f'{bot.user} is now running!')


#Handle incoming message
@bot.event
async def on_message(message: Message) -> None:
  if message.author == bot.user:
    return

  username: str = str(message.author)
  user_message: str = message.content
  channel: str = str(message.channel)

  print(f'[{channel}] {username}: "{user_message}"')

  await send_message(message, user_message)

  if message.content.strip():
    temp = message.content.lower()

    if temp.startswith('i\'m '):
      pick = random.randint(1, 2)
      if pick == 1:
        await message.channel.send('Hi ' + message.content[4::] +
                                   ', I\'m Alan Tang, the goat')
      if pick == 2:
        await message.channel.send('Hi ' + message.content[4::] +
                                   ', I\'m David Tan, the fraud')


#Main entry point
def main() -> None:
  bot.run(token=TOKEN)


if __name__ == '__main__':
  main()
