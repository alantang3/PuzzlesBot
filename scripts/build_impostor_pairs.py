"""Generate games/data/impostor_words.json.

Word Impostor needs *related-but-different* word pairs (the impostor only
stays hidden if their word is plausibly confusable with the crew's). The
reliable way to mass-produce good pairs is to combine items *within a
category* — any two animals / apps / music genres are inherently related.

Run:  python scripts/build_impostor_pairs.py
"""

import json
import random
from itertools import combinations
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "games" / "data" / "impostor_words.json"

# Each category's items are mutually confusable, so every within-category
# combination is a valid pair. Mix of classic and trendy categories.
CATEGORIES: dict[str, list[str]] = {
    "land animals": [
        "dog", "cat", "horse", "cow", "pig", "sheep", "goat", "rabbit", "fox",
        "wolf", "bear", "lion", "tiger", "deer", "squirrel", "raccoon",
        "elephant", "giraffe", "zebra", "kangaroo", "monkey", "panda",
    ],
    "sea animals": [
        "whale", "dolphin", "shark", "octopus", "crab", "lobster", "jellyfish",
        "seahorse", "starfish", "eel", "squid", "clam", "oyster", "turtle",
        "stingray", "seal", "otter",
    ],
    "birds": [
        "eagle", "owl", "hawk", "sparrow", "parrot", "penguin", "flamingo",
        "pigeon", "crow", "swan", "duck", "peacock", "robin", "falcon",
        "ostrich", "woodpecker",
    ],
    "bugs": [
        "ant", "bee", "wasp", "beetle", "butterfly", "moth", "spider",
        "scorpion", "dragonfly", "ladybug", "cricket", "grasshopper",
        "mosquito", "caterpillar",
    ],
    "fruits": [
        "apple", "orange", "banana", "grape", "mango", "peach", "pear",
        "plum", "cherry", "strawberry", "blueberry", "watermelon",
        "pineapple", "kiwi", "lemon", "lime", "coconut", "raspberry",
    ],
    "vegetables": [
        "carrot", "potato", "onion", "broccoli", "spinach", "lettuce",
        "tomato", "cucumber", "pepper", "corn", "peas", "celery",
        "mushroom", "cabbage", "eggplant", "zucchini",
    ],
    "desserts": [
        "cake", "pie", "cookie", "brownie", "donut", "cupcake", "ice cream",
        "pudding", "cheesecake", "muffin", "waffle", "pancake", "tart",
        "macaron", "tiramisu",
    ],
    "fast food": [
        "burger", "fries", "pizza", "taco", "burrito", "hot dog", "nuggets",
        "sandwich", "wrap", "sub", "wings", "quesadilla",
    ],
    "drinks": [
        "coffee", "tea", "soda", "juice", "milk", "lemonade", "smoothie",
        "milkshake", "boba", "matcha", "espresso", "latte", "cappuccino",
        "hot chocolate", "energy drink",
    ],
    "alcohol": [
        "beer", "wine", "whiskey", "vodka", "rum", "tequila", "gin", "cider",
        "champagne", "sake", "brandy", "margarita",
    ],
    "sports": [
        "soccer", "basketball", "football", "baseball", "hockey", "tennis",
        "golf", "volleyball", "cricket", "rugby", "badminton", "lacrosse",
        "boxing", "wrestling", "skiing", "surfing", "bowling",
    ],
    "instruments": [
        "guitar", "piano", "violin", "cello", "drums", "flute", "trumpet",
        "saxophone", "clarinet", "harp", "banjo", "ukulele", "trombone",
        "accordion", "harmonica",
    ],
    "music genres": [
        "rap", "pop", "rock", "country", "jazz", "blues", "metal", "punk",
        "indie", "EDM", "techno", "house", "reggae", "classical", "lofi",
        "drill", "trap", "K-pop", "R&B", "disco",
    ],
    "social apps": [
        "TikTok", "Instagram", "Snapchat", "Twitter", "Threads", "Reddit",
        "Discord", "Facebook", "YouTube", "Twitch", "Tumblr", "Pinterest",
        "LinkedIn", "BeReal",
    ],
    "streaming services": [
        "Netflix", "Hulu", "Disney+", "HBO", "Spotify", "Apple Music",
        "Prime Video", "Peacock", "Paramount+", "SoundCloud", "Tidal",
    ],
    "video games": [
        "Minecraft", "Fortnite", "Roblox", "Valorant", "League of Legends",
        "Among Us", "Fall Guys", "Overwatch", "Call of Duty", "Apex Legends",
        "Animal Crossing", "Terraria", "Rocket League", "Genshin Impact",
        "Stardew Valley", "GTA",
    ],
    "tech & devices": [
        "iPhone", "Android", "Mac", "PC", "iPad", "AirPods", "VR headset",
        "Kindle", "smartwatch", "Nintendo Switch", "PlayStation", "Xbox",
        "drone", "Alexa",
    ],
    "apparel brands": [
        "Nike", "Adidas", "Puma", "Under Armour", "Vans", "Converse",
        "Crocs", "Birkenstocks", "Gucci", "Louis Vuitton", "Supreme",
        "North Face", "Lululemon",
    ],
    "food chains": [
        "Starbucks", "Dunkin", "McDonald's", "Burger King", "Wendy's",
        "Chipotle", "Subway", "Taco Bell", "Chick-fil-A", "KFC", "Panera",
        "Dairy Queen", "Five Guys",
    ],
    "ground vehicles": [
        "car", "truck", "motorcycle", "bicycle", "bus", "train", "scooter",
        "van", "jeep", "sports car", "limo", "pickup truck", "tractor",
    ],
    "air & sea travel": [
        "airplane", "helicopter", "jet", "hot air balloon", "glider", "boat",
        "yacht", "cruise ship", "submarine", "canoe", "kayak", "ferry",
        "jet ski",
    ],
    "weather": [
        "rain", "snow", "hail", "fog", "thunder", "lightning", "tornado",
        "hurricane", "blizzard", "drizzle", "sleet", "sunshine", "heatwave",
    ],
    "landforms": [
        "mountain", "hill", "valley", "river", "lake", "ocean", "desert",
        "forest", "jungle", "island", "peninsula", "canyon", "waterfall",
        "glacier", "volcano", "cave", "swamp",
    ],
    "times & seasons": [
        "summer", "winter", "spring", "autumn", "morning", "noon", "evening",
        "midnight", "dawn", "dusk",
    ],
    "jobs": [
        "doctor", "nurse", "teacher", "professor", "lawyer", "judge", "chef",
        "baker", "pilot", "firefighter", "police officer", "engineer",
        "plumber", "electrician", "dentist", "scientist", "artist",
        "accountant", "barber", "farmer",
    ],
    "fantasy creatures": [
        "wizard", "witch", "dragon", "unicorn", "mermaid", "vampire",
        "werewolf", "zombie", "ghost", "goblin", "troll", "elf", "fairy",
        "phoenix", "griffin", "giant",
    ],
    "superhero universe": [
        "Marvel", "DC", "Spider-Man", "Batman", "Superman", "Iron Man",
        "Wonder Woman", "Thor", "Hulk", "Captain America", "Flash",
        "Aquaman", "Black Panther",
    ],
    "gen z slang": [
        "rizz", "charisma", "situationship", "relationship", "ghosting",
        "the ick", "red flag", "green flag", "vibe", "aura", "drip", "swag",
        "glow up", "main character",
    ],
    "tabletop games": [
        "chess", "checkers", "Monopoly", "Scrabble", "Clue", "Uno", "poker",
        "Go Fish", "Risk", "Jenga", "dominoes", "Connect Four", "battleship",
        "Catan",
    ],
    "school subjects": [
        "math", "science", "history", "english", "geography", "chemistry",
        "biology", "physics", "art", "music", "gym", "economics",
    ],
}

