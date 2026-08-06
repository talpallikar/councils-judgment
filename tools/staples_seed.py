"""Fallback seed staple lists per format.

The live lists come from MTGTop8 (competitive formats) and EDHREC (commander)
via staple_sources.py, refreshed automatically and stored in the database.
These hand-curated lists are only used before the first successful refresh or
when every live source is unreachable. Cards with fewer than 2 unique
illustrations are skipped automatically at matchup time, so an imperfect
entry here is harmless.

"standard" is intentionally empty: Standard rotates too fast for a hardcoded
seed. Before the first MTGTop8 refresh succeeds, the server falls back to
Scryfall popularity search for Standard-legal cards.
"""

FORMATS = [
    ("standard", "Standard"),
    ("pioneer", "Pioneer"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("vintage", "Vintage"),
    ("commander", "Commander"),
]

STAPLES = {
    "standard": [],  # built dynamically from Scryfall, see app.py
    "pioneer": [
        "Thoughtseize", "Fatal Push", "Fable of the Mirror-Breaker",
        "Sheoldred, the Apocalypse", "Ledger Shredder", "Arclight Phoenix",
        "Treasure Cruise", "Consider", "Opt", "Spell Pierce", "Mutavault",
        "Supreme Verdict", "Teferi, Hero of Dominaria", "The Wandering Emperor",
        "Portable Hole", "Temporary Lockdown", "Nykthos, Shrine to Nyx",
        "Llanowar Elves", "Elvish Mystic", "Karn, the Great Creator",
        "Cavern of Souls", "Collected Company", "Mystical Dispute",
        "Bonecrusher Giant", "Brazen Borrower", "Bloodtithe Harvester",
        "Graveyard Trespasser", "Liliana of the Veil", "Boseiju, Who Endures",
        "Otawara, Soaring City", "Godless Shrine", "Steam Vents",
        "Sacred Foundry", "Overgrown Tomb", "Watery Grave", "Blood Crypt",
        "Hallowed Fountain", "Breeding Pool", "Stomping Ground",
        "Temple Garden", "Mana Confluence", "Monastery Swiftspear",
        "Sylvan Caryatid", "Courser of Kruphix", "Chained to the Rocks",
        "Abrupt Decay", "Dreadbore", "Elspeth, Sun's Champion",
        "Skysovereign, Consul Flagship", "Torrential Gearhulk",
    ],
    "modern": [
        "Ragavan, Nimble Pilferer", "Lightning Bolt", "Counterspell",
        "Thoughtseize", "Fatal Push", "Path to Exile", "Solitude",
        "Endurance", "Subtlety", "Force of Negation", "Murktide Regent",
        "Dragon's Rage Channeler", "Monastery Swiftspear", "Unholy Heat",
        "Expressive Iteration", "Mishra's Bauble", "Urza's Saga",
        "Orcish Bowmasters", "Wrenn and Six", "Boseiju, Who Endures",
        "Otawara, Soaring City", "Prismatic Ending", "Leyline Binding",
        "Teferi, Time Raveler", "Chalice of the Void", "Aether Vial",
        "Primeval Titan", "Amulet of Vigor", "Scapeshift", "Goblin Guide",
        "Snapcaster Mage", "Cryptic Command", "Archmage's Charm",
        "Tarmogoyf", "Liliana of the Veil", "Dark Confidant",
        "Mox Opal", "Splinter Twin", "Faithless Looting", "Green Sun's Zenith",
        "Scalding Tarn", "Misty Rainforest", "Polluted Delta",
        "Flooded Strand", "Bloodstained Mire", "Windswept Heath",
        "Wooded Foothills", "Arid Mesa", "Marsh Flats", "Verdant Catacombs",
        "Cavern of Souls", "Ancient Stirrings", "Karn, the Great Creator",
        "Crashing Footfalls", "Living End", "Shardless Agent",
        "Indomitable Creativity", "Archon of Cruelty", "Ensnaring Bridge",
    ],
    "legacy": [
        "Brainstorm", "Ponder", "Force of Will", "Daze", "Wasteland",
        "Swords to Plowshares", "Delver of Secrets", "Murktide Regent",
        "Dragon's Rage Channeler", "Reanimate", "Entomb", "Animate Dead",
        "Griselbrand", "Archon of Cruelty", "Show and Tell", "Sneak Attack",
        "Ancient Tomb", "City of Traitors", "Chalice of the Void",
        "Lotus Petal", "Lion's Eye Diamond", "Dark Ritual",
        "Tendrils of Agony", "Gamble", "Mother of Runes",
        "Stoneforge Mystic", "Batterskull", "Jace, the Mind Sculptor",
        "Snapcaster Mage", "Hydroblast", "Pyroblast", "Red Elemental Blast",
        "Surgical Extraction", "Force of Negation", "Life from the Loam",
        "Exploration", "Mox Diamond", "Green Sun's Zenith",
        "Knight of the Reliquary", "Karakas", "Rishadan Port",
        "Thalia, Guardian of Thraben", "Aether Vial", "True-Name Nemesis",
        "Baleful Strix", "Orcish Bowmasters", "The One Ring",
        "Sylvan Library", "Abrupt Decay", "Hymn to Tourach", "Sinkhole",
        "Volcanic Island", "Underground Sea", "Tundra", "Tropical Island",
        "Bayou", "Polluted Delta", "Flooded Strand",
    ],
    "vintage": [
        "Black Lotus", "Ancestral Recall", "Time Walk", "Timetwister",
        "Mox Sapphire", "Mox Jet", "Mox Ruby", "Mox Pearl", "Mox Emerald",
        "Sol Ring", "Mana Crypt", "Mana Vault", "Demonic Tutor",
        "Vampiric Tutor", "Mystical Tutor", "Brainstorm", "Ponder",
        "Gitaxian Probe", "Force of Will", "Force of Negation",
        "Mental Misstep", "Flusterstorm", "Mana Drain",
        "Sphere of Resistance", "Thorn of Amethyst", "Lodestone Golem",
        "Tinker", "Bolas's Citadel", "Yawgmoth's Will", "Tendrils of Agony",
        "Dark Ritual", "Lion's Eye Diamond", "Paradoxical Outcome",
        "Dack Fayden", "Narset, Parter of Veils", "Lavinia, Azorius Renegade",
        "Time Vault", "Voltaic Key", "Blightsteel Colossus",
        "Tolarian Academy", "Library of Alexandria", "Wasteland",
        "Strip Mine", "Lurrus of the Dream-Den", "Urza's Saga",
        "The One Ring", "Orcish Bowmasters", "Swords to Plowshares",
        "Snapcaster Mage", "Deathrite Shaman", "Underground Sea",
        "Volcanic Island",
    ],
    "commander": [
        "Sol Ring", "Command Tower", "Arcane Signet", "Cyclonic Rift",
        "Rhystic Study", "Smothering Tithe", "Dockside Extortionist",
        "Swords to Plowshares", "Path to Exile", "Counterspell", "Swan Song",
        "Cultivate", "Kodama's Reach", "Farseek", "Nature's Lore",
        "Beast Within", "Chaos Warp", "Generous Gift", "Demonic Tutor",
        "Vampiric Tutor", "Craterhoof Behemoth", "Eternal Witness",
        "Solemn Simulacrum", "Lightning Greaves", "Swiftfoot Boots",
        "Fierce Guardianship", "Deflecting Swat", "Teferi's Protection",
        "Heroic Intervention", "Bojuka Bog", "Reliquary Tower",
        "Fellwar Stone", "Mystic Remora", "Esper Sentinel",
        "The Great Henge", "Toxic Deluge", "Blasphemous Act", "Vandalblast",
        "Wrath of God", "Damnation", "Propaganda", "Ghostly Prison",
        "Sakura-Tribe Elder", "Birds of Paradise", "Llanowar Elves",
        "Mana Crypt", "Mana Vault", "Ancient Tomb", "Rise of the Dark Realms",
        "Finale of Devastation", "Exotic Orchard", "Sun Titan",
        "Avacyn, Angel of Hope", "Ur-Dragon", "Atraxa, Praetors' Voice",
        "Edgar Markov", "Krenko, Mob Boss", "Omnath, Locus of Creation",
    ],
}
