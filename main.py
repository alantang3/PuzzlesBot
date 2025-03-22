from typing import Final
import os
from dotenv import load_dotenv
from discord import Intents, Client, Message
from responses import get_response

#Step 0: Load token
load_dotenv()
TOKEN: Final[str] = os.getenv('DISCORD_TOKEN')
print(TOKEN)

#Step 1: Bot setup
intents: Intents = Intents.default()
intents.message_content = True
client: Client = Client(intents=intents)

#Step 2: Message functionality
async def send_message(message: Message, user_message: str) -> None:
  if not user_message:
    print('(Message was empty because intents were not enabled')
    return


#Step 3: Startup bot
@client.event
async def on_ready() -> None:
  print(f'{client.user} is now running!')

#Step 4: Handle incoming message
@client.event
async def on_message(message: Message) -> None: 
  if message.author == client.user:
    return

  username: str = str(message.author)
  user_message: str = message.content
  channel: str = str(message.channel)
  
  print(f'[{channel}] {username}: "{user_message}"')
  await send_mesage(message, user_message)

  #Step 5: Main entry point
  def main() -> None:
    client.run(token=TOKEN)

  if 
  

  prni