# Hand-picked cross-category rivalries / memes that aren't a single list but
# are classic head-to-heads.
EXTRA: list[list[str]] = [
    ["rap", "white girl music"],
    ["Drake", "Kendrick"],
    ["Taylor Swift", "Beyonce"],
    ["LeBron", "Jordan"],
    ["Messi", "Ronaldo"],
    ["Star Wars", "Star Trek"],
    ["anime", "cartoons"],
    ["Coke", "Pepsi"],
    ["pizza", "burger"],
    ["dog", "cat"],
    ["coffee", "tea"],
    ["pirate", "ninja"],
    ["robot", "alien"],
    ["zombie", "vampire"],
    ["crypto", "stocks"],
    ["NFT", "meme coin"],
    ["Tesla", "pickup truck"],
    ["vinyl", "cassette"],
    ["meme", "inside joke"],
    ["podcast", "radio"],
    ["influencer", "celebrity"],
    ["thrift store", "fast fashion"],
    ["reality TV", "sitcom"],
    ["rom-com", "horror movie"],
    ["pen", "pencil"],
    ["sword", "shield"],
    ["king", "queen"],
    ["sun", "moon"],
]


def build() -> list[list[str]]:
    seen: set[tuple[str, str]] = set()
    pairs: list[list[str]] = []
    for items in CATEGORIES.values():
        for a, b in combinations(sorted(set(items)), 2):
            key = tuple(sorted((a.lower(), b.lower())))
            if a.lower() == b.lower() or key in seen:
                continue
            seen.add(key)
            pairs.append([a, b])
    for a, b in EXTRA:
        key = tuple(sorted((a.lower(), b.lower())))
        if a.lower() == b.lower() or key in seen:
            continue
        seen.add(key)
        pairs.append([a, b])
    random.shuffle(pairs)
    return pairs


if __name__ == "__main__":
    pairs = build()
    OUT.write_text(
        json.dumps({"pairs": pairs}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(pairs)} pairs to {OUT}")
