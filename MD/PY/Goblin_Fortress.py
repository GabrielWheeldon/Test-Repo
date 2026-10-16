import json
from collections import deque
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import random
import re
import time
import tkinter as tk
from tkinter import messagebox


BLOCK_PRESETS_DIR = Path(__file__).resolve().parent / "blocks"
ITEM_PRESETS_DIR = Path(__file__).resolve().parent / "items"
CRAFTING_RECIPES_FILE = Path(__file__).resolve().parent / "recipes" / "recipes.json"
SAVE_GAMES_DIR = Path.home() / ".goblin_fortress" / "saves"
SAVE_VERSION = 1
DEFAULT_FOREGROUND_COLOR = "#222222"
DEFAULT_BLOCK_BACKGROUND = "#8B5A2B"
ITEM_FOREGROUND_PALETTE = (
    "#8C3B2A", "#A34F18", "#866116", "#4C7624",
    "#176B55", "#176C83", "#3459A6", "#63469A",
    "#923F78", "#8B3948", "#585858", "#316E9A",
)
BODY_PARTS = (
    ("head", "Head"),
    ("chest", "Chest"),
    ("stomach", "Stomach"),
    ("left_arm", "Left arm"),
    ("right_arm", "Right arm"),
    ("left_leg", "Left leg"),
    ("right_leg", "Right leg"),
    ("heart", "Heart"),
    ("lungs", "Lungs"),
    ("liver", "Liver"),
)
PART_SPOIL_TIME = 600.0
INFECTION_DAMAGE_INTERVAL = 5.0
COLD_BLOCK_IDS = {
    "blue_ice", "clear_ice", "frosted_ice", "glacier_stone",
    "packed_ice", "permafrost", "snow_brick",
}


@lru_cache(maxsize=16384)
def _terrain_corner_value(corner_x, corner_y):
    seed = corner_x * 73856093 ^ corner_y * 19349663
    return random.Random(seed).random()


@lru_cache(maxsize=32768)
def _tree_spawn_roll(x, y):
    seed = x * 73856093 ^ y * 19349663 ^ 0x5A17
    return random.Random(seed).random()


def is_hex_color(value):
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(character in "0123456789abcdefABCDEF" for character in value[1:])
    )


def darken_color(color, depth_distance):
    if not is_hex_color(color):
        raise ValueError(f"Invalid hex color: {color!r}")
    brightness = max(0.2, 0.82 ** max(0, depth_distance))
    return scale_color(color, brightness)


@lru_cache(maxsize=8192)
def color_at_depth(color, depth, player_depth):
    depth_offset = depth - player_depth
    if depth_offset == 0:
        brightness = 1.12
    elif depth_offset > 0:
        brightness = max(0.2, 0.82 ** (depth_offset - 1))
    else:
        brightness = max(0.2, 0.82 ** abs(depth_offset))
    return scale_color(color, brightness)


@lru_cache(maxsize=512)
def scale_color(color, brightness):
    if not is_hex_color(color):
        raise ValueError(f"Invalid hex color: {color!r}")
    channels = [
        min(255, round(int(color[index:index + 2], 16) * brightness))
        for index in (1, 3, 5)
    ]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


class gameObject:
    def __init__(
        self, name, icon, description, pos,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        self.name = name
        self.icon = icon
        self.description = description
        self.pos = pos
        self.foreground_color = foreground_color
        self.break_time = 1.0
        self.stackable = False
        self.count = 1

    def move(self, new_pos):
        self.pos = new_pos


class block(gameObject):
    def __init__(
        self, name, icon, description, pos, solid=True, breakable=True,
        tool_type=None, foreground_color=DEFAULT_FOREGROUND_COLOR,
        background_color=DEFAULT_BLOCK_BACKGROUND,
        break_time=1.0,
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.solid = solid
        self.breakable = breakable
        self.placeable = True
        self.stackable = True
        self.preset_id = None
        self.tool_type = tool_type
        self.background_color = background_color
        self.break_time = break_time
        self.grows_into_tree = False

    @classmethod
    def from_preset(cls, preset, pos):
        if preset.get("kind") == "door":
            return door.from_preset(preset, pos)
        result = cls(
            preset["name"],
            preset["icon"],
            preset["description"],
            pos,
            preset.get("solid", True),
            preset.get("breakable", True),
            preset.get("tool_type"),
            preset.get("foreground_color", DEFAULT_FOREGROUND_COLOR),
            preset.get("background_color", DEFAULT_BLOCK_BACKGROUND),
            preset.get("break_time", 1.0),
        )
        result.preset_id = preset.get("id")
        result.grows_into_tree = preset.get("grows_into_tree", False)
        if result.grows_into_tree:
            result.planted_at = time.monotonic()
        return result


class door(block):
    def __init__(self, name, icon, description, pos, foreground_color, background_color, break_time):
        super().__init__(
            name, icon, description, pos,
            foreground_color=foreground_color,
            background_color=background_color,
            break_time=break_time,
        )
        self.is_open = False

    @classmethod
    def from_preset(cls, preset, pos):
        result = cls(
            preset["name"],
            preset["icon"],
            preset["description"],
            pos,
            preset.get("foreground_color", DEFAULT_FOREGROUND_COLOR),
            preset.get("background_color", DEFAULT_BLOCK_BACKGROUND),
            preset.get("break_time", 1.0),
        )
        result.preset_id = preset.get("id")
        return result

    def toggle(self):
        self.is_open = not self.is_open


class craftedItem(gameObject):
    def __init__(
        self, item_id, name, icon, description, pos=(0, 0, 0), placeable=True,
        foreground_color=DEFAULT_FOREGROUND_COLOR, stackable=True,
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.item_id = item_id
        self.placeable = placeable
        self.stackable = stackable

    @classmethod
    def from_preset(cls, preset, pos=(0, 0, 0)):
        return cls(
            preset["id"],
            preset["name"],
            preset["icon"],
            preset["description"],
            pos,
            preset.get("placeable", True),
            preset.get("foreground_color", DEFAULT_FOREGROUND_COLOR),
            preset.get(
                "stackable",
                preset.get("kind", "item") in ("item", "block", "usable"),
            ),
        )


class toolItem(craftedItem):
    def __init__(
        self, item_id, name, icon, description, pos, tool_type, durability,
        drop_bonus, mining_speed=2.0,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        super().__init__(
            item_id, name, icon, description, pos, False, foreground_color, False
        )
        self.tool_type = tool_type
        self.durability = durability
        self.drop_bonus = drop_bonus
        self.mining_speed = mining_speed


class usableItem(craftedItem):
    def __init__(
        self, item_id, name, icon, description, pos, heal_amount,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        super().__init__(
            item_id, name, icon, description, pos, False, foreground_color, True
        )
        self.heal_amount = heal_amount


class bodyPartItem(gameObject):
    def __init__(
        self, part_id, part_name, health, spoil_remaining=PART_SPOIL_TIME,
        last_updated=None, spoiled=False, infected=False,
    ):
        super().__init__(
            f"{part_name}{' (spoiled)' if spoiled else ''}",
            "!", f"A detached {part_name.lower()}.", (0, 0, 0),
            "#B84B4B" if spoiled else "#C98E72",
        )
        self.part_id = part_id
        self.part_name = part_name
        self.health = health
        self.spoil_remaining = spoil_remaining
        self.last_updated = time.time() if last_updated is None else last_updated
        self.spoiled = spoiled
        self.infected = infected
        self.placeable = False

    def update_spoilage(self, cold, current_time=None):
        now = time.time() if current_time is None else current_time
        if not cold and not self.spoiled:
            self.spoil_remaining = max(
                0.0, self.spoil_remaining - max(0.0, now - self.last_updated)
            )
            if self.spoil_remaining <= 0:
                self.spoiled = True
                self.name = f"{self.part_name} (spoiled)"
                self.foreground_color = "#B84B4B"
        self.last_updated = now


class storageObject(gameObject):
    def __init__(
        self, name, icon, description, pos, contents=None, item_id=None,
        foreground_color="#C47A27",
        background_color="#573714",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.contents = contents if contents is not None else []
        self.item_id = item_id
        self.placeable = True
        self.background_color = background_color


class craftingTable(gameObject):
    def __init__(
        self, name, icon, description, pos, item_id="crafting_table",
        foreground_color="#8C5A32",
        background_color="#54351F",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.item_id = item_id
        self.placeable = True
        self.background_color = background_color


class furnace(gameObject):
    def __init__(
        self, name, icon, description, pos, item_id="furnace",
        foreground_color="#687789",
        background_color="#303944",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.item_id = item_id
        self.placeable = True
        self.background_color = background_color


class campfire(furnace):
    def __init__(
        self, name, icon, description, pos,
        foreground_color="#D65A31",
        background_color="#632719",
    ):
        super().__init__(
            name, icon, description, pos, "campfire",
            foreground_color, background_color,
        )


class restingPlace(gameObject):
    def __init__(
        self, name, icon, description, pos, item_id="bed",
        foreground_color="#5A78A8",
        background_color="#273954",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.item_id = item_id
        self.placeable = True
        self.background_color = background_color


def load_block_presets():
    presets = {}
    for preset_path in sorted(BLOCK_PRESETS_DIR.glob("*.json")):
        with preset_path.open(encoding="utf-8") as preset_file:
            preset = json.load(preset_file)
        for required_field in ("name", "icon", "description"):
            if not isinstance(preset.get(required_field), str):
                raise ValueError(
                    f"{preset_path} must define a string '{required_field}' field."
                )
        preset["id"] = preset_path.stem
        preset.setdefault("foreground_color", DEFAULT_FOREGROUND_COLOR)
        preset.setdefault("background_color", DEFAULT_BLOCK_BACKGROUND)
        for color_field in ("foreground_color", "background_color"):
            if not is_hex_color(preset[color_field]):
                raise ValueError(
                    f"{preset_path} must define a hex '{color_field}' value."
                )
        presets[preset_path.stem] = preset

    if "dirt" not in presets:
        raise FileNotFoundError(
            f"Required dirt block preset was not found in {BLOCK_PRESETS_DIR}."
        )
    return presets


def load_item_presets():
    presets = {}
    for preset_path in sorted(ITEM_PRESETS_DIR.glob("*.json")):
        with preset_path.open(encoding="utf-8") as preset_file:
            preset = json.load(preset_file)
        for required_field in ("name", "icon", "description"):
            if not isinstance(preset.get(required_field), str):
                raise ValueError(
                    f"{preset_path} must define a string '{required_field}' field."
                )
        preset_id = preset_path.stem
        if preset.get("id", preset_id) != preset_id:
            raise ValueError(f"{preset_path} id must match its filename.")
        preset["id"] = preset_id
        if "foreground_color" not in preset:
            palette_index = sum(map(ord, preset_id)) % len(ITEM_FOREGROUND_PALETTE)
            preset["foreground_color"] = ITEM_FOREGROUND_PALETTE[palette_index]
        if not is_hex_color(preset["foreground_color"]):
            raise ValueError(
                f"{preset_path} must define a hex 'foreground_color' value."
            )
        if not isinstance(preset.get("placeable"), bool):
            raise ValueError(f"{preset_path} must define a boolean 'placeable' field.")
        if "stackable" in preset and not isinstance(preset["stackable"], bool):
            raise ValueError(f"{preset_path} must define a boolean 'stackable' field.")
        if preset.get("kind", "item") not in (
            "item", "block", "tool", "usable", "storage",
            "crafting_station", "furnace", "campfire", "resting_place",
            "door",
        ):
            raise ValueError(f"{preset_path} has an unsupported item kind.")
        presets[preset_id] = preset
    return presets


def load_entity_presets():
    entity_presets_dir = Path(__file__).resolve().parent / "entities"
    presets = {}
    for preset_path in sorted(entity_presets_dir.glob("*.json")):
        with preset_path.open(encoding="utf-8") as preset_file:
            preset = json.load(preset_file)
        preset_id = preset_path.stem
        if preset.get("id", preset_id) != preset_id:
            raise ValueError(f"{preset_path} id must match its filename.")
        for field in ("name", "icon", "description"):
            if not isinstance(preset.get(field), str) or not preset[field]:
                raise ValueError(f"{preset_path} must define a non-empty '{field}'.")
        if (
            not isinstance(preset.get("health"), int)
            or preset["health"] < 1
            or not isinstance(preset.get("attack_power"), int)
            or preset["attack_power"] < 0
        ):
            raise ValueError(
                f"{preset_path} must define positive health and non-negative attack_power."
            )
        if preset.get("disposition") not in ("hostile", "passive"):
            raise ValueError(
                f"{preset_path} disposition must be 'hostile' or 'passive'."
            )
        if not is_hex_color(preset.get("foreground_color")):
            raise ValueError(f"{preset_path} must define a hex foreground_color.")
        preset["id"] = preset_id
        presets[preset_id] = preset
    return presets


def load_crafting_recipes():
    with CRAFTING_RECIPES_FILE.open(encoding="utf-8") as recipe_file:
        recipes = json.load(recipe_file)
    if not isinstance(recipes, list):
        raise ValueError(f"{CRAFTING_RECIPES_FILE} must contain a JSON list.")
    if len(recipes) < 50:
        raise ValueError(
            f"{CRAFTING_RECIPES_FILE} must contain at least 50 crafting recipes."
        )

    item_presets = load_item_presets()
    recipe_ids = set()
    for recipe in recipes:
        for field in ("id", "name", "station", "ingredients", "result"):
            if field not in recipe:
                raise ValueError(f"Crafting recipe is missing '{field}': {recipe!r}")
        if recipe["id"] in recipe_ids:
            raise ValueError(f"Duplicate crafting recipe id: {recipe['id']}")
        recipe_ids.add(recipe["id"])
        if recipe["station"] not in ("crafting_table", "furnace", "campfire"):
            raise ValueError(f"Invalid crafting station in recipe {recipe['id']}.")
        if not recipe["ingredients"]:
            raise ValueError(f"Recipe {recipe['id']} needs at least one ingredient.")
        for ingredient in recipe["ingredients"]:
            if (
                not isinstance(ingredient.get("id"), str)
                or not isinstance(ingredient.get("quantity"), int)
                or ingredient["quantity"] < 1
            ):
                raise ValueError(f"Invalid ingredient in recipe {recipe['id']}.")
        result = recipe["result"]
        if (
            not isinstance(result.get("id"), str)
            or not isinstance(result.get("name"), str)
            or not isinstance(result.get("icon"), str)
            or not isinstance(result.get("quantity"), int)
            or result["quantity"] < 1
        ):
            raise ValueError(f"Invalid result in recipe {recipe['id']}.")
        if result["id"] not in item_presets:
            raise FileNotFoundError(
                f"Recipe {recipe['id']} has no item preset for '{result['id']}'."
            )
        item_preset = item_presets[result["id"]]
        for field in ("name", "icon", "description"):
            if item_preset[field] != result[field]:
                raise ValueError(
                    f"Recipe {recipe['id']} and item preset '{result['id']}' "
                    f"must have matching '{field}' values."
                )
    return recipes


def item_identifier(item):
    if isinstance(item, block):
        return item.preset_id
    return getattr(item, "item_id", None)


def add_item_to_collection(collection, item):
    if getattr(item, "stackable", False):
        item_id = item_identifier(item)
        existing = next(
            (
                candidate for candidate in collection
                if getattr(candidate, "stackable", False)
                and type(candidate) is type(item)
                and item_identifier(candidate) == item_id
            ),
            None,
        )
        if existing is not None:
            existing.count += item.count
            return existing
    collection.append(item)
    return item


def remove_item_from_collection(collection, item, quantity=1):
    if item not in collection:
        return 0
    if not getattr(item, "stackable", False):
        collection.remove(item)
        return 1
    removed = min(max(0, quantity), item.count)
    item.count -= removed
    if item.count <= 0:
        collection.remove(item)
    return removed


def use_item(item, player_character):
    if not isinstance(item, usableItem):
        return False, "That item cannot be used directly."
    restored, restored_limbs = player_character.heal_damage(item.heal_amount)
    if restored <= 0 and restored_limbs <= 0:
        return False, "Your health and limbs are already fully healed."
    player_character.remove_from_inventory(item, 1)
    return True, (
        f"Used {item.name}; restored {restored} body health and "
        f"{restored_limbs} limb health."
    )


def missing_recipe_ingredients(inventory, recipe):
    required = {}
    for ingredient in recipe["ingredients"]:
        item_id = ingredient["id"]
        required[item_id] = required.get(item_id, 0) + ingredient["quantity"]

    available = {}
    for inventory_item in inventory:
        item_id = item_identifier(inventory_item)
        available[item_id] = available.get(item_id, 0) + getattr(
            inventory_item, "count", 1
        )
    missing = [
        f"{quantity - available.get(item_id, 0)} more {item_id.replace('_', ' ')}"
        for item_id, quantity in required.items()
        if available.get(item_id, 0) < quantity
    ]
    return required, missing


def consume_recipe_ingredients(inventory, required):
    for item_id, quantity in required.items():
        remaining = quantity
        for ingredient_item in list(inventory):
            if item_identifier(ingredient_item) != item_id:
                continue
            remaining -= remove_item_from_collection(
                inventory, ingredient_item, remaining
            )
            if remaining == 0:
                break


def add_crafting_result(inventory, result, game_world):
    if result["quantity"] < 1:
        raise ValueError("Crafting result quantity must be positive.")
    result_item = game_world.create_crafting_result(result)
    if getattr(result_item, "stackable", False):
        result_item.count = result["quantity"]
        add_item_to_collection(inventory, result_item)
    else:
        add_item_to_collection(inventory, result_item)
        for _ in range(result["quantity"] - 1):
            add_item_to_collection(
                inventory, game_world.create_crafting_result(result)
            )


def craft_recipe(inventory, recipe, game_world):
    required, missing = missing_recipe_ingredients(inventory, recipe)
    if missing:
        return False, missing

    consume_recipe_ingredients(inventory, required)
    add_crafting_result(inventory, recipe["result"], game_world)
    return True, []


class entity(gameObject):
    def __init__(
        self, name, icon, description, pos, health, attack_power,
        foreground_color="#D4A72C", disposition="passive",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.health = health
        self.max_health = health
        self.attack_power = attack_power
        self.disposition = disposition
        self.last_attack_time = 0.0
        self.limb_health = {
            part_id: health for part_id, _label in BODY_PARTS
        }
        self.attached_parts = {part_id for part_id, _label in BODY_PARTS}
        self.infected = False
        self.infection_elapsed = 0.0

    @classmethod
    def from_preset(cls, preset, pos=(0, 0, 0)):
        creature = cls(
            preset["name"],
            preset["icon"],
            preset["description"],
            pos,
            preset["health"],
            preset["attack_power"],
            preset["foreground_color"],
            preset["disposition"],
        )
        creature.preset_id = preset["id"]
        return creature

    @property
    def hp(self):
        return self.health

    @hp.setter
    def hp(self, value):
        self.health = max(0, min(self.max_health, value))

    def take_damage(self, amount, part_id=None):
        if amount <= 0:
            return None, 0
        if part_id is None:
            available_parts = [
                part_id for part_id, _label in BODY_PARTS
                if part_id in self.attached_parts
            ]
            if not available_parts:
                return None, 0
            part_id = random.choice(available_parts)
        if part_id not in self.limb_health:
            raise ValueError(f"Unknown body part: {part_id}")
        if part_id not in self.attached_parts:
            return None, 0
        damage = min(amount, self.health)
        self.limb_health[part_id] = max(
            0, self.limb_health[part_id] - amount
        )
        self.hp -= damage
        return part_id, damage

    def heal_damage(self, amount):
        if amount <= 0:
            return 0, 0
        restored_health = min(amount, self.max_health - self.health)
        self.hp += restored_health
        restored_limb_health = 0
        for part_id, _label in BODY_PARTS:
            if part_id not in self.attached_parts:
                continue
            current = self.limb_health[part_id]
            restored = min(amount, self.max_health - current)
            self.limb_health[part_id] += restored
            restored_limb_health += restored
        return restored_health, restored_limb_health

    def restore_limb_health(self, limb_health):
        if not isinstance(limb_health, dict):
            raise ValueError("Saved limb health must be an object.")
        for part_id, value in limb_health.items():
            if (
                part_id not in self.limb_health
                or not isinstance(value, int)
                or not 0 <= value <= self.max_health
            ):
                raise ValueError(f"Invalid saved health for body part '{part_id}'.")
            self.limb_health[part_id] = value

    def restore_body_state(
        self, attached_parts=None, infected=False, infection_elapsed=0.0
    ):
        if attached_parts is not None:
            if (
                not isinstance(attached_parts, list)
                or any(
                    not isinstance(part_id, str)
                    or part_id not in self.limb_health
                    for part_id in attached_parts
                )
                or len(set(attached_parts)) != len(attached_parts)
            ):
                raise ValueError("Saved attached body parts are invalid.")
            self.attached_parts = set(attached_parts)
            for part_id in self.limb_health:
                if part_id not in self.attached_parts:
                    self.limb_health[part_id] = 0
        if not isinstance(infected, bool):
            raise ValueError("Saved infection status must be a boolean.")
        if (
            isinstance(infection_elapsed, bool)
            or not isinstance(infection_elapsed, (int, float))
            or not 0 <= infection_elapsed < INFECTION_DAMAGE_INTERVAL
        ):
            raise ValueError("Saved infection time is invalid.")
        self.infected = infected
        self.infection_elapsed = float(infection_elapsed)

    def amputate(self, part_id):
        if part_id not in self.attached_parts:
            return None
        part_health = self.limb_health[part_id]
        self.attached_parts.remove(part_id)
        self.limb_health[part_id] = 0
        return part_health

    def reattach(self, part_item):
        if (
            part_item.part_id in self.attached_parts
            or part_item.part_id not in self.limb_health
        ):
            return False
        self.attached_parts.add(part_item.part_id)
        self.limb_health[part_item.part_id] = min(
            self.max_health, part_item.health
        )
        if part_item.spoiled or part_item.infected:
            self.infected = True
        return True

    def heal_fully(self):
        self.hp = self.max_health
        for part_id, _label in BODY_PARTS:
            if part_id in self.attached_parts:
                self.limb_health[part_id] = self.max_health

    @property
    def effective_attack_power(self):
        arm_health = (
            sum(
                self.limb_health[part_id]
                for part_id in ("left_arm", "right_arm")
            )
        ) / (2 * self.max_health)
        if self.attack_power <= 0:
            return 0
        return max(1, round(self.attack_power * (0.5 + 0.5 * arm_health)))

    def can_move(self):
        if not ({"left_leg", "right_leg"} & self.attached_parts):
            return False
        leg_health = (
            sum(
                self.limb_health[part_id]
                for part_id in ("left_leg", "right_leg")
            )
        ) / (2 * self.max_health)
        stumble_chance = (1 - leg_health) * 0.75
        return random.random() >= stumble_chance


class player(entity):
    def __init__(self, name, icon, description, pos, health, attack_power, inventory):
        super().__init__(name, icon, description, pos, health, attack_power)
        self.inventory = inventory

    def add_to_inventory(self, item):
        return add_item_to_collection(self.inventory, item)

    def remove_from_inventory(self, item, quantity=1):
        return remove_item_from_collection(self.inventory, item, quantity)

    def display_inventory(self):
        if not self.inventory:
            print("Your inventory is empty.")
        else:
            print("Inventory:")
            for item in self.inventory:
                quantity = f" x{item.count}" if item.stackable else ""
                print(f"- {item.name}{quantity}: {item.description}")


class world:
    Y_LEVEL_COUNT = 40
    SURFACE_DEPTH = 10
    VISIBLE_DEPTHS_BELOW = 6

    def __init__(self):
        self.layers: list[list[list[gameObject | None]]] = []
        self.map: list[list[gameObject | None]] = []
        self.subsurface: list[list[list[gameObject | None]]] = []
        self.chunks: dict[
            tuple[int, int], list[list[list[gameObject | None]]]
        ] = {}
        self.entities_by_chunk: dict[tuple[int, int], set[entity]] = {}
        self.saplings: set[block] = set()
        self._generated_chunk_entities = {}
        self._block_prototypes = {}
        self._block_presets = None
        self._item_presets = None
        self._entity_presets = None
        self.chunk_width = 0
        self.chunk_height = 0
        self.active_chunk = (0, 0)

    def _get_block_presets(self):
        if self._block_presets is None:
            self._block_presets = load_block_presets()
        return self._block_presets

    def _get_entity_presets(self):
        if self._entity_presets is None:
            self._entity_presets = load_entity_presets()
        return self._entity_presets

    def _get_item_presets(self):
        if self._item_presets is None:
            self._item_presets = load_item_presets()
        return self._item_presets

    def _create_terrain_block(self, preset_id, pos):
        prototype = self._block_prototypes.get(preset_id)
        if prototype is None:
            prototype = block.from_preset(
                self._get_block_presets()[preset_id], (0, 0, 0)
            )
            self._block_prototypes[preset_id] = prototype
        # Copy preset fields without re-running the constructor for every tile.
        terrain_block = object.__new__(block)
        terrain_block.__dict__ = prototype.__dict__.copy()
        terrain_block.pos = pos
        return terrain_block

    def _index_chunk_objects(self, chunk_key, chunk_layers):
        chunk_entities = self._generated_chunk_entities.pop(
            chunk_key, None
        )
        if chunk_entities is None:
            chunk_entities = set()
            for layer in chunk_layers:
                for row in layer:
                    for tile in row:
                        if isinstance(tile, entity):
                            chunk_entities.add(tile)
                        elif isinstance(tile, block) and tile.grows_into_tree:
                            self.saplings.add(tile)
        if chunk_entities:
            self.entities_by_chunk[chunk_key] = chunk_entities

    def rebuild_object_indexes(self):
        self.entities_by_chunk.clear()
        self.saplings.clear()
        self._generated_chunk_entities.clear()
        for chunk_key, chunk_layers in self.chunks.items():
            self._index_chunk_objects(chunk_key, chunk_layers)

    def _track_tile_change(self, chunk_key, old_tile, new_tile):
        if isinstance(old_tile, entity):
            chunk_entities = self.entities_by_chunk.get(chunk_key)
            if chunk_entities is not None:
                chunk_entities.discard(old_tile)
                if not chunk_entities:
                    del self.entities_by_chunk[chunk_key]
        elif isinstance(old_tile, block) and old_tile.grows_into_tree:
            self.saplings.discard(old_tile)
        if isinstance(new_tile, entity):
            self.entities_by_chunk.setdefault(chunk_key, set()).add(new_tile)
        elif isinstance(new_tile, block) and new_tile.grows_into_tree:
            self.saplings.add(new_tile)

    def generate_world(self, width: int, height: int):
        if width < 1 or height < 1:
            raise ValueError("Chunk width and height must be positive.")
        self.chunk_width = width
        self.chunk_height = height
        self.chunks.clear()
        self.entities_by_chunk.clear()
        self.saplings.clear()
        self._generated_chunk_entities.clear()
        self.active_chunk = (0, 0)
        self.layers = self._generate_chunk(0, 0)
        self.map = self.layers[self.SURFACE_DEPTH]
        self.subsurface = self.layers[self.SURFACE_DEPTH + 1:]
        self.chunks[(0, 0)] = self.layers
        self._index_chunk_objects((0, 0), self.layers)

    @staticmethod
    @lru_cache(maxsize=32768)
    def _terrain_noise(x, y, scale):
        grid_x, offset_x = divmod(x, scale)
        grid_y, offset_y = divmod(y, scale)

        def smooth(value):
            return value * value * (3 - 2 * value)

        horizontal = smooth(offset_x / scale)
        vertical = smooth(offset_y / scale)
        top_left = _terrain_corner_value(grid_x, grid_y)
        top_right = _terrain_corner_value(grid_x + 1, grid_y)
        bottom_left = _terrain_corner_value(grid_x, grid_y + 1)
        bottom_right = _terrain_corner_value(grid_x + 1, grid_y + 1)
        top = top_left + (top_right - top_left) * horizontal
        bottom = bottom_left + (bottom_right - bottom_left) * horizontal
        return top + (bottom - top) * vertical

    @lru_cache(maxsize=32768)
    def _surface_material(self, x, y):
        regional = self._terrain_noise(x, y, 12)
        local = self._terrain_noise(x, y, 4)
        climate = self._terrain_noise(x + 4096, y - 8192, 30)
        if climate > 0.9:
            return "packed_ice"
        if regional < 0.3 or (regional < 0.4 and local < 0.45):
            return "sandy_soil"
        if regional > 0.84 and local > 0.62:
            return "stone"
        if local < 0.32:
            return "dirt"
        return "grass"

    @lru_cache(maxsize=32768)
    def get_surface_depth(self, x, y):
        regional = self._terrain_noise(x, y, 18)
        local = self._terrain_noise(x, y, 7)
        height = 0.7 * regional + 0.3 * local
        return max(
            2,
            min(
                self.Y_LEVEL_COUNT - 5,
                self.SURFACE_DEPTH + round((height - 0.5) * 6),
            ),
        )

    @staticmethod
    def _tree_positions(x, y, base_depth):
        return (
            (x, y, base_depth, "oak_log"),
            (x, y, base_depth - 1, "oak_log"),
            (x - 1, y, base_depth - 1, "oak_leaves"),
            (x + 1, y, base_depth - 1, "oak_leaves"),
            (x, y - 1, base_depth - 1, "oak_leaves"),
            (x, y + 1, base_depth - 1, "oak_leaves"),
            (x, y, base_depth - 2, "oak_leaves"),
        )

    def _grow_tree_at(self, x, y, depth, sapling=None):
        tree_positions = self._tree_positions(x, y, depth)
        for tree_x, tree_y, tree_depth, _preset_id in tree_positions:
            if not 0 <= tree_depth < self.Y_LEVEL_COUNT:
                return False
            existing = self.get_tile(tree_x, tree_y, tree_depth)
            if existing is not None and not (
                (tree_x, tree_y, tree_depth) == (x, y, depth)
                and existing is sapling
            ):
                return False
        ground = self.get_tile(x, y, depth + 1)
        if not isinstance(ground, block) or ground.preset_id not in (
            "grass", "dirt",
        ):
            return False
        block_presets = self._get_block_presets()
        for tree_x, tree_y, tree_depth, preset_id in tree_positions:
            self.set_tile(
                tree_x,
                tree_y,
                tree_depth,
                block.from_preset(
                    block_presets[preset_id],
                    (tree_x, tree_y, tree_depth),
                ),
            )
        return True

    def grow_saplings(self, current_time=None):
        now = time.monotonic() if current_time is None else current_time
        saplings = [
            tile
            for tile in self.saplings
            if now - tile.planted_at >= 30.0
        ]
        changed = False
        for sapling in saplings:
            x, y, depth = sapling.pos
            if self._grow_tree_at(x, y, depth, sapling):
                changed = True
            else:
                sapling.planted_at = now
        return changed

    def _generate_creature_village(
        self, chunk_layers, chunk_x, chunk_y, block_presets, chunk_entities
    ):
        if (
            (chunk_x, chunk_y) == (0, 0)
            or self.chunk_width < 9
            or self.chunk_height < 9
        ):
            return
        seed = chunk_x * 73856093 ^ chunk_y * 19349663 ^ 0x71A6E
        village_random = random.Random(seed)
        if village_random.random() >= 0.14:
            return

        entity_presets = self._get_entity_presets()
        entity_ids = sorted(entity_presets)
        theme_index = (
            chunk_x * 73856093 ^ chunk_y * 19349663
        ) % len(entity_ids)
        resident_preset = entity_presets[entity_ids[theme_index]]
        is_hostile = resident_preset["disposition"] == "hostile"
        wall_preset_id = "stone" if is_hostile else "oak_planks"
        origin_x = chunk_x * self.chunk_width
        origin_y = chunk_y * self.chunk_height
        center_x = self.chunk_width // 2
        center_y = self.chunk_height // 2
        house_y = center_y - 3
        house_starts = (center_x - 4, center_x + 2)
        house_cells = set()

        for house_x in house_starts:
            for offset_y in range(3):
                for offset_x in range(3):
                    if offset_x == 1 and offset_y == 2:
                        continue
                    local_x = house_x + offset_x
                    local_y = house_y + offset_y
                    global_x = origin_x + local_x
                    global_y = origin_y + local_y
                    depth = self.get_surface_depth(global_x, global_y)
                    current = chunk_layers[depth][local_y][local_x]
                    if current is not None:
                        if (
                            not isinstance(current, block)
                            or current.preset_id not in ("oak_log", "oak_leaves")
                        ):
                            continue
                    preset_id = (
                        "oak_log"
                        if not is_hostile and offset_x in (0, 2)
                        and offset_y in (0, 2)
                        else wall_preset_id
                    )
                    chunk_layers[depth][local_y][local_x] = block.from_preset(
                        block_presets[preset_id],
                        (global_x, global_y, depth),
                    )
                    house_cells.add((local_x, local_y))

        spawn_cells = [
            (local_x, local_y)
            for local_y in range(center_y, self.chunk_height - 1)
            for local_x in range(1, self.chunk_width - 1)
            if (local_x, local_y) not in house_cells
        ]
        village_random.shuffle(spawn_cells)
        population = village_random.randint(3, 5)
        spawned = 0
        for local_x, local_y in spawn_cells:
            global_x = origin_x + local_x
            global_y = origin_y + local_y
            depth = self.get_surface_depth(global_x, global_y)
            if chunk_layers[depth][local_y][local_x] is not None:
                continue
            resident = entity.from_preset(
                resident_preset,
                (global_x, global_y, depth),
            )
            chunk_layers[depth][local_y][local_x] = resident
            chunk_entities.add(resident)
            spawned += 1
            if spawned == population:
                break

    def _generate_chunk(self, chunk_x: int, chunk_y: int):
        block_presets = self._get_block_presets()
        chunk_entities = set()
        origin_x = chunk_x * self.chunk_width
        origin_y = chunk_y * self.chunk_height
        chunk_layers = [
            [
                [None for _ in range(self.chunk_width)]
                for _ in range(self.chunk_height)
            ]
            for _ in range(self.Y_LEVEL_COUNT)
        ]
        for local_y in range(self.chunk_height):
            global_y = origin_y + local_y
            for local_x in range(self.chunk_width):
                global_x = origin_x + local_x
                surface_depth = self.get_surface_depth(global_x, global_y)
                surface_material = self._surface_material(global_x, global_y)
                for depth in range(surface_depth + 1, self.Y_LEVEL_COUNT):
                    preset_id = (
                        surface_material if depth == surface_depth + 1
                        else "dirt" if depth <= surface_depth + 3
                        else "stone"
                    )
                    chunk_layers[depth][local_y][local_x] = (
                        self._create_terrain_block(
                            preset_id, (global_x, global_y, depth)
                        )
                    )

        starter_clearance = {(3, 5), (4, 4), (4, 5), (6, 5)}
        for tree_y in range(origin_y - 1, origin_y + self.chunk_height + 1):
            for tree_x in range(origin_x - 1, origin_x + self.chunk_width + 1):
                if _tree_spawn_roll(tree_x, tree_y) >= 0.035:
                    continue
                tree_depth = self.get_surface_depth(tree_x, tree_y)
                tree_tiles = self._tree_positions(
                    tree_x, tree_y, tree_depth
                )
                if (chunk_x, chunk_y) == (0, 0) and any(
                    (tile_x, tile_y) in starter_clearance
                    for tile_x, tile_y, _tile_depth, _preset_id in tree_tiles
                ):
                    continue
                surface = self._surface_material(tree_x, tree_y)
                if surface not in ("grass", "dirt"):
                    continue
                if any(
                    not (origin_x <= tile_x < origin_x + self.chunk_width)
                    or not (origin_y <= tile_y < origin_y + self.chunk_height)
                    or not 0 <= tile_depth < self.Y_LEVEL_COUNT
                    for tile_x, tile_y, tile_depth, _preset_id in tree_tiles
                ):
                    continue
                if any(
                    chunk_layers[tile_depth][tile_y - origin_y][tile_x - origin_x]
                    is not None
                    for tile_x, tile_y, tile_depth, _preset_id in tree_tiles
                ):
                    continue
                for tree_x_pos, tree_y_pos, tree_depth, preset_id in tree_tiles:
                    chunk_layers[tree_depth][tree_y_pos - origin_y][tree_x_pos - origin_x] = (
                        block.from_preset(
                            block_presets[preset_id],
                            (tree_x_pos, tree_y_pos, tree_depth),
                        )
                    )

        self._generate_creature_village(
            chunk_layers, chunk_x, chunk_y, block_presets, chunk_entities
        )

        if (chunk_x, chunk_y) == (0, 0) and self.chunk_width >= 5 and self.chunk_height >= 2:
            center_x, center_y = self.chunk_width // 2, self.chunk_height // 2
            chest_x, chest_y = center_x - 2, center_y
            table_x, table_y = center_x - 1, center_y
            furnace_x, furnace_y = center_x + 1, center_y
            chunk_layers[self.get_surface_depth(chest_x, chest_y)][center_y][center_x - 2] = storageObject(
                "Chest", "C", "A chest for storing items.",
                (chest_x, chest_y, self.get_surface_depth(chest_x, chest_y)),
                item_id="chest",
                background_color="#573714",
            )
            chunk_layers[self.get_surface_depth(table_x, table_y)][center_y][center_x - 1] = craftingTable(
                "Crafting Table", "T", "A table for crafting blocks.",
                (table_x, table_y, self.get_surface_depth(table_x, table_y)),
                background_color="#54351F",
            )
            chunk_layers[self.get_surface_depth(furnace_x, furnace_y)][center_y][center_x + 1] = furnace(
                "Furnace", "F", "A furnace for smelting blocks.",
                (furnace_x, furnace_y, self.get_surface_depth(furnace_x, furnace_y)),
                background_color="#303944",
            )
        self._generated_chunk_entities[(chunk_x, chunk_y)] = chunk_entities
        return chunk_layers

    def load_chunk(self, chunk_x: int, chunk_y: int):
        chunk_key = (chunk_x, chunk_y)
        if chunk_key not in self.chunks:
            chunk_layers = self._generate_chunk(chunk_x, chunk_y)
            self.chunks[chunk_key] = chunk_layers
            self._index_chunk_objects(chunk_key, chunk_layers)
        self.active_chunk = chunk_key
        self.layers = self.chunks[chunk_key]
        self.map = self.layers[self.SURFACE_DEPTH]
        self.subsurface = self.layers[self.SURFACE_DEPTH + 1:]
        return self.layers

    def _chunk_location(self, x: int, y: int):
        chunk_x, local_x = divmod(x, self.chunk_width)
        chunk_y, local_y = divmod(y, self.chunk_height)
        return chunk_x, chunk_y, local_x, local_y

    @staticmethod
    def is_solid(tile):
        if isinstance(tile, door):
            return not tile.is_open
        return (
            isinstance(tile, block) and tile.solid
        ) or (
            tile is not None and getattr(tile, "placeable", False)
        )

    def resolve_fall(self, entity):
        x, y, depth = entity.pos
        start_depth = depth
        while depth < len(self.layers) - 1:
            below_tile = self.get_tile(x, y, depth + 1)
            if self.is_solid(below_tile):
                break
            depth += 1
        if depth != start_depth:
            entity.move((x, y, depth))
        return depth - start_depth

    def move_player(self, entity, dx, dy):
        if not entity.can_move():
            return "stumbled"
        x, y, depth = entity.pos
        target_x = x + dx
        target_y = y + dy
        target_tile = self.get_tile(target_x, target_y, depth)
        stepped_up = False
        if self.is_solid(target_tile):
            if isinstance(target_tile, door):
                return "blocked"
            if depth == 0 or self.is_solid(
                self.get_tile(target_x, target_y, depth - 1)
            ):
                return "blocked"
            depth -= 1
            stepped_up = True

        entity.move((target_x, target_y, depth))
        fall_distance = self.resolve_fall(entity)
        self.load_chunk(*self._chunk_location(entity.pos[0], entity.pos[1])[:2])
        if fall_distance:
            return "fell"
        return "stepped" if stepped_up else "moved"

    def move_player_y(self, entity, amount):
        x, y, depth = entity.pos
        target_depth = depth + amount
        if not 0 <= target_depth < len(self.layers):
            return False
        if self.is_solid(self.get_tile(x, y, target_depth)):
            return False
        entity.move((x, y, target_depth))
        self.resolve_fall(entity)
        self.load_chunk(*self._chunk_location(x, y)[:2])
        return True

    def get_tile(self, x, y, depth):
        if not (0 <= depth < self.Y_LEVEL_COUNT):
            return None
        chunk_x, chunk_y, local_x, local_y = self._chunk_location(x, y)
        chunk_key = (chunk_x, chunk_y)
        if chunk_key not in self.chunks:
            chunk_layers = self._generate_chunk(chunk_x, chunk_y)
            self.chunks[chunk_key] = chunk_layers
            self._index_chunk_objects(chunk_key, chunk_layers)
        chunk_layers = self.chunks[chunk_key]
        return chunk_layers[depth][local_y][local_x]

    def update_entity_ai(self, player_character, current_time=None):
        now = time.monotonic() if current_time is None else current_time
        player_x, player_y, player_depth = player_character.pos
        player_chunk_x, player_chunk_y, _, _ = self._chunk_location(
            player_x, player_y
        )
        nearby_entities = []
        for (chunk_x, chunk_y), chunk_entities in self.entities_by_chunk.items():
            if (
                abs(chunk_x - player_chunk_x) > 1
                or abs(chunk_y - player_chunk_y) > 1
            ):
                continue
            nearby_entities.extend(chunk_entities)

        changed = False
        for creature in nearby_entities:
            if creature.health <= 0:
                chunk_x, chunk_y, local_x, local_y = self._chunk_location(
                    creature.pos[0], creature.pos[1]
                )
                current_chunk = self.chunks.get((chunk_x, chunk_y))
                if current_chunk is not None and current_chunk[creature.pos[2]][local_y][local_x] is creature:
                    self.set_tile(*creature.pos, None)
                    changed = True
                continue

            x, y, depth = creature.pos
            if depth != player_depth:
                continue
            distance = abs(x - player_x) + abs(y - player_y)

            if creature.disposition == "hostile":
                if distance == 1:
                    if now - creature.last_attack_time >= 1.0:
                        player_character.take_damage(
                            creature.effective_attack_power
                        )
                        creature.last_attack_time = now
                        changed = True
                    continue
                if distance <= 6:
                    step = self._find_entity_path_step(
                        creature, player_character.pos, maximum_distance=6
                    )
                else:
                    continue
            elif distance <= 3:
                step = self._find_entity_flee_step(creature, player_character)
            elif random.random() < 0.08:
                step = self._find_entity_wander_step(creature)
            else:
                continue

            if (
                step is not None
                and creature.can_move()
                and self._move_entity(creature, step)
            ):
                changed = True
        return changed

    def _find_entity_path_step(self, creature, player_pos, maximum_distance):
        start_x, start_y, depth = creature.pos
        player_x, player_y, player_depth = player_pos
        if depth != player_depth:
            return None
        queue = deque([(start_x, start_y, None)])
        visited = {(start_x, start_y)}
        while queue:
            x, y, first_step = queue.popleft()
            if abs(x - player_x) + abs(y - player_y) == 1:
                return first_step
            for next_x, next_y in (
                (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)
            ):
                point = (next_x, next_y)
                if (
                    point in visited
                    or abs(next_x - start_x) + abs(next_y - start_y)
                    > maximum_distance
                    or (next_x, next_y) == (player_x, player_y)
                ):
                    continue
                tile = self.get_tile(next_x, next_y, depth)
                if tile is not None:
                    continue
                visited.add(point)
                queue.append((
                    next_x, next_y,
                    (next_x, next_y, depth)
                    if first_step is None else first_step,
                ))
        return None

    def _find_entity_flee_step(self, creature, player_character):
        x, y, depth = creature.pos
        player_x, player_y, _ = player_character.pos
        options = [
            (next_x, next_y, depth)
            for next_x, next_y in (
                (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)
            )
            if self.get_tile(next_x, next_y, depth) is None
            and (next_x, next_y) != (player_x, player_y)
        ]
        if not options:
            return None
        return max(
            options,
            key=lambda point: abs(point[0] - player_x) + abs(point[1] - player_y),
        )

    def _find_entity_wander_step(self, creature):
        x, y, depth = creature.pos
        options = [
            (next_x, next_y, depth)
            for next_x, next_y in (
                (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)
            )
            if self.get_tile(next_x, next_y, depth) is None
        ]
        return random.choice(options) if options else None

    def _move_entity(self, creature, destination):
        old_x, old_y, old_depth = creature.pos
        next_x, next_y, next_depth = destination
        if (
            self.get_tile(next_x, next_y, next_depth) is not None
            or self.get_tile(old_x, old_y, old_depth) is not creature
        ):
            return False
        self.set_tile(old_x, old_y, old_depth, None)
        creature.move(destination)
        self.set_tile(next_x, next_y, next_depth, creature)
        return True

    def spawn_entity(self, preset, near_pos):
        x, y, depth = near_pos
        candidates = sorted(
            (
                (candidate_x, candidate_y)
                for distance in (1, 2)
                for candidate_x in range(x - distance, x + distance + 1)
                for candidate_y in range(y - distance, y + distance + 1)
                if abs(candidate_x - x) + abs(candidate_y - y) == distance
            ),
            key=lambda position: (
                abs(position[0] - x) + abs(position[1] - y),
                position[1],
                position[0],
            ),
        )
        for candidate_x, candidate_y in candidates:
            if self.get_tile(candidate_x, candidate_y, depth) is not None:
                continue
            creature = entity.from_preset(
                preset, (candidate_x, candidate_y, depth)
            )
            self.set_tile(candidate_x, candidate_y, depth, creature)
            return creature
        return None

    def get_visible_tiles(self, x, y, depth):
        return [
            (visible_depth, tile)
            for visible_depth in range(
                depth,
                min(self.Y_LEVEL_COUNT, depth + self.VISIBLE_DEPTHS_BELOW + 1),
            )
            if (tile := self.get_tile(x, y, visible_depth)) is not None
        ]

    def get_visible_tile(self, x, y, depth):
        visible_end = min(
            self.Y_LEVEL_COUNT,
            depth + self.VISIBLE_DEPTHS_BELOW + 1,
        )
        for visible_depth in range(depth, visible_end):
            tile = self.get_tile(x, y, visible_depth)
            if tile is not None:
                return visible_depth, tile
        return None

    def get_placement_depth(self, x, y, player_pos):
        player_x, player_y, player_depth = player_pos
        below_depth = player_depth + 1
        if (
            below_depth < self.Y_LEVEL_COUNT
            and self.get_tile(x, y, below_depth) is None
        ):
            return below_depth
        if (
            (x, y) != (player_x, player_y)
            and self.get_tile(x, y, player_depth) is None
        ):
            return player_depth
        return None

    def can_mine_target(self, x, y, depth, player_pos, max_vertical_distance=4):
        player_x, player_y, player_depth = player_pos
        if (
            not 0 <= depth < self.Y_LEVEL_COUNT
            or abs(depth - player_depth) > max_vertical_distance
        ):
            return False
        target = self.get_tile(x, y, depth)
        if target is None or (
            isinstance(target, block) and not target.breakable
        ):
            return False

        queue = deque([(player_x, player_y, player_depth)])
        visited = {(player_x, player_y, player_depth)}
        while queue:
            current_x, current_y, current_depth = queue.popleft()
            if (
                abs(current_x - x) + abs(current_y - y)
                + abs(current_depth - depth) == 1
            ):
                return True
            for next_x, next_y, next_depth in (
                (current_x + 1, current_y, current_depth),
                (current_x - 1, current_y, current_depth),
                (current_x, current_y + 1, current_depth),
                (current_x, current_y - 1, current_depth),
                (current_x, current_y, current_depth + 1),
                (current_x, current_y, current_depth - 1),
            ):
                next_pos = (next_x, next_y, next_depth)
                if next_pos in visited:
                    continue
                if (
                    not 0 <= next_depth < self.Y_LEVEL_COUNT
                    or abs(next_depth - player_depth) > max_vertical_distance
                    or (next_x - player_x) ** 2 + (next_y - player_y) ** 2 > 16
                    or self.is_solid(self.get_tile(next_x, next_y, next_depth))
                ):
                    continue
                visited.add(next_pos)
                queue.append(next_pos)
        return False

    def get_mineable_targets(
        self, x, y, camera_depth, player_pos, max_vertical_distance=4
    ):
        visible_end = min(
            self.Y_LEVEL_COUNT,
            camera_depth + self.VISIBLE_DEPTHS_BELOW + 1,
        )
        return [
            (depth, target)
            for depth in range(max(0, camera_depth), visible_end)
            if abs(depth - player_pos[2]) <= max_vertical_distance
            and self.can_mine_target(
                x, y, depth, player_pos, max_vertical_distance
            )
            and (target := self.get_tile(x, y, depth)) is not None
        ]

    def set_tile(self, x, y, depth, tile):
        if not (0 <= depth < self.Y_LEVEL_COUNT):
            raise IndexError("Layer is outside the generated world.")
        chunk_x, chunk_y, local_x, local_y = self._chunk_location(x, y)
        chunk_key = (chunk_x, chunk_y)
        if chunk_key not in self.chunks:
            chunk_layers = self._generate_chunk(chunk_x, chunk_y)
            self.chunks[chunk_key] = chunk_layers
            self._index_chunk_objects(chunk_key, chunk_layers)
        chunk_layers = self.chunks[chunk_key]
        old_tile = chunk_layers[depth][local_y][local_x]
        self._track_tile_change(chunk_key, old_tile, tile)
        chunk_layers[depth][local_y][local_x] = tile

    def create_block(self, preset_id, pos, presets=None):
        presets = self._get_block_presets() if presets is None else presets
        if preset_id not in presets:
            raise KeyError(f"Unknown block preset: {preset_id}")
        return block.from_preset(presets[preset_id], pos)

    def create_crafting_result(self, result):
        preset_id = result["id"] if isinstance(result, dict) else result
        return self.create_crafted_item(preset_id)

    def create_crafted_item(
        self, item_id, pos=(0, 0, 0), item_presets=None, block_presets=None
    ):
        item_presets = (
            self._get_item_presets() if item_presets is None else item_presets
        )
        item_preset = item_presets.get(item_id)
        if item_preset is None:
            block_presets = (
                self._get_block_presets()
                if block_presets is None else block_presets
            )
            if item_id not in block_presets:
                raise KeyError(f"Unknown crafted item preset: {item_id}")
            return block.from_preset(block_presets[item_id], pos)
        if item_preset.get("kind") == "block":
            block_presets = (
                self._get_block_presets()
                if block_presets is None else block_presets
            )
            if item_id not in block_presets:
                raise ValueError(
                    f"Crafted block '{item_id}' has no matching block preset."
                )
            crafted_block = block.from_preset(block_presets[item_id], pos)
            crafted_block.name = item_preset["name"]
            crafted_block.icon = item_preset["icon"]
            crafted_block.description = item_preset["description"]
            return crafted_block
        kind = item_preset.get("kind", "item")
        if kind == "tool":
            return toolItem(
                item_id, item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_preset["tool_type"],
                item_preset["durability"], item_preset.get("drop_bonus", 1),
                item_preset.get("mining_speed", 2.0),
                item_preset["foreground_color"],
            )
        if kind == "usable":
            return usableItem(
                item_id, item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_preset["heal_amount"],
                item_preset["foreground_color"],
            )
        if kind == "storage":
            return storageObject(
                item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_id=item_id,
                foreground_color=item_preset["foreground_color"],
                background_color=item_preset.get(
                    "background_color", "#573714"
                ),
            )
        if kind == "crafting_station":
            return craftingTable(
                item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_id,
                item_preset["foreground_color"],
                item_preset.get("background_color", "#54351F"),
            )
        if kind == "furnace":
            return furnace(
                item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_id,
                item_preset["foreground_color"],
                item_preset.get("background_color", "#303944"),
            )
        if kind == "campfire":
            return campfire(
                item_preset["name"], item_preset["icon"],
                item_preset["description"], pos,
                item_preset["foreground_color"],
                item_preset.get("background_color", "#632719"),
            )
        if kind == "resting_place":
            return restingPlace(
                item_preset["name"], item_preset["icon"],
                item_preset["description"], pos, item_id,
                item_preset["foreground_color"],
                item_preset.get("background_color", "#273954"),
            )
        if kind == "door":
            return self.create_block(item_id, pos, block_presets)
        return craftedItem.from_preset(item_preset, pos)

    def break_block(self, x, y, depth, tool=None):
        target = self.get_tile(x, y, depth)
        if (
            target is not None
            and not isinstance(target, block)
            and getattr(target, "placeable", False)
        ):
            self.set_tile(x, y, depth, None)
            return target
        if not isinstance(target, block):
            return None
        if not target.breakable:
            raise ValueError(f"{target.name} cannot be broken.")
        self.set_tile(x, y, depth, None)
        if (
            target.tool_type
            and isinstance(tool, toolItem)
            and tool.tool_type == target.tool_type
        ):
            tool.durability -= 1
        return target

    @staticmethod
    def get_break_duration(target, tool=None):
        duration = max(0.1, getattr(target, "break_time", 1.0))
        if isinstance(tool, toolItem):
            speed = tool.mining_speed
            target_tool = getattr(target, "tool_type", None)
            if target_tool and tool.tool_type != target_tool:
                speed *= 0.5
            duration /= max(1.0, speed)
        return duration

    def place_block(self, preset_id, x, y, depth):
        if not (0 <= depth < len(self.layers)):
            return False
        if not (0 <= y < len(self.layers[depth]) and 0 <= x < len(self.layers[depth][y])):
            return False
        if self.get_tile(x, y, depth) is not None:
            return False
        self.set_tile(x, y, depth, self.create_block(preset_id, (x, y, depth)))
        return True

    def generate_map(self, width: int, height: int):
        self.generate_world(width, height)

    def display_map(self):
        for row in self.map:
            print(" ".join(tile.icon if tile is not None else " " for tile in row))


def is_cold_location(game_world, pos):
    x, y, _depth = pos
    surface_depth = game_world.get_surface_depth(x, y)
    return any(
        isinstance(tile, block) and tile.preset_id in COLD_BLOCK_IDS
        for tile in (
            game_world.get_tile(x, y, surface_depth),
            game_world.get_tile(x, y, surface_depth + 1),
        )
    )


def update_spoilage_in_collection(
    collection, game_world, cold=False, location=None,
):
    location_is_cold = cold or (
        location is not None and is_cold_location(game_world, location)
    )
    for item in collection:
        if isinstance(item, bodyPartItem):
            item.update_spoilage(location_is_cold)
        elif isinstance(item, storageObject):
            storage_is_cold = (
                location_is_cold
                or item.item_id == "icebox"
                or (
                    location is not None
                    and is_cold_location(game_world, location)
                )
            )
            update_spoilage_in_collection(
                item.contents, game_world, storage_is_cold, item.pos
            )


def serialize_game_object(game_object):
    pos = list(game_object.pos)
    serialized = {"pos": pos, "count": getattr(game_object, "count", 1)}
    if isinstance(game_object, bodyPartItem):
        serialized.update(
            type="body_part",
            part_id=game_object.part_id,
            health=game_object.health,
            spoil_remaining=game_object.spoil_remaining,
            last_updated=game_object.last_updated,
            spoiled=game_object.spoiled,
            infected=game_object.infected,
        )
    elif isinstance(game_object, block):
        serialized.update(type="block", id=game_object.preset_id)
        if isinstance(game_object, door):
            serialized["is_open"] = game_object.is_open
        if game_object.grows_into_tree:
            serialized["growth_elapsed"] = max(
                0.0, time.monotonic() - game_object.planted_at
            )
    elif isinstance(game_object, entity):
        preset_id = getattr(game_object, "preset_id", None)
        if preset_id is None:
            raise ValueError(f"Cannot save entity without a preset id: {game_object.name}")
        serialized.update(
            type="entity",
            id=preset_id,
            health=game_object.health,
            limb_health=game_object.limb_health,
            attached_parts=sorted(game_object.attached_parts),
            infected=game_object.infected,
            infection_elapsed=game_object.infection_elapsed,
        )
    elif isinstance(game_object, storageObject):
        serialized.update(
            type="storage",
            id=game_object.item_id,
            contents=[
                serialize_game_object(item) for item in game_object.contents
            ],
        )
    elif isinstance(game_object, craftedItem):
        serialized.update(type="item", id=game_object.item_id)
        if isinstance(game_object, toolItem):
            serialized["durability"] = game_object.durability
    else:
        item_id = getattr(game_object, "item_id", None)
        if item_id is None:
            raise ValueError(f"Cannot save unsupported object: {game_object.name}")
        serialized.update(type="item", id=item_id)
    return serialized


CLAN_RANKS = ("member", "officer", "leader")
CLAN_RELATIONS = ("neutral", "allied", "rival")
CLAN_STARTING_DATA = {
    "oakwardens": {
        "name": "Oakwardens",
        "motto": "Keep the old forests standing.",
        "treasury": 250,
        "members": {
            "Elder Rowan": "leader",
            "Mira Leafstep": "officer",
            "Bramble": "member",
            "Willow": "member",
        },
    },
    "stonepact": {
        "name": "Stone Pact",
        "motto": "Build together; stand unbroken.",
        "treasury": 300,
        "members": {
            "Thane Flint": "leader",
            "Nora Anvil": "officer",
            "Mossbeard": "member",
            "Pebble": "member",
        },
    },
    "mosscircle": {
        "name": "Moss Circle",
        "motto": "Trade knowledge, not territory.",
        "treasury": 200,
        "members": {
            "Sage Caps": "leader",
            "Fern": "officer",
            "Pip": "member",
            "Root": "member",
        },
    },
    "nightveil": {
        "name": "Nightveil",
        "motto": "No path is closed after dark.",
        "treasury": 350,
        "members": {
            "Warden Dusk": "leader",
            "Ash": "officer",
            "Shade": "member",
            "Whisper": "member",
        },
    },
}


class clanPolitics:
    INITIAL_COINS = 100
    FOUNDING_COST = 50

    def __init__(self):
        self.wallet = self.INITIAL_COINS
        self.membership = None
        self.clans = {
            clan_id: {
                **definition,
                "members": dict(definition["members"]),
                "relations": {},
            }
            for clan_id, definition in CLAN_STARTING_DATA.items()
        }
        self._reset_relations()

    def _reset_relations(self):
        clan_ids = tuple(self.clans)
        for clan_id, clan in self.clans.items():
            clan["relations"] = {
                other_id: "neutral"
                for other_id in clan_ids if other_id != clan_id
            }

    @staticmethod
    def _validate_amount(amount):
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("Enter a positive whole number of coins.")

    def reset(self):
        self.__init__()

    def join(self, clan_id):
        if clan_id not in self.clans:
            raise ValueError("That clan no longer exists.")
        if self.membership is not None:
            raise ValueError("Leave your current clan before joining another.")
        self.clans[clan_id]["members"]["You"] = "member"
        self.membership = clan_id

    def leave(self):
        if self.membership is None:
            raise ValueError("You are not in a clan.")
        clan = self.clans[self.membership]
        rank = clan["members"].pop("You")
        if rank == "leader" and clan["members"]:
            successor = next(
                (
                    member for member, member_rank in clan["members"].items()
                    if member_rank == "officer"
                ),
                next(iter(clan["members"])),
            )
            clan["members"][successor] = "leader"
        self.membership = None

    def create_clan(self, name):
        clean_name = " ".join(name.split())
        if len(clean_name) < 3 or len(clean_name) > 32:
            raise ValueError("Clan names must be between 3 and 32 characters.")
        if self.membership is not None:
            raise ValueError("Leave your current clan before founding one.")
        if self.wallet < self.FOUNDING_COST:
            raise ValueError(
                f"Founding a clan costs {self.FOUNDING_COST} coins."
            )
        clan_id = re.sub(r"[^a-z0-9]+", "", clean_name.casefold())
        if not clan_id or clan_id in self.clans:
            raise ValueError("That clan name is already in use.")
        self.wallet -= self.FOUNDING_COST
        self.clans[clan_id] = {
            "name": clean_name,
            "motto": "A new banner rises.",
            "treasury": 0,
            "members": {"You": "leader"},
            "relations": {
                other_id: "neutral" for other_id in self.clans
            },
        }
        for other_id in self.clans:
            if other_id != clan_id:
                self.clans[other_id]["relations"][clan_id] = "neutral"
        self.membership = clan_id

    def deposit(self, amount):
        self._validate_amount(amount)
        if self.membership is None:
            raise ValueError("Join a clan before using its treasury.")
        if self.wallet < amount:
            raise ValueError("You do not have enough coins to deposit that amount.")
        self.wallet -= amount
        self.clans[self.membership]["treasury"] += amount

    def withdraw(self, amount):
        self._validate_amount(amount)
        if self.membership is None:
            raise ValueError("Join a clan before using its treasury.")
        clan = self.clans[self.membership]
        if clan["members"].get("You") not in ("officer", "leader"):
            raise ValueError("Only clan officers and leaders can withdraw funds.")
        if clan["treasury"] < amount:
            raise ValueError("The clan treasury does not have enough coins.")
        clan["treasury"] -= amount
        self.wallet += amount

    def change_rank(self, member_name, direction):
        if self.membership is None:
            raise ValueError("Join a clan before managing its ranks.")
        clan = self.clans[self.membership]
        if clan["members"].get("You") != "leader":
            raise ValueError("Only the clan leader can change ranks.")
        if member_name == "You":
            raise ValueError("You cannot change your own rank directly.")
        current_rank = clan["members"].get(member_name)
        if current_rank is None:
            raise ValueError("That member is no longer in your clan.")
        rank_index = CLAN_RANKS.index(current_rank)
        next_index = max(0, min(len(CLAN_RANKS) - 1, rank_index + direction))
        if next_index == rank_index:
            raise ValueError("That member is already at the highest/lowest rank.")
        next_rank = CLAN_RANKS[next_index]
        if next_rank == "leader":
            clan["members"]["You"] = "officer"
        clan["members"][member_name] = next_rank

    def seek_promotion(self):
        if self.membership is None:
            raise ValueError("Join a clan before seeking a promotion.")
        clan = self.clans[self.membership]
        current_rank = clan["members"]["You"]
        if current_rank == "leader":
            raise ValueError("You already lead this clan.")
        cost = 50 if current_rank == "member" else 100
        if self.wallet < cost:
            raise ValueError(
                f"Promotion to the next rank requires {cost} coins."
            )
        self.wallet -= cost
        clan["treasury"] += cost
        if current_rank == "member":
            clan["members"]["You"] = "officer"
        else:
            leader = next(
                (
                    member for member, rank in clan["members"].items()
                    if rank == "leader"
                ),
                None,
            )
            if leader is not None:
                clan["members"][leader] = "officer"
            clan["members"]["You"] = "leader"

    def set_relation(self, other_clan_id, relation):
        if self.membership is None:
            raise ValueError("Join a clan before conducting diplomacy.")
        if other_clan_id not in self.clans or other_clan_id == self.membership:
            raise ValueError("Choose a different existing clan.")
        if relation not in CLAN_RELATIONS:
            raise ValueError("Choose neutral, allied, or rival.")
        clan = self.clans[self.membership]
        current_relation = clan["relations"][other_clan_id]
        if current_relation == relation:
            raise ValueError(f"Relations are already {relation}.")
        cost = {"neutral": 0, "allied": 25, "rival": 10}[relation]
        if clan["treasury"] < cost:
            raise ValueError(
                f"Changing relations to {relation} costs {cost} treasury coins."
            )
        clan["treasury"] -= cost
        clan["relations"][other_clan_id] = relation
        self.clans[other_clan_id]["relations"][self.membership] = relation

    def to_data(self):
        return {
            "wallet": self.wallet,
            "membership": self.membership,
            "clans": self.clans,
        }

    def load_data(self, data):
        if not isinstance(data, dict):
            raise ValueError("Saved clan politics must be an object.")
        wallet = data.get("wallet")
        if not isinstance(wallet, int) or wallet < 0:
            raise ValueError("Saved coin balance must be a non-negative integer.")
        clans_data = data.get("clans")
        if not isinstance(clans_data, dict) or not clans_data:
            raise ValueError("Saved clan list is invalid.")
        clans = {}
        for clan_id, saved_clan in clans_data.items():
            if (
                not isinstance(clan_id, str)
                or not clan_id
                or not isinstance(saved_clan, dict)
            ):
                raise ValueError("Saved clan entry is invalid.")
            name, motto = saved_clan.get("name"), saved_clan.get("motto")
            treasury = saved_clan.get("treasury")
            members, relations = (
                saved_clan.get("members"), saved_clan.get("relations")
            )
            if (
                not isinstance(name, str) or not name
                or not isinstance(motto, str)
                or not isinstance(treasury, int) or treasury < 0
                or not isinstance(members, dict)
                or not isinstance(relations, dict)
            ):
                raise ValueError(f"Saved clan '{clan_id}' has invalid data.")
            if any(
                not isinstance(member, str)
                or not member
                or rank not in CLAN_RANKS
                for member, rank in members.items()
            ):
                raise ValueError(f"Saved clan '{name}' has invalid member ranks.")
            leaders = sum(rank == "leader" for rank in members.values())
            if members and leaders != 1:
                raise ValueError(f"Saved clan '{name}' must have one leader.")
            clans[clan_id] = {
                "name": name,
                "motto": motto,
                "treasury": treasury,
                "members": dict(members),
                "relations": dict(relations),
            }
        for clan_id, clan in clans.items():
            if set(clan["relations"]) != set(clans) - {clan_id}:
                raise ValueError(f"Saved relations for '{clan['name']}' are invalid.")
            if any(relation not in CLAN_RELATIONS for relation in clan["relations"].values()):
                raise ValueError(f"Saved relations for '{clan['name']}' are invalid.")
            for other_id, relation in clan["relations"].items():
                if clans[other_id]["relations"].get(clan_id) != relation:
                    raise ValueError("Saved clan relations are not reciprocal.")
        membership = data.get("membership")
        if membership is not None and (
            membership not in clans or "You" not in clans[membership]["members"]
        ):
            raise ValueError("Saved clan membership is invalid.")
        if sum("You" in clan["members"] for clan in clans.values()) != (
            1 if membership is not None else 0
        ):
            raise ValueError("Saved player clan membership is inconsistent.")
        self.wallet = wallet
        self.membership = membership
        self.clans = clans


def deserialize_game_object(
    serialized, game_world, block_presets=None, item_presets=None,
    entity_presets=None,
):
    if not isinstance(serialized, dict):
        raise ValueError("Save contains an invalid object entry.")
    if block_presets is None:
        block_presets = load_block_presets()
    if item_presets is None:
        item_presets = load_item_presets()
    if entity_presets is None:
        entity_presets = load_entity_presets()
    pos = tuple(serialized["pos"])
    object_type = serialized["type"]
    preset_id = serialized.get("id")
    if object_type == "block":
        restored = game_world.create_block(preset_id, pos, block_presets)
        if isinstance(restored, door):
            restored.is_open = serialized.get("is_open", False)
        if restored.grows_into_tree:
            restored.planted_at = (
                time.monotonic() - serialized.get("growth_elapsed", 0.0)
            )
    elif object_type == "body_part":
        part_id = serialized.get("part_id")
        part_names = dict(BODY_PARTS)
        health = serialized.get("health")
        spoil_remaining = serialized.get("spoil_remaining")
        last_updated = serialized.get("last_updated")
        spoiled = serialized.get("spoiled", False)
        infected = serialized.get("infected", False)
        if (
            not isinstance(part_id, str)
            or part_id not in part_names
            or isinstance(health, bool)
            or not isinstance(health, int)
            or not 0 <= health <= 100
            or isinstance(spoil_remaining, bool)
            or not isinstance(spoil_remaining, (int, float))
            or not 0 <= spoil_remaining <= PART_SPOIL_TIME
            or isinstance(last_updated, bool)
            or not isinstance(last_updated, (int, float))
            or not -1_000_000_000_000 <= last_updated <= 1_000_000_000_000
            or not isinstance(spoiled, bool)
            or not isinstance(infected, bool)
        ):
            raise ValueError("Save contains an invalid detached body part.")
        restored = bodyPartItem(
            part_id,
            part_names[part_id],
            health,
            spoil_remaining,
            last_updated,
            spoiled,
            infected,
        )
    elif object_type == "entity":
        if preset_id not in entity_presets:
            raise ValueError(f"Save refers to unknown entity preset '{preset_id}'.")
        restored = entity.from_preset(entity_presets[preset_id], pos)
        restored.hp = serialized["health"]
        if "limb_health" in serialized:
            restored.restore_limb_health(serialized["limb_health"])
        restored.restore_body_state(
            serialized.get("attached_parts"),
            serialized.get("infected", False),
            serialized.get("infection_elapsed", 0.0),
        )
    elif object_type in ("item", "storage"):
        restored = game_world.create_crafted_item(
            preset_id, pos, item_presets, block_presets
        )
        if object_type == "storage":
            if not isinstance(restored, storageObject):
                raise ValueError(f"Save item '{preset_id}' is not a storage object.")
            if not isinstance(serialized["contents"], list):
                raise ValueError(f"Save storage '{preset_id}' has invalid contents.")
            restored.contents = [
                deserialize_game_object(
                    item,
                    game_world,
                    block_presets,
                    item_presets,
                    entity_presets,
                )
                for item in serialized["contents"]
            ]
        if isinstance(restored, toolItem):
            restored.durability = serialized["durability"]
    else:
        raise ValueError(f"Save contains unsupported object type '{object_type}'.")
    if getattr(restored, "stackable", False):
        restored.count = serialized.get("count", 1)
    return restored


def save_game_data(
    save_path, game_world, player_character, selected_layer,
    hotbar_items=None, selected_hotbar_slot=0, clan_politics=None,
):
    update_spoilage_in_collection(
        player_character.inventory,
        game_world,
        location=player_character.pos,
    )
    for layers in game_world.chunks.values():
        for layer in layers:
            for row in layer:
                for tile in row:
                    if isinstance(tile, (bodyPartItem, storageObject)):
                        update_spoilage_in_collection(
                            [tile], game_world, location=tile.pos
                        )
    chunks = []
    for (chunk_x, chunk_y), layers in sorted(game_world.chunks.items()):
        chunks.append({
            "x": chunk_x,
            "y": chunk_y,
            "layers": [
                [
                    [
                        serialize_game_object(tile) if tile is not None else None
                        for tile in row
                    ]
                    for row in layer
                ]
                for layer in layers
            ],
        })
    data = {
        "version": SAVE_VERSION,
        "name": save_path.stem.replace("_", " "),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "chunk_width": game_world.chunk_width,
        "chunk_height": game_world.chunk_height,
        "chunks": chunks,
        "clan_politics": (
            clan_politics.to_data()
            if clan_politics is not None
            else clanPolitics().to_data()
        ),
        "player": {
            "pos": list(player_character.pos),
            "health": player_character.health,
            "limb_health": player_character.limb_health,
            "attached_parts": sorted(player_character.attached_parts),
            "infected": player_character.infected,
            "infection_elapsed": player_character.infection_elapsed,
            "inventory": [
                serialize_game_object(item)
                for item in player_character.inventory
            ],
            "selected_layer": selected_layer,
            "hotbar": [
                player_character.inventory.index(item)
                if item in player_character.inventory else None
                for item in (hotbar_items or [])
            ],
            "selected_hotbar_slot": selected_hotbar_slot,
        },
    }
    save_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = save_path.with_suffix(".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as save_file:
            json.dump(data, save_file, separators=(",", ":"))
        temporary_path.replace(save_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_game_data(save_path, game_world):
    with save_path.open(encoding="utf-8") as save_file:
        data = json.load(save_file)
    if not isinstance(data, dict):
        raise ValueError(f"{save_path.name} does not contain a save object.")
    if data.get("version") != SAVE_VERSION:
        raise ValueError(f"Unsupported save version in {save_path.name}.")
    politics = clanPolitics()
    politics.load_data(data.get("clan_politics", politics.to_data()))
    width = data.get("chunk_width")
    height = data.get("chunk_height")
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ValueError(f"{save_path.name} has invalid chunk dimensions.")
    loaded_chunks = {}
    chunks_data = data.get("chunks")
    if not isinstance(chunks_data, list):
        raise ValueError(f"{save_path.name} has invalid chunk data.")
    block_presets = load_block_presets()
    item_presets = load_item_presets()
    entity_presets = load_entity_presets()
    for chunk_data in chunks_data:
        if not isinstance(chunk_data, dict):
            raise ValueError(f"{save_path.name} has an invalid chunk entry.")
        chunk_x = chunk_data["x"]
        chunk_y = chunk_data["y"]
        if not isinstance(chunk_x, int) or not isinstance(chunk_y, int):
            raise ValueError(f"{save_path.name} has invalid chunk coordinates.")
        layers_data = chunk_data["layers"]
        if not isinstance(layers_data, list):
            raise ValueError(f"{save_path.name} has invalid chunk layers.")
        if len(layers_data) != world.Y_LEVEL_COUNT:
            raise ValueError(f"{save_path.name} has an invalid Y-level count.")
        layers = []
        for depth, layer_data in enumerate(layers_data):
            if not isinstance(layer_data, list):
                raise ValueError(
                    f"{save_path.name} has an invalid chunk layer."
                )
            if len(layer_data) != height:
                raise ValueError(f"{save_path.name} has invalid chunk height.")
            layer = []
            for local_y, row_data in enumerate(layer_data):
                if not isinstance(row_data, list):
                    raise ValueError(f"{save_path.name} has an invalid chunk row.")
                if len(row_data) != width:
                    raise ValueError(f"{save_path.name} has invalid chunk width.")
                row = []
                for local_x, tile_data in enumerate(row_data):
                    if tile_data is None:
                        row.append(None)
                    else:
                        global_x = chunk_x * width + local_x
                        global_y = chunk_y * height + local_y
                        row.append(
                            deserialize_game_object(
                                tile_data,
                                game_world,
                                block_presets,
                                item_presets,
                                entity_presets,
                            )
                        )
                layer.append(row)
            layers.append(layer)
        loaded_chunks[(chunk_x, chunk_y)] = layers
    if not loaded_chunks:
        raise ValueError(f"{save_path.name} contains no world chunks.")

    player_data = data.get("player")
    if not isinstance(player_data, dict):
        raise ValueError(f"{save_path.name} has invalid player data.")
    player_pos = tuple(player_data["pos"])
    if (
        len(player_pos) != 3
        or any(not isinstance(coordinate, int) for coordinate in player_pos)
        or not 0 <= player_pos[2] < world.Y_LEVEL_COUNT
    ):
        raise ValueError(f"{save_path.name} has an invalid player position.")
    health = player_data["health"]
    if not isinstance(health, int) or not 0 <= health <= 100:
        raise ValueError(f"{save_path.name} has invalid player health.")
    limb_health = player_data.get("limb_health", {})
    if not isinstance(limb_health, dict) or any(
        part_id not in {key for key, _label in BODY_PARTS}
        or not isinstance(value, int)
        or not 0 <= value <= 100
        for part_id, value in limb_health.items()
    ):
        raise ValueError(f"{save_path.name} has invalid player limb health.")
    attached_parts = player_data.get("attached_parts")
    if attached_parts is not None and (
        not isinstance(attached_parts, list)
        or any(
            not isinstance(part_id, str)
            or part_id not in {key for key, _label in BODY_PARTS}
            for part_id in attached_parts
        )
        or len(set(attached_parts)) != len(attached_parts)
    ):
        raise ValueError(f"{save_path.name} has invalid attached body parts.")
    infected = player_data.get("infected", False)
    infection_elapsed = player_data.get("infection_elapsed", 0.0)
    if (
        not isinstance(infected, bool)
        or isinstance(infection_elapsed, bool)
        or not isinstance(infection_elapsed, (int, float))
        or not 0 <= infection_elapsed < INFECTION_DAMAGE_INTERVAL
    ):
        raise ValueError(f"{save_path.name} has invalid infection data.")
    selected_layer = player_data["selected_layer"]
    if (
        not isinstance(selected_layer, int)
        or not 0 <= selected_layer < world.Y_LEVEL_COUNT
    ):
        raise ValueError(f"{save_path.name} has an invalid camera layer.")
    if not isinstance(player_data["inventory"], list):
        raise ValueError(f"{save_path.name} has invalid player inventory.")
    inventory = [
        deserialize_game_object(
            item,
            game_world,
            block_presets,
            item_presets,
            entity_presets,
        )
        for item in player_data["inventory"]
    ]
    hotbar = player_data.get("hotbar", [])
    if any(
        index is not None
        and (not isinstance(index, int) or not 0 <= index < len(inventory))
        for index in hotbar
    ):
        raise ValueError(f"{save_path.name} has an invalid hotbar.")
    selected_hotbar_slot = player_data.get("selected_hotbar_slot", 0)
    if not isinstance(selected_hotbar_slot, int) or not 0 <= selected_hotbar_slot < 5:
        raise ValueError(f"{save_path.name} has an invalid selected hotbar slot.")
    active_chunk_key = (player_pos[0] // width, player_pos[1] // height)
    active_layers = loaded_chunks.get(active_chunk_key)
    if active_layers is None:
        raise ValueError(f"{save_path.name} does not include the player's chunk.")
    game_world.chunk_width = width
    game_world.chunk_height = height
    game_world.chunks = loaded_chunks
    game_world.rebuild_object_indexes()
    game_world.active_chunk = active_chunk_key
    game_world.layers = active_layers
    game_world.map = active_layers[world.SURFACE_DEPTH]
    game_world.subsurface = active_layers[world.SURFACE_DEPTH + 1:]
    return {
        "pos": player_pos,
        "health": health,
        "limb_health": limb_health,
        "attached_parts": attached_parts,
        "infected": infected,
        "infection_elapsed": infection_elapsed,
        "inventory": inventory,
        "selected_layer": selected_layer,
        "hotbar": [
            inventory[index] if index is not None else None
            for index in hotbar
        ],
        "selected_hotbar_slot": selected_hotbar_slot,
        "clan_politics": politics.to_data(),
    }


def save_game_paths():
    if not SAVE_GAMES_DIR.exists():
        return []
    return sorted(
        SAVE_GAMES_DIR.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def save_path_for_name(name):
    safe_name = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "_")
    if not safe_name:
        raise ValueError("Enter a save name using letters, numbers, spaces, '_' or '-'.")
    return SAVE_GAMES_DIR / f"{safe_name}.json"


def main():
    game_world = world()
    politics = clanPolitics()
    width, height = 10, 10
    zoom_levels = (16, 20, 24, 28, 34, 42, 52, 64)
    zoom_index = zoom_levels.index(34)
    cell_size = zoom_levels[zoom_index]
    viewport_pixels = 340
    viewport_width = viewport_pixels // cell_size
    viewport_height = viewport_pixels // cell_size
    game_world.generate_world(width, height)
    crafting_recipes = load_crafting_recipes()
    player_x, player_y = width // 2 - 1, height // 2 - 1
    player_surface_depth = game_world.get_surface_depth(player_x, player_y)
    player_character = player(
        "Player", "@", "The adventurer.",
        (player_x, player_y, player_surface_depth), 100, 10, []
    )
    selected_layer = player_surface_depth
    game_active = False
    entity_update_job = None
    last_entity_update = time.monotonic()
    task_queue = []
    active_task = None
    task_started = False
    task_runner_job = None

    root = tk.Tk()
    root.title("Goblin Fortress")
    root.resizable(True, True)
    root.attributes("-fullscreen", False)

    def toggle_fullscreen(_event=None):
        is_fullscreen = bool(root.attributes("-fullscreen"))
        root.attributes("-fullscreen", not is_fullscreen)
        return "break"

    def leave_fullscreen(_event=None):
        if root.attributes("-fullscreen"):
            root.attributes("-fullscreen", False)
            return "break"
        return None

    root.bind("<F11>", toggle_fullscreen)
    root.bind("<Escape>", leave_fullscreen)

    menu_frame = tk.Frame(root, padx=48, pady=36, background="#202B23")
    menu_frame.pack(fill="both", expand=True)
    tk.Label(
        menu_frame,
        text="GOBLIN FORTRESS",
        font=("Consolas", 24, "bold"),
        foreground="#E8D5A5",
        background="#202B23",
    ).pack(pady=(12, 8))
    tk.Label(
        menu_frame,
        text="Explore, gather, craft, and survive.",
        font=("Consolas", 11),
        foreground="#C5D0C0",
        background="#202B23",
    ).pack(pady=(0, 24))
    menu_buttons = tk.Frame(menu_frame, background="#202B23")
    menu_buttons.pack()
    menu_status = tk.Label(
        menu_frame,
        text="",
        foreground="#F0C080",
        background="#202B23",
        wraplength=360,
    )
    menu_status.pack(pady=(12, 0))

    main_frame = tk.Frame(root, padx=12, pady=12)
    map_column = tk.Frame(main_frame)
    map_column.grid(row=0, column=0, sticky="n")
    right_panel = tk.Frame(main_frame)
    right_panel.grid(row=0, column=1, padx=(12, 0), sticky="n")

    tk.Button(
        map_column,
        text="Save / Menu",
        command=lambda: open_save_menu(),
    ).pack(anchor="w", pady=(0, 5))
    tk.Button(
        map_column,
        text="Clans & Politics",
        command=lambda: open_clan_management(),
    ).pack(anchor="w", pady=(0, 5))
    map_frame = tk.LabelFrame(map_column, text="World", padx=8, pady=8)
    map_frame.pack(anchor="w")
    map_canvas = tk.Canvas(
        map_frame,
        width=viewport_width * cell_size,
        height=viewport_height * cell_size,
        highlightthickness=0,
        background="#000000",
    )
    map_canvas.pack()
    map_background_items = []
    map_text_items = []
    map_render_states = []
    rendered_view_state = None
    rendered_player_position = None
    rendered_entity_ids = set()
    zoom_frame = tk.Frame(map_column)
    zoom_frame.pack(anchor="w", fill="x", pady=(5, 0))
    tk.Button(
        zoom_frame, text="Zoom -", command=lambda: change_zoom(-1)
    ).pack(side="left")
    zoom_label = tk.Label(zoom_frame, anchor="center", width=12)
    zoom_label.pack(side="left", expand=True)
    tk.Button(
        zoom_frame, text="Zoom +", command=lambda: change_zoom(1)
    ).pack(side="right")
    task_frame = tk.LabelFrame(map_column, text="Task queue", padx=6, pady=5)
    task_frame.pack(anchor="w", fill="x", pady=(5, 0))
    task_list = tk.Listbox(
        task_frame, width=32, height=4, exportselection=False
    )
    task_list.pack(fill="x")
    tk.Button(
        task_frame,
        text="Clear tasks",
        command=lambda: clear_task_queue("Task queue cleared."),
    ).pack(fill="x", pady=(4, 0))

    layer_frame = tk.LabelFrame(right_panel, text="Y Level", padx=8, pady=8)
    layer_frame.pack(fill="x")
    layer_label = tk.Label(layer_frame, anchor="w")
    layer_label.pack(fill="x")
    layer_buttons = tk.Frame(layer_frame)
    layer_buttons.pack(fill="x", pady=(5, 0))

    health_frame = tk.LabelFrame(right_panel, text="Player Health", padx=8, pady=6)
    health_frame.pack(fill="x", pady=(8, 0))
    health_bar = tk.Canvas(
        health_frame, width=190, height=22, highlightthickness=1,
        highlightbackground="#444444", background="#202020",
    )
    health_bar.pack(fill="x")
    health_render_state = None
    infection_label = tk.Label(
        health_frame, text="No infection", anchor="w", foreground="#63C76A"
    )
    infection_label.pack(fill="x", pady=(3, 0))
    tk.Button(
        health_frame,
        text="Treat infection (1 Healing Salve)",
        command=lambda: treat_infection(),
    ).pack(fill="x", pady=(3, 0))
    limb_frame = tk.LabelFrame(
        right_panel, text="Body Parts & Organs", padx=6, pady=5
    )
    limb_frame.pack(fill="x", pady=(8, 0))
    limb_bars = {}
    for part_id, part_label in BODY_PARTS:
        row = tk.Frame(limb_frame)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=part_label, width=10, anchor="w").pack(side="left")
        limb_bar = tk.Canvas(
            row, width=130, height=16, highlightthickness=1,
            highlightbackground="#555555", background="#202020",
        )
        limb_bar.pack(side="left", fill="x", expand=True)
        limb_bars[part_id] = limb_bar
    tk.Button(
        limb_frame,
        text="Surgery / Reattach",
        command=lambda: open_surgery_dialog(),
    ).pack(fill="x", pady=(4, 0))
    limb_render_states = {}

    hotbar_frame = tk.LabelFrame(right_panel, text="Hotbar", padx=8, pady=8)
    hotbar_frame.pack(fill="x", pady=(8, 0))
    hotbar_items = [None] * 5
    selected_hotbar_slot = 0
    hotbar_buttons = []

    inventory_frame = tk.LabelFrame(right_panel, text="Inventory", padx=8, pady=8)
    inventory_frame.pack(fill="both", expand=True, pady=(8, 0))
    tk.Label(inventory_frame, text="Search items").pack(anchor="w")
    inventory_search_var = tk.StringVar()
    inventory_search = tk.Entry(
        inventory_frame, textvariable=inventory_search_var
    )
    inventory_search.pack(fill="x", pady=(0, 5))
    inventory_list = tk.Listbox(inventory_frame, width=26, height=9, exportselection=False)
    inventory_list.pack(fill="both", expand=True)
    tk.Button(
        inventory_frame,
        text="Craft from inventory",
        command=lambda: open_inventory_crafting(),
    ).pack(fill="x", pady=(5, 0))
    inventory_visible = []
    inventory_render_state = None
    simple_crafting_recipe_ids = {
        "oak_planks",
        "oak_sapling",
        "oak_door",
        "grass",
        "dirt_bricks",
        "stone_bricks",
        "torch",
        "stone_pickaxe",
        "stone_shovel",
    }
    crafting_queue = []
    crafting_job = None
    crafting_ui = None
    hovered_name = tk.Label(
        inventory_frame,
        text="Hover over a map tile",
        anchor="w",
        justify="left",
        wraplength=190,
    )
    hovered_name.pack(fill="x", pady=(6, 0))
    hovered_cell = None
    mining_state = None

    status = tk.Label(main_frame, anchor="w", justify="left")
    status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    controls = tk.Label(
        main_frame,
        text=(
            "Move: arrows/WASD | Player Y: Ctrl+Up/Down | "
            "Camera Y: [ / ] | Hotbar: 1-5 | F: use item | "
            "Attack nearby creatures: right-click | "
            "Zoom: buttons / Ctrl+scroll | Right-click inventory item to assign"
        ),
        anchor="w",
    )
    controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def selected_item():
        hotbar_item = hotbar_items[selected_hotbar_slot]
        if hotbar_item in player_character.inventory:
            return hotbar_item
        selection = inventory_list.curselection()
        return inventory_visible[selection[0]] if selection else None

    def refresh_hotbar():
        for index, button in enumerate(hotbar_buttons):
            item = hotbar_items[index]
            if item not in player_character.inventory:
                hotbar_items[index] = None
                item = None
            stack_count = (
                f" x{item.count}" if item and item.stackable else ""
            )
            label = (
                f"{index + 1}: {item.icon} {item.name}{stack_count}"
                if item else f"{index + 1}: Empty"
            )
            relief = "sunken" if index == selected_hotbar_slot else "raised"
            if button.cget("text") != label or button.cget("relief") != relief:
                button.config(text=label, relief=relief)

    def select_hotbar_slot(index):
        nonlocal selected_hotbar_slot
        selected_hotbar_slot = index
        refresh_hotbar()
        status.config(text=f"Selected hotbar slot {index + 1}.")

    def assign_inventory_to_hotbar(_event=None):
        item = selected_item_from_inventory()
        if item is None:
            status.config(text="Select an inventory item to put in the hotbar.")
            return
        hotbar_items[selected_hotbar_slot] = item
        refresh_hotbar()
        status.config(text=f"Put {item.name} in hotbar slot {selected_hotbar_slot + 1}.")

    def use_selected_item():
        item = selected_item()
        if not isinstance(item, usableItem):
            status.config(text="That item cannot be used directly.")
            return
        enqueue_task("use", item=item)

    def selected_item_from_inventory():
        selection = inventory_list.curselection()
        return inventory_visible[selection[0]] if selection else None

    def refresh_inventory():
        nonlocal inventory_render_state
        selection = inventory_list.curselection()
        selected = inventory_visible[selection[0]] if selection else None
        query = inventory_search_var.get().strip().casefold()
        visible_items = [
            item for item in player_character.inventory
            if query in item.name.casefold() or query in item.description.casefold()
        ]
        render_state = (
            query,
            tuple(
                (
                    id(item),
                    item.name,
                    item.icon,
                    getattr(item, "count", 1),
                    getattr(item, "durability", None),
                    (
                        item.spoiled,
                        round(item.spoil_remaining),
                    ) if isinstance(item, bodyPartItem) else None,
                )
                for item in visible_items
            )
        )
        if render_state != inventory_render_state:
            inventory_visible[:] = visible_items
            inventory_list.delete(0, tk.END)
            for item in inventory_visible:
                stack_count = f" x{item.count}" if item.stackable else ""
                durability = (
                    f" ({item.durability} uses)"
                    if isinstance(item, toolItem) else ""
                )
                spoilage = (
                    f" ({max(0, round(item.spoil_remaining))}s)"
                    if isinstance(item, bodyPartItem) and not item.spoiled
                    else ""
                )
                inventory_list.insert(
                    tk.END,
                    f"{item.icon} {item.name}{stack_count}{durability}{spoilage}",
                )
            if selected in inventory_visible:
                inventory_list.selection_set(inventory_visible.index(selected))
            inventory_render_state = render_state
        refresh_hotbar()
        refresh_crafting_menu()

    def refresh_crafting_menu():
        if crafting_ui is None or not crafting_ui["window"].winfo_exists():
            return
        previous = (
            crafting_ui["recipes"][crafting_ui["recipe_list"].curselection()[0]]
            if crafting_ui["recipe_list"].curselection()
            else None
        )
        available_recipes = [
            recipe
            for recipe in crafting_recipes
            if recipe["id"] in simple_crafting_recipe_ids
            and not missing_recipe_ingredients(
                player_character.inventory, recipe
            )[1]
        ]
        crafting_ui["recipes"][:] = available_recipes
        recipe_list = crafting_ui["recipe_list"]
        recipe_list.delete(0, tk.END)
        for recipe in available_recipes:
            result = recipe["result"]
            recipe_list.insert(
                tk.END, f"{result['icon']} {recipe['name']} x{result['quantity']}"
            )
        if previous in available_recipes:
            recipe_list.selection_set(available_recipes.index(previous))
        elif available_recipes:
            recipe_list.selection_set(0)
        update_crafting_recipe_details()
        update_crafting_queue_display()

    def update_crafting_recipe_details(_event=None):
        if crafting_ui is None or not crafting_ui["window"].winfo_exists():
            return
        selection = crafting_ui["recipe_list"].curselection()
        if not selection:
            crafting_ui["details"].config(
                text="Gather ingredients to unlock simple recipes."
            )
            return
        recipe = crafting_ui["recipes"][selection[0]]
        ingredients = "\n".join(
            f"{ingredient['quantity']} x "
            f"{ingredient['id'].replace('_', ' ').title()}"
            for ingredient in recipe["ingredients"]
        )
        result = recipe["result"]
        crafting_ui["details"].config(
            text=(
                f"{result['name']} x{result['quantity']}\n\n"
                f"Ingredients:\n{ingredients}\n\n"
                f"{recipe.get('description', '')}"
            )
        )

    def update_crafting_queue_display():
        if crafting_ui is None or not crafting_ui["window"].winfo_exists():
            return
        queue_list = crafting_ui["queue_list"]
        queue_list.delete(0, tk.END)
        for index, recipe in enumerate(crafting_queue, start=1):
            queue_list.insert(
                tk.END, f"{index}. {recipe['name']} x{recipe['result']['quantity']}"
            )
        if crafting_job is None:
            crafting_ui["current"].config(text="Idle")
            crafting_ui["progress"].delete("all")
        else:
            crafting_ui["current"].config(
                text=f"Crafting: {crafting_job['recipe']['name']}"
            )

    def open_inventory_crafting():
        nonlocal crafting_ui
        if crafting_ui is not None and crafting_ui["window"].winfo_exists():
            crafting_ui["window"].deiconify()
            crafting_ui["window"].lift()
            refresh_crafting_menu()
            return

        dialog = tk.Toplevel(root)
        dialog.title("Inventory crafting")
        dialog.transient(root)
        dialog.geometry("580x440")
        crafting_ui = {"window": dialog, "recipes": []}
        content = tk.Frame(dialog, padx=10, pady=10)
        content.pack(fill="both", expand=True)
        recipe_column = tk.Frame(content)
        recipe_column.pack(side="left", fill="both", expand=True)
        detail_column = tk.Frame(content, padx=10)
        detail_column.pack(side="left", fill="both", expand=True)
        tk.Label(recipe_column, text="Available simple recipes").pack(anchor="w")
        recipe_list = tk.Listbox(
            recipe_column, width=30, height=12, exportselection=False
        )
        recipe_list.pack(fill="both", expand=True, pady=(4, 0))
        details = tk.Label(
            detail_column,
            text="",
            anchor="nw",
            justify="left",
            wraplength=230,
        )
        details.pack(fill="both", expand=True)
        tk.Button(
            detail_column,
            text="Add to crafting queue",
            command=queue_selected_recipe,
        ).pack(anchor="e", pady=(8, 0))
        current = tk.Label(content, text="Idle", anchor="w")
        current.pack(fill="x", pady=(10, 3))
        progress = tk.Canvas(
            content,
            width=540,
            height=18,
            highlightthickness=1,
            highlightbackground="#444444",
            background="#202020",
        )
        progress.pack(fill="x")
        tk.Label(content, text="Waiting in queue", anchor="w").pack(
            fill="x", pady=(8, 2)
        )
        queue_list = tk.Listbox(content, height=5, exportselection=False)
        queue_list.pack(fill="both", expand=True)
        crafting_ui.update(
            recipe_list=recipe_list,
            details=details,
            current=current,
            progress=progress,
            queue_list=queue_list,
        )
        recipe_list.bind("<<ListboxSelect>>", update_crafting_recipe_details)
        refresh_crafting_menu()

    def queue_selected_recipe():
        if crafting_ui is None or not crafting_ui["window"].winfo_exists():
            return
        selection = crafting_ui["recipe_list"].curselection()
        if not selection:
            return
        recipe = crafting_ui["recipes"][selection[0]]
        required, missing = missing_recipe_ingredients(
            player_character.inventory, recipe
        )
        if missing:
            refresh_crafting_menu()
            return
        consume_recipe_ingredients(player_character.inventory, required)
        crafting_queue.append(recipe)
        refresh_display()
        update_crafting_queue_display()
        update_crafting_progress()

    def update_crafting_progress():
        nonlocal crafting_job
        if crafting_job is None:
            if not crafting_queue:
                update_crafting_queue_display()
                return
            recipe = crafting_queue.pop(0)
            crafting_job = {
                "recipe": recipe,
                "started_at": time.monotonic(),
                "duration": 1.5 + 0.5 * sum(
                    ingredient["quantity"]
                    for ingredient in recipe["ingredients"]
                ),
            }
            update_crafting_queue_display()

        elapsed = time.monotonic() - crafting_job["started_at"]
        progress_ratio = min(1.0, elapsed / crafting_job["duration"])
        if crafting_ui is not None and crafting_ui["window"].winfo_exists():
            progress = crafting_ui["progress"]
            progress.delete("all")
            progress.create_rectangle(
                0,
                0,
                round(540 * progress_ratio),
                18,
                fill="#52D273",
                outline="",
            )
        if progress_ratio >= 1.0:
            recipe = crafting_job["recipe"]
            add_crafting_result(
                player_character.inventory, recipe["result"], game_world
            )
            crafting_job = None
            refresh_display()
            update_crafting_queue_display()
        if crafting_job is not None or crafting_queue:
            root.after(50, update_crafting_progress)

    def set_hovered_cell(x, y):
        nonlocal hovered_cell
        if hovered_cell == (x, y):
            return
        hovered_cell = (x, y)
        update_hovered_name()

    def update_hovered_name():
        if hovered_cell is None:
            hovered_name.config(text="Hover over a map tile")
            return

        origin_x, origin_y = current_view_origin()
        cell_x, cell_y = hovered_cell
        x, y = origin_x + cell_x, origin_y + cell_y
        player_x, player_y, player_depth = player_character.pos
        if (x, y) == (player_x, player_y) and player_depth == selected_layer:
            hovered_name.config(
                text=(
                    f"@ {player_character.name} "
                    f"(Y level {world.Y_LEVEL_COUNT - player_depth})"
                )
            )
            return

        visible_tile = game_world.get_visible_tile(x, y, selected_layer)
        if visible_tile is not None:
            depth, tile = visible_tile
            tile_name = tile.name
        else:
            depth = selected_layer
            tile_name = "Air"
        hovered_name.config(
            text=f"{tile_name} (Y level {world.Y_LEVEL_COUNT - depth})"
        )

    def draw_entity(
        canvas, creature, offset_x=0, offset_y=0, tag="dynamic"
    ):
        color = creature.foreground_color
        scale = cell_size / 34
        line_width = max(1, round(2 * scale))
        attached = creature.attached_parts
        if "chest" in attached:
            canvas.create_line(
                offset_x + round(17 * scale), offset_y + round(13 * scale),
                offset_x + round(17 * scale), offset_y + round(22 * scale),
                fill=color, width=line_width, tags=(tag,),
            )
        if "left_arm" in attached:
            canvas.create_line(
                offset_x + round(17 * scale), offset_y + round(15 * scale),
                offset_x + round(10 * scale), offset_y + round(19 * scale),
                fill=color, width=line_width, tags=(tag,),
            )
        if "right_arm" in attached:
            canvas.create_line(
                offset_x + round(17 * scale), offset_y + round(15 * scale),
                offset_x + round(24 * scale), offset_y + round(19 * scale),
                fill=color, width=line_width, tags=(tag,),
            )
        if "left_leg" in attached:
            canvas.create_line(
                offset_x + round(17 * scale), offset_y + round(22 * scale),
                offset_x + round(12 * scale), offset_y + round(29 * scale),
                fill=color, width=line_width, tags=(tag,),
            )
        if "right_leg" in attached:
            canvas.create_line(
                offset_x + round(17 * scale), offset_y + round(22 * scale),
                offset_x + round(22 * scale), offset_y + round(29 * scale),
                fill=color, width=line_width, tags=(tag,),
            )
        if "head" in attached:
            canvas.create_oval(
                offset_x + round(12 * scale), offset_y + round(2 * scale),
                offset_x + round(22 * scale), offset_y + round(12 * scale),
                fill=color, outline="#FFFFFF", width=max(1, round(scale)),
                tags=(tag,),
            )
            canvas.create_text(
                offset_x + round(17 * scale), offset_y + round(7 * scale),
                text=creature.icon, fill="#FFFFFF",
                font=("Consolas", max(5, round(7 * scale)), "bold"),
                tags=(tag,),
            )

    def update_map_cell(
        screen_x, screen_y, origin_x, origin_y, player_depth,
        draw_sprites=True,
    ):
        world_x, world_y = origin_x + screen_x, origin_y + screen_y
        visible_tile = game_world.get_visible_tile(
            world_x, world_y, selected_layer
        )
        tile = visible_tile[1] if visible_tile is not None else None
        visible_depth = (
            visible_tile[0] if visible_tile is not None else selected_layer
        )
        is_player = (world_x, world_y, selected_layer) == player_character.pos
        left = screen_x * cell_size
        top = screen_y * cell_size
        background = "#000000"
        text = ""
        foreground = "#FFFFFF"
        font_size = max(7, round(cell_size * 14 / 34))
        if hasattr(tile, "background_color"):
            background = color_at_depth(
                tile.background_color, visible_depth, player_depth
            )
        if is_player:
            text = None
            if draw_sprites:
                draw_entity(
                    map_canvas, player_character, left, top, "player-sprite"
                )
        elif isinstance(tile, entity):
            text = None
            if draw_sprites and tile not in rendered_entity_ids:
                draw_entity(map_canvas, tile, left, top)
                rendered_entity_ids.add(tile)
        elif tile is not None:
            text = (
                "/" if isinstance(tile, door) and tile.is_open
                else tile.icon
            )
            foreground = color_at_depth(
                tile.foreground_color, visible_depth, player_depth
            )
        index = screen_y * viewport_width + screen_x
        render_state = (background, text, foreground, font_size)
        if map_render_states[index] != render_state:
            map_canvas.itemconfigure(
                map_background_items[index], fill=background
            )
            map_canvas.itemconfigure(
                map_text_items[index],
                text=text or "",
                fill=foreground,
                font=("Consolas", font_size),
            )
            map_render_states[index] = render_state
        return tile

    def draw_mining_progress(origin_x, origin_y):
        if mining_state is None:
            return
        world_x, world_y = mining_state["position"][:2]
        screen_x, screen_y = world_x - origin_x, world_y - origin_y
        if not (
            0 <= screen_x < viewport_width
            and 0 <= screen_y < viewport_height
        ):
            return
        progress = min(
            1.0,
            (time.monotonic() - mining_state["started_at"])
            / mining_state["duration"],
        )
        left, top = screen_x * cell_size, screen_y * cell_size
        bar_margin = max(1, round(cell_size * 0.06))
        bar_top = max(1, round(cell_size * 0.06))
        bar_bottom = max(bar_top + 1, round(cell_size * 0.15))
        bar_width = cell_size - 2 * bar_margin
        map_canvas.create_rectangle(
            left + bar_margin, top + bar_top,
            left + cell_size - bar_margin, top + bar_bottom,
            fill="#202020", outline="#FFFFFF", tags=("dynamic",),
        )
        if progress > 0:
            map_canvas.create_rectangle(
                left + bar_margin + 1, top + bar_top + 1,
                left + bar_margin + 1 + round((bar_width - 2) * progress),
                top + bar_bottom - 1,
                fill="#52D273", outline="", tags=("dynamic",),
            )

    def refresh_display(player_moved_from=None):
        nonlocal health_render_state, rendered_view_state
        nonlocal rendered_player_position
        player_x, player_y, player_depth = player_character.pos
        health_ratio = (
            max(0, min(1, player_character.hp / player_character.max_health))
            if player_character.max_health > 0 else 0
        )
        health_state = (player_character.hp, player_character.max_health)
        if health_render_state != health_state:
            health_bar.delete("all")
            health_bar.create_rectangle(
                0, 0, round(190 * health_ratio), 22,
                fill="#3DBB58" if health_ratio > 0.3 else "#D44343",
                outline="",
            )
            health_bar.create_text(
                95, 11,
                text=f"HP {player_character.hp}/{player_character.max_health}",
                fill="#FFFFFF",
                font=("Consolas", 10, "bold"),
            )
            health_render_state = health_state
        infection_label.config(
            text=(
                "Infection: losing 1 HP every 5 seconds"
                if player_character.infected else "No infection"
            ),
            foreground="#D44343" if player_character.infected else "#63C76A",
        )
        for part_id, part_label in BODY_PARTS:
            limb_health = player_character.limb_health[part_id]
            limb_ratio = limb_health / player_character.max_health
            attached = part_id in player_character.attached_parts
            limb_state = (limb_health, player_character.max_health, attached)
            if limb_render_states.get(part_id) != limb_state:
                limb_bar = limb_bars[part_id]
                limb_bar.delete("all")
                limb_bar.create_rectangle(
                    0, 0, round(130 * limb_ratio), 16,
                    fill="#555555" if not attached else (
                        "#3DBB58" if limb_ratio > 0.6
                        else "#E0A52E" if limb_ratio > 0.3
                        else "#D44343"
                    ),
                    outline="",
                )
                limb_bar.create_text(
                    65, 8,
                    text=(
                        "Missing" if not attached
                        else f"{limb_health}/{player_character.max_health}"
                    ),
                    fill="#FFFFFF",
                    font=("Consolas", 8, "bold"),
                )
                limb_render_states[part_id] = limb_state
        game_world.load_chunk(
            player_x // game_world.chunk_width,
            player_y // game_world.chunk_height,
        )
        origin_x, origin_y = current_view_origin()
        chunk_x = player_x // game_world.chunk_width
        chunk_y = player_y // game_world.chunk_height
        visible_bottom_depth = min(
            world.Y_LEVEL_COUNT - 1,
            selected_layer + world.VISIBLE_DEPTHS_BELOW,
        )
        camera_y_level = world.Y_LEVEL_COUNT - selected_layer
        lowest_visible_y_level = world.Y_LEVEL_COUNT - visible_bottom_depth
        map_frame.config(
            text=(
                f"Chunk ({chunk_x}, {chunk_y}) | "
                f"Y Level {camera_y_level} over to Y Level "
                f"{lowest_visible_y_level}"
            )
        )
        layer_label.config(
            text=(
                f"Camera Y: {camera_y_level} | "
                f"Player Y: {world.Y_LEVEL_COUNT - player_depth}"
            )
        )
        view_state = (
            origin_x, origin_y, selected_layer,
            viewport_width, viewport_height,
        )
        can_update_player_only = (
            player_moved_from is not None
            and rendered_view_state == view_state
            and rendered_player_position == player_moved_from
        )
        if can_update_player_only:
            map_canvas.delete("player-sprite")
            old_screen = (
                player_moved_from[0] - origin_x,
                player_moved_from[1] - origin_y,
            )
            new_screen = (player_x - origin_x, player_y - origin_y)
            old_tile = None
            if (
                0 <= old_screen[0] < viewport_width
                and 0 <= old_screen[1] < viewport_height
            ):
                old_tile = update_map_cell(
                    *old_screen, origin_x, origin_y, player_depth,
                    draw_sprites=False,
                )
            if (
                isinstance(old_tile, entity)
                and old_tile not in rendered_entity_ids
            ):
                draw_entity(
                    map_canvas,
                    old_tile,
                    old_screen[0] * cell_size,
                    old_screen[1] * cell_size,
                )
                rendered_entity_ids.add(old_tile)
            if (
                0 <= new_screen[0] < viewport_width
                and 0 <= new_screen[1] < viewport_height
            ):
                update_map_cell(
                    *new_screen, origin_x, origin_y, player_depth,
                    draw_sprites=False,
                )
                draw_entity(
                    map_canvas, player_character,
                    new_screen[0] * cell_size,
                    new_screen[1] * cell_size,
                    "player-sprite",
                )
        else:
            map_canvas.delete("dynamic")
            map_canvas.delete("player-sprite")
            rendered_entity_ids.clear()
            for y in range(viewport_height):
                for x in range(viewport_width):
                    update_map_cell(
                        x, y, origin_x, origin_y, player_depth
                    )
            draw_mining_progress(origin_x, origin_y)
        rendered_view_state = view_state
        rendered_player_position = player_character.pos

        refresh_inventory()
        update_hovered_name()
        status.config(
            text=(
                f"Position: ({player_x}, {player_y})  "
                f"Health: {player_character.health}  "
                f"Coins: {politics.wallet}"
            )
        )

    def treat_infection():
        if not player_character.infected:
            status.config(text="You are not infected.")
            return
        salve = next(
            (
                item for item in player_character.inventory
                if isinstance(item, usableItem)
                and item.item_id == "healing_salve"
            ),
            None,
        )
        if salve is None:
            status.config(text="Treating infection requires a Healing Salve.")
            return
        player_character.remove_from_inventory(salve)
        player_character.infected = False
        player_character.infection_elapsed = 0.0
        refresh_display()
        status.config(text="You treated the infection with a Healing Salve.")

    def open_surgery_dialog():
        dialog = tk.Toplevel(root)
        dialog.title("Surgery and reattachment")
        dialog.transient(root)
        dialog.grab_set()
        tk.Label(
            dialog,
            text="Remove an attached part or reattach a part in your inventory.",
            wraplength=360,
        ).pack(anchor="w", padx=10, pady=(10, 4))
        part_list = tk.Listbox(
            dialog, width=48, height=len(BODY_PARTS), exportselection=False
        )
        part_list.pack(fill="both", expand=True, padx=10, pady=4)
        surgery_status = tk.Label(dialog, text="", anchor="w", wraplength=360)
        surgery_status.pack(fill="x", padx=10, pady=4)

        def refresh_parts():
            selection = part_list.curselection()
            selected_index = selection[0] if selection else 0
            part_list.delete(0, tk.END)
            for part_id, part_name in BODY_PARTS:
                detached = next(
                    (
                        item for item in player_character.inventory
                        if isinstance(item, bodyPartItem)
                        and item.part_id == part_id
                    ),
                    None,
                )
                if part_id in player_character.attached_parts:
                    part_status = (
                        f"Attached, {player_character.limb_health[part_id]} HP"
                    )
                elif detached is None:
                    part_status = "Missing; no replacement in inventory"
                else:
                    part_status = (
                        "Missing; replacement spoiled"
                        if detached.spoiled else
                        "Missing; replacement infected"
                        if detached.infected else "Missing; replacement fresh"
                    )
                part_list.insert(tk.END, f"{part_name}: {part_status}")
            if BODY_PARTS:
                selected_index = min(selected_index, len(BODY_PARTS) - 1)
                part_list.selection_set(selected_index)

        def selected_part():
            selection = part_list.curselection()
            return BODY_PARTS[selection[0]] if selection else None

        def amputate_selected():
            selected = selected_part()
            if selected is None:
                surgery_status.config(text="Select a body part first.")
                return
            part_id, part_name = selected
            part_health = player_character.amputate(part_id)
            if part_health is None:
                surgery_status.config(text=f"{part_name} is already missing.")
                return
            detached = bodyPartItem(
                part_id,
                part_name,
                part_health,
                infected=player_character.infected,
            )
            player_character.add_to_inventory(detached)
            refresh_parts()
            refresh_display()
            surgery_status.config(
                text=f"Amputated {part_name}; it will spoil after 10 warm minutes."
            )

        def reattach_selected():
            selected = selected_part()
            if selected is None:
                surgery_status.config(text="Select a body part first.")
                return
            part_id, part_name = selected
            update_spoilage_in_collection(
                player_character.inventory,
                game_world,
                location=player_character.pos,
            )
            detached = next(
                (
                    item for item in player_character.inventory
                    if isinstance(item, bodyPartItem)
                    and item.part_id == part_id
                ),
                None,
            )
            if detached is None:
                surgery_status.config(
                    text=f"You have no {part_name.lower()} to reattach."
                )
                return
            if part_id in player_character.attached_parts:
                surgery_status.config(text=f"{part_name} is already attached.")
                return
            player_character.reattach(detached)
            player_character.remove_from_inventory(detached)
            refresh_parts()
            refresh_display()
            condition = (
                "spoiled" if detached.spoiled else "infected"
            )
            surgery_status.config(
                text=(
                    f"Reattached {condition} {part_name}; "
                    "infection has taken hold."
                    if detached.spoiled or detached.infected
                    else f"Reattached {part_name}."
                )
            )

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(
            controls, text="Amputate selected", command=amputate_selected
        ).pack(side="left")
        tk.Button(
            controls, text="Reattach selected", command=reattach_selected
        ).pack(side="right")
        refresh_parts()

    def refresh_menu_save_buttons():
        try:
            paths = save_game_paths()
        except OSError as error:
            menu_status.config(text=f"Could not read save games: {error}")
            paths = []
        continue_button.config(state="normal" if paths else "disabled")

    def restore_game(path):
        nonlocal selected_layer, selected_hotbar_slot, mining_state
        try:
            restored = load_game_data(path, game_world)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            menu_status.config(text=f"Could not load {path.name}: {error}")
            return False
        clear_task_queue()
        player_character.move(restored["pos"])
        politics.load_data(restored["clan_politics"])
        player_character.hp = restored["health"]
        player_character.restore_limb_health(restored["limb_health"])
        player_character.restore_body_state(
            restored["attached_parts"],
            restored["infected"],
            restored["infection_elapsed"],
        )
        player_character.inventory[:] = restored["inventory"]
        selected_layer = restored["selected_layer"]
        selected_hotbar_slot = restored["selected_hotbar_slot"]
        hotbar_items[:] = (restored["hotbar"] + [None] * 5)[:5]
        mining_state = None
        refresh_display()
        menu_status.config(text=f"Loaded {path.stem.replace('_', ' ')}.")
        return True

    def continue_game():
        try:
            paths = save_game_paths()
        except OSError as error:
            menu_status.config(text=f"Could not read save games: {error}")
            return
        if not paths:
            menu_status.config(text="No saved games yet.")
            refresh_menu_save_buttons()
            return
        if restore_game(paths[0]):
            start_game()

    def open_load_game_menu():
        try:
            paths = save_game_paths()
        except OSError as error:
            menu_status.config(text=f"Could not read save games: {error}")
            return
        if not paths:
            menu_status.config(text="No saved games yet.")
            return

        dialog = tk.Toplevel(root)
        dialog.title("Load game")
        dialog.transient(root)
        dialog.grab_set()
        tk.Label(dialog, text="Select a saved game:").pack(
            anchor="w", padx=10, pady=(10, 4)
        )
        save_list = tk.Listbox(dialog, width=42, height=10, exportselection=False)
        save_list.pack(fill="both", expand=True, padx=10, pady=4)
        load_status = tk.Label(dialog, text="", anchor="w", wraplength=300)
        load_status.pack(fill="x", padx=10, pady=4)
        for path in paths:
            saved_time = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            save_list.insert(
                tk.END, f"{path.stem.replace('_', ' ')}  ({saved_time})"
            )
        save_list.selection_set(0)

        def load_selected():
            selection = save_list.curselection()
            if not selection:
                load_status.config(text="Select a saved game.")
                return
            if restore_game(paths[selection[0]]):
                dialog.destroy()
                start_game()

        tk.Button(
            dialog, text="Load selected game", command=load_selected
        ).pack(anchor="e", padx=10, pady=(4, 10))
        save_list.bind("<Double-Button-1>", lambda _event: load_selected())

    def open_save_menu():
        dialog = tk.Toplevel(root)
        dialog.title("Save game")
        dialog.transient(root)
        dialog.grab_set()
        tk.Label(dialog, text="Save name:").pack(
            anchor="w", padx=12, pady=(12, 4)
        )
        name_var = tk.StringVar(
            value=datetime.now().strftime("Save %Y-%m-%d %H-%M-%S")
        )
        name_entry = tk.Entry(dialog, textvariable=name_var, width=36)
        name_entry.pack(fill="x", padx=12)
        save_status = tk.Label(dialog, text="", anchor="w", wraplength=300)
        save_status.pack(fill="x", padx=12, pady=8)

        def save_current_game():
            try:
                save_path = save_path_for_name(name_var.get())
                if save_path.exists() and not messagebox.askyesno(
                    "Overwrite save",
                    f"Replace the existing save '{save_path.stem}'?",
                    parent=dialog,
                ):
                    return
                save_game_data(
                    save_path,
                    game_world,
                    player_character,
                    selected_layer,
                    hotbar_items,
                    selected_hotbar_slot,
                    politics,
                )
            except (OSError, ValueError, TypeError) as error:
                save_status.config(text=f"Could not save game: {error}")
                return
            save_status.config(
                text=f"Saved as {save_path.stem.replace('_', ' ')}."
            )
            menu_status.config(text=save_status.cget("text"))
            refresh_menu_save_buttons()

        tk.Button(
            dialog, text="Save game", command=save_current_game
        ).pack(anchor="e", padx=12, pady=(0, 6))

        def return_to_menu():
            dialog.destroy()
            show_main_menu()

        tk.Button(
            dialog, text="Return to main menu", command=return_to_menu
        ).pack(anchor="e", padx=12, pady=(0, 12))
        name_entry.focus_set()

    def open_clan_management():
        dialog = tk.Toplevel(root)
        dialog.title("Clans & Politics")
        dialog.transient(root)
        dialog.geometry("680x560")

        wallet_label = tk.Label(dialog, anchor="w", font=("Consolas", 11, "bold"))
        wallet_label.pack(fill="x", padx=12, pady=(12, 4))
        content = tk.Frame(dialog)
        content.pack(fill="both", expand=True, padx=12, pady=6)
        clan_column = tk.LabelFrame(content, text="Clans", padx=8, pady=6)
        clan_column.pack(side="left", fill="y")
        clan_list = tk.Listbox(clan_column, width=24, height=13, exportselection=False)
        clan_list.pack(fill="y")
        clan_info = tk.Label(
            clan_column, text="", justify="left", anchor="nw", wraplength=190
        )
        clan_info.pack(fill="x", pady=(6, 4))
        tk.Button(
            clan_column, text="Join selected clan", command=lambda: run_clan_action(
                lambda: politics.join(selected_clan_id()),
                "You joined the clan.",
            ),
        ).pack(fill="x", pady=2)
        tk.Button(
            clan_column, text="Leave current clan", command=lambda: run_clan_action(
                politics.leave, "You left your clan."
            ),
        ).pack(fill="x", pady=2)
        tk.Button(
            clan_column,
            text="Seek next rank (50 / 100 coins)",
            command=lambda: run_clan_action(
                politics.seek_promotion,
                "You earned a promotion; the cost went to the treasury.",
            ),
        ).pack(fill="x", pady=2)

        tk.Label(clan_column, text="Found a clan (50 coins):").pack(
            anchor="w", pady=(10, 2)
        )
        new_clan_name = tk.Entry(clan_column)
        new_clan_name.pack(fill="x")
        tk.Button(
            clan_column,
            text="Found clan",
            command=lambda: run_clan_action(
                lambda: politics.create_clan(new_clan_name.get()),
                "Your new clan has been founded.",
            ),
        ).pack(fill="x", pady=2)

        management_column = tk.Frame(content)
        management_column.pack(side="left", fill="both", expand=True, padx=(12, 0))
        clan_status = tk.Label(management_column, anchor="w", justify="left")
        clan_status.pack(fill="x", pady=(0, 6))

        treasury_frame = tk.LabelFrame(
            management_column, text="Treasury", padx=8, pady=6
        )
        treasury_frame.pack(fill="x")
        treasury_label = tk.Label(treasury_frame, anchor="w")
        treasury_label.pack(fill="x")
        amount_row = tk.Frame(treasury_frame)
        amount_row.pack(fill="x", pady=(4, 0))
        tk.Label(amount_row, text="Coins:").pack(side="left")
        amount_var = tk.StringVar(value="10")
        tk.Entry(amount_row, textvariable=amount_var, width=8).pack(
            side="left", padx=(4, 8)
        )
        tk.Button(
            amount_row, text="Deposit", command=lambda: run_clan_action(
                lambda: politics.deposit(int(amount_var.get())),
                "Coins deposited into the treasury.",
            require_current_clan=True,
        ),
        ).pack(side="left", padx=2)
        tk.Button(
            amount_row, text="Withdraw", command=lambda: run_clan_action(
                lambda: politics.withdraw(int(amount_var.get())),
                "Coins withdrawn from the treasury.",
            require_current_clan=True,
            ),
        ).pack(side="left", padx=2)

        roster_frame = tk.LabelFrame(
            management_column, text="Members and ranks", padx=8, pady=6
        )
        roster_frame.pack(fill="both", expand=True, pady=(8, 0))
        member_list = tk.Listbox(
            roster_frame, height=7, exportselection=False
        )
        member_list.pack(fill="both", expand=True)
        rank_buttons = tk.Frame(roster_frame)
        rank_buttons.pack(fill="x", pady=(4, 0))
        tk.Button(
            rank_buttons, text="Promote", command=lambda: change_member_rank(1)
        ).pack(side="left", expand=True, fill="x")
        tk.Button(
            rank_buttons, text="Demote", command=lambda: change_member_rank(-1)
        ).pack(side="left", expand=True, fill="x", padx=(5, 0))

        diplomacy_frame = tk.LabelFrame(
            management_column, text="Diplomacy", padx=8, pady=6
        )
        diplomacy_frame.pack(fill="x", pady=(8, 0))
        relations_list = tk.Listbox(
            diplomacy_frame, height=4, exportselection=False
        )
        relations_list.pack(fill="x")
        relation_target = tk.StringVar()
        relation_menu = tk.OptionMenu(diplomacy_frame, relation_target, "")
        relation_menu.pack(fill="x", pady=(4, 0))
        relation_buttons = tk.Frame(diplomacy_frame)
        relation_buttons.pack(fill="x", pady=(4, 0))
        for relation in ("allied", "rival", "neutral"):
            tk.Button(
                relation_buttons,
                text=f"Set {relation}",
                command=lambda chosen=relation: set_clan_relation(chosen),
            ).pack(side="left", expand=True, fill="x", padx=1)

        clan_ids = []
        member_ids = []
        detail_clan_id = None
        action_status = tk.Label(dialog, anchor="w", justify="left")
        action_status.pack(fill="x", padx=12, pady=(4, 8))

        def selected_clan_id():
            selection = clan_list.curselection()
            if not selection:
                raise ValueError("Select a clan first.")
            return clan_ids[selection[0]]

        def run_clan_action(
            action, success_message, require_current_clan=False
        ):
            if (
                require_current_clan
                and detail_clan_id != politics.membership
            ):
                action_status.config(
                    text="Select your own clan to manage its treasury or politics."
                )
                return
            try:
                action()
            except (ValueError, KeyError) as error:
                action_status.config(text=str(error))
                return
            action_status.config(text=success_message)
            refresh_clan_window()

        def change_member_rank(direction):
            if detail_clan_id != politics.membership:
                action_status.config(
                    text="Select your own clan before changing member ranks."
                )
                return
            selection = member_list.curselection()
            if not selection:
                action_status.config(text="Select a clan member first.")
                return
            member_name = member_ids[selection[0]]
            run_clan_action(
                lambda: politics.change_rank(member_name, direction),
                f"Updated {member_name}'s rank.",
            )

        def set_clan_relation(relation):
            if detail_clan_id != politics.membership:
                action_status.config(
                    text="Select your own clan before changing relations."
                )
                return
            try:
                target_id = next(
                    clan_id for clan_id, clan in politics.clans.items()
                    if clan["name"] == relation_target.get()
                )
            except StopIteration:
                action_status.config(text="Choose another clan first.")
                return
            run_clan_action(
                lambda: politics.set_relation(target_id, relation),
                f"Relations are now {relation}.",
            )

        def update_relation_menu():
            menu = relation_menu["menu"]
            menu.delete(0, "end")
            options = [
                clan["name"]
                for clan_id, clan in politics.clans.items()
                if clan_id != politics.membership
            ]
            for name in options:
                menu.add_command(
                    label=name,
                    command=lambda value=name: relation_target.set(value),
                )
            relation_target.set(options[0] if options else "")

        def update_clan_details(clan_id):
            clan = politics.clans[clan_id]
            player_rank = clan["members"].get("You")
            clan_info.config(
                text=(
                    f"{clan['name']}\\n{clan['motto']}\\n"
                    f"Treasury: {clan['treasury']} coins"
                )
            )
            if player_rank is None:
                clan_status.config(
                    text="You are not a member. Joining is free."
                )
            else:
                clan_status.config(
                    text=f"Your rank: {player_rank.title()} in {clan['name']}."
                )
            treasury_label.config(
                text=(
                    f"Clan funds: {clan['treasury']} coins  |  "
                    f"Your wallet: {politics.wallet} coins"
                )
            )
            member_ids.clear()
            member_list.delete(0, tk.END)
            for name, rank in sorted(
                clan["members"].items(),
                key=lambda member: (-CLAN_RANKS.index(member[1]), member[0]),
            ):
                member_ids.append(name)
                member_list.insert(tk.END, f"{name} - {rank.title()}")
            relations_list.delete(0, tk.END)
            for other_id, relation in sorted(clan["relations"].items()):
                relations_list.insert(
                    tk.END,
                    f"{politics.clans[other_id]['name']}: {relation.title()}",
                )
            update_relation_menu()

        def refresh_clan_window():
            nonlocal detail_clan_id
            wallet_label.config(
                text=f"Your money: {politics.wallet} coins"
            )
            clan_ids[:] = sorted(
                politics.clans,
                key=lambda clan_id: politics.clans[clan_id]["name"].casefold(),
            )
            clan_list.delete(0, tk.END)
            for clan_id in clan_ids:
                clan_list.insert(tk.END, politics.clans[clan_id]["name"])
            if clan_ids:
                if detail_clan_id not in clan_ids:
                    detail_clan_id = (
                        politics.membership
                        if politics.membership in clan_ids else clan_ids[0]
                    )
                selected_index = clan_ids.index(detail_clan_id)
                clan_list.selection_set(selected_index)
                clan_list.see(selected_index)
                update_clan_details(detail_clan_id)
            else:
                clan_info.config(text="No clans exist.")

        def select_clan(_event=None):
            nonlocal detail_clan_id
            try:
                detail_clan_id = selected_clan_id()
            except ValueError:
                return
            update_clan_details(detail_clan_id)

        clan_list.bind("<<ListboxSelect>>", select_clan)
        tk.Button(
            dialog, text="Close", command=dialog.destroy
        ).pack(anchor="e", padx=12, pady=(0, 10))
        refresh_clan_window()

    def show_main_menu():
        nonlocal game_active, entity_update_job
        clear_task_queue()
        game_active = False
        if entity_update_job is not None:
            root.after_cancel(entity_update_job)
            entity_update_job = None
        root.unbind("<KeyPress>")
        main_frame.pack_forget()
        menu_frame.pack(fill="both", expand=True)
        refresh_menu_save_buttons()

    def start_new_game():
        nonlocal selected_layer, selected_hotbar_slot, mining_state
        clear_task_queue()
        game_world.generate_world(width, height)
        politics.reset()
        player_x, player_y = width // 2 - 1, height // 2 - 1
        start_depth = game_world.get_surface_depth(player_x, player_y)
        player_character.move((player_x, player_y, start_depth))
        player_character.restore_body_state(
            [part_id for part_id, _label in BODY_PARTS],
            False,
            0.0,
        )
        player_character.heal_fully()
        player_character.inventory.clear()
        selected_layer = start_depth
        selected_hotbar_slot = 0
        hotbar_items[:] = [None] * 5
        mining_state = None
        start_game()

    def change_layer(amount):
        nonlocal selected_layer
        selected_layer = max(
            0, min(world.Y_LEVEL_COUNT - 1, selected_layer + amount)
        )
        refresh_display()

    def handle_layer_scroll(event):
        scroll_up = (
            getattr(event, "num", None) == 4
            or getattr(event, "delta", 0) > 0
        )
        if getattr(event, "state", 0) & 0x4:
            change_zoom(1 if scroll_up else -1)
        elif scroll_up:
            change_layer(-1)
        elif (
            getattr(event, "num", None) == 5
            or getattr(event, "delta", 0) < 0
        ):
            change_layer(1)
        return "break"

    def handle_map_motion(event):
        cell_x = event.x // cell_size
        cell_y = event.y // cell_size
        if 0 <= cell_x < viewport_width and 0 <= cell_y < viewport_height:
            set_hovered_cell(cell_x, cell_y)

    def handle_map_leave(_event):
        nonlocal hovered_cell
        if hovered_cell is not None:
            hovered_cell = None
            update_hovered_name()

    def change_zoom(amount):
        nonlocal zoom_index, cell_size, viewport_width, viewport_height
        nonlocal hovered_cell
        new_zoom_index = max(
            0, min(len(zoom_levels) - 1, zoom_index + amount)
        )
        if new_zoom_index == zoom_index:
            return
        zoom_index = new_zoom_index
        cell_size = zoom_levels[zoom_index]
        viewport_width = max(1, viewport_pixels // cell_size)
        viewport_height = max(1, viewport_pixels // cell_size)
        hovered_cell = None
        zoom_label.config(text=f"Zoom: {round(cell_size / 34 * 100)}%")
        build_map_grid()
        if game_active:
            refresh_display()

    def build_map_grid():
        nonlocal map_background_items, map_text_items, map_render_states
        nonlocal rendered_view_state, rendered_player_position
        map_canvas.configure(
            width=viewport_width * cell_size,
            height=viewport_height * cell_size,
        )
        map_canvas.delete("all")
        map_background_items = []
        map_text_items = []
        map_render_states = []
        rendered_view_state = None
        rendered_player_position = None
        rendered_entity_ids.clear()
        for y in range(viewport_height):
            for x in range(viewport_width):
                left = x * cell_size
                top = y * cell_size
                map_background_items.append(
                    map_canvas.create_rectangle(
                        left, top, left + cell_size, top + cell_size,
                        fill="#000000", outline="#888888", width=1,
                    )
                )
                map_text_items.append(
                    map_canvas.create_text(
                        left + cell_size // 2,
                        top + cell_size // 2,
                        text="",
                        fill="#FFFFFF",
                        font=("Consolas", max(7, round(cell_size * 14 / 34))),
                    )
                )
                map_render_states.append(None)

    def change_player_y_level(amount):
        nonlocal selected_layer
        if game_world.move_player_y(player_character, amount):
            selected_layer = player_character.pos[2]
            refresh_display()

    def in_interaction_range(x, y):
        player_x, player_y, _ = player_character.pos
        return (x - player_x) ** 2 + (y - player_y) ** 2 <= 4 ** 2

    def refresh_task_list():
        task_list.delete(0, tk.END)
        if active_task is not None:
            task_list.insert(tk.END, f"Working: {active_task['label']}")
        task_list.insert(
            tk.END,
            *(task["label"] for task in task_queue),
        )

    def finish_active_task(delay=100):
        nonlocal active_task, task_started, task_runner_job
        active_task = None
        task_started = False
        refresh_task_list()
        if task_queue:
            task_runner_job = root.after(delay, process_task_queue)

    def clear_task_queue(message=None):
        nonlocal active_task, task_started, task_runner_job, mining_state
        if task_runner_job is not None:
            root.after_cancel(task_runner_job)
            task_runner_job = None
        task_queue.clear()
        active_task = None
        task_started = False
        mining_state = None
        refresh_task_list()
        if game_active:
            refresh_display()
        if message:
            status.config(text=message)

    def task_is_in_range(task):
        kind = task["kind"]
        x, y = task["position"]
        if kind == "use":
            return task["item"] in player_character.inventory
        if not in_interaction_range(x, y):
            return False
        if kind == "mine":
            depth = task["depth"]
            return (
                game_world.get_tile(x, y, depth) is task["target"]
                and abs(depth - player_character.pos[2]) <= 4
                and game_world.can_mine_target(
                    x, y, depth, player_character.pos
                )
            )
        if kind == "attack":
            player_x, player_y, player_depth = player_character.pos
            return (
                game_world.get_tile(x, y, task["depth"]) is task["target"]
                and task["depth"] == player_depth
                and abs(x - player_x) + abs(y - player_y) <= 1
            )
        if kind == "place":
            return (
                task["item"] in player_character.inventory
                and getattr(task["item"], "placeable", False)
                and placement_depth(x, y) is not None
            )
        return (
            game_world.get_tile(x, y, task["depth"]) is task["target"]
        )

    def enqueue_task(kind, x=None, y=None, depth=None, target=None, item=None):
        if kind == "use":
            label = f"Use {item.name}"
            position = None
        else:
            position = (x, y)
            if kind == "mine":
                label = f"Mine {target.name}"
            elif kind == "attack":
                label = f"Attack {target.name}"
            elif kind == "place":
                label = f"Place {item.name}"
            else:
                label = f"Use {target.name}"
        task_queue.append({
            "kind": kind,
            "position": position,
            "depth": depth,
            "target": target,
            "item": item,
            "tool": selected_item() if kind == "mine" else None,
            "label": label,
        })
        refresh_task_list()
        status.config(text=f"Queued {label}.")
        if active_task is None and task_runner_job is None:
            process_task_queue()

    def process_task_queue():
        nonlocal active_task, task_started, task_runner_job
        task_runner_job = None
        if task_started:
            return
        if active_task is None:
            if not task_queue:
                return
            task = task_queue.pop(0)
            if not task_is_in_range(task):
                task_queue.clear()
                refresh_task_list()
                status.config(
                    text=(
                        "Task queue stopped: a task is no longer in range "
                        "or available."
                    )
                )
                return
            active_task = task
            refresh_task_list()
        task = active_task
        if root.grab_current() not in (None, root):
            task_runner_job = root.after(150, process_task_queue)
            return
        task_started = True

        kind = task["kind"]
        x, y = task["position"] if task["position"] is not None else (None, None)
        if kind == "mine":
            if not start_mining(
                x, y, task["depth"], tool=task["tool"]
            ):
                finish_active_task()
        elif kind == "attack":
            attack_entity(x, y, task["target"])
            finish_active_task()
        elif kind == "place":
            place_selected(x, y, task["item"])
            finish_active_task()
        elif kind == "interact":
            if interact(x, y, task["depth"], task["target"]):
                finish_active_task(200)
            else:
                finish_active_task()
        elif kind == "use":
            success, message = use_item(task["item"], player_character)
            if success:
                refresh_display()
            status.config(text=message)
            finish_active_task()

    def placement_depth(x, y):
        return game_world.get_placement_depth(x, y, player_character.pos)

    def current_view_origin():
        player_x, player_y, _ = player_character.pos
        return (
            (player_x // viewport_width) * viewport_width,
            (player_y // viewport_height) * viewport_height,
        )

    def break_target(target_x, target_y, depth, tool):
        nonlocal selected_layer
        def finish_mining_task():
            if active_task is not None and active_task["kind"] == "mine":
                finish_active_task()

        if not in_interaction_range(target_x, target_y):
            status.config(text="That block is outside your 4-block interaction radius.")
            finish_mining_task()
            return
        player_pos = player_character.pos
        if abs(depth - player_pos[2]) > 4:
            status.config(text="You can mine only within 4 Y levels of your position.")
            finish_mining_task()
            return
        if not game_world.can_mine_target(
            target_x, target_y, depth, player_pos
        ):
            status.config(text="There is no open path to mine that block.")
            finish_mining_task()
            return
        try:
            target = game_world.break_block(target_x, target_y, depth, tool)
        except ValueError as error:
            status.config(text=str(error))
            finish_mining_task()
            return
        if target is None:
            status.config(text="There is no placeable object in that cell.")
            finish_mining_task()
            return
        player_character.add_to_inventory(target)
        message = f"Collected {target.name}."
        if (
            isinstance(target, block)
            and target.tool_type
            and isinstance(tool, toolItem)
            and tool.tool_type == target.tool_type
        ):
            for _ in range(tool.drop_bonus):
                player_character.add_to_inventory(
                    game_world.create_block(
                        target.preset_id, target.pos
                    )
                )
            if tool.durability <= 0:
                player_character.remove_from_inventory(tool)
                message = f"{tool.name} broke; collected {target.name}."
            else:
                message = (
                    f"Collected {target.name}; {tool.name} has "
                    f"{tool.durability} uses left."
                )
        player_x, player_y, player_depth = player_character.pos
        if (target_x, target_y) == (player_x, player_y) and depth > player_depth:
            game_world.resolve_fall(player_character)
            selected_layer = player_character.pos[2]
        refresh_display()
        status.config(text=message)
        finish_mining_task()

    def cancel_mining(message):
        clear_task_queue(message)

    def start_mining(target_x, target_y, depth, tool=None):
        nonlocal mining_state
        if not in_interaction_range(target_x, target_y):
            status.config(text="That block is outside your 4-block interaction radius.")
            return False
        player_pos = player_character.pos
        if (
            abs(depth - player_pos[2]) > 4
            or not game_world.can_mine_target(
                target_x, target_y, depth, player_pos
            )
        ):
            status.config(text="There is no open path to mine that block.")
            return False
        target = game_world.get_tile(target_x, target_y, depth)
        if target is None:
            status.config(text="There is no breakable object in that cell.")
            return False
        mining_state = {
            "position": (target_x, target_y, depth),
            "target": target,
            "tool": tool,
            "camera_layer": selected_layer,
            "started_at": time.monotonic(),
            "duration": game_world.get_break_duration(target, tool),
        }
        refresh_display()
        status.config(text=f"Mining {target.name}...")
        update_mining_progress()
        return True

    def update_mining_progress():
        nonlocal mining_state
        if mining_state is None:
            return
        target_x, target_y, depth = mining_state["position"]
        player_pos = player_character.pos
        current_target = game_world.get_tile(target_x, target_y, depth)
        if (
            current_target is not mining_state["target"]
            or selected_layer != mining_state["camera_layer"]
            or not in_interaction_range(target_x, target_y)
            or abs(depth - player_pos[2]) > 4
            or not game_world.can_mine_target(
                target_x, target_y, depth, player_pos
            )
        ):
            cancel_mining("Mining stopped: target is no longer reachable.")
            return
        if time.monotonic() - mining_state["started_at"] >= mining_state["duration"]:
            tool = mining_state["tool"]
            mining_state = None
            break_target(target_x, target_y, depth, tool)
            return
        refresh_display()
        status.config(text=f"Mining {current_target.name}...")
        root.after(50, update_mining_progress)

    def place_selected(target_x, target_y, item=None):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That space is outside your 4-block interaction radius.")
            return False
        item = selected_item() if item is None else item
        if not getattr(item, "placeable", False):
            status.config(text="Select a placeable item in your inventory first.")
            return False
        depth = placement_depth(target_x, target_y)
        if depth is None:
            status.config(
                text="You can place only on your Y level or the level below, "
                     "and both spaces are occupied."
            )
            return False
        if item not in player_character.inventory:
            status.config(text=f"{item.name} is no longer in your inventory.")
            return False
        placed_item = game_world.create_crafted_item(
            item_identifier(item), (target_x, target_y, depth)
        )
        game_world.set_tile(target_x, target_y, depth, placed_item)
        player_character.remove_from_inventory(item, 1)
        status.config(text=f"Placed {item.name}.")
        refresh_display()
        return True

    def open_chest(chest):
        dialog = tk.Toplevel(root)
        dialog.title(chest.name)
        dialog.transient(root)
        dialog.grab_set()
        chest_column = tk.LabelFrame(
            dialog,
            text=(
                "Cold-storage contents"
                if chest.item_id == "icebox" else "Chest contents"
            ),
            padx=8,
            pady=6,
        )
        chest_column.grid(row=0, column=0, padx=8, pady=8, sticky="n")
        player_column = tk.LabelFrame(dialog, text="Your inventory", padx=8, pady=6)
        player_column.grid(row=0, column=1, padx=8, pady=8, sticky="n")
        chest_search_var = tk.StringVar()
        player_search_var = tk.StringVar()
        tk.Label(chest_column, text="Search chest").pack(anchor="w")
        chest_search = tk.Entry(chest_column, textvariable=chest_search_var, width=24)
        chest_search.pack(fill="x", pady=(0, 5))
        tk.Label(player_column, text="Search inventory").pack(anchor="w")
        player_search = tk.Entry(player_column, textvariable=player_search_var, width=24)
        player_search.pack(fill="x", pady=(0, 5))
        chest_list = tk.Listbox(chest_column, width=24, height=10, exportselection=False)
        chest_list.pack()
        player_list = tk.Listbox(player_column, width=24, height=10, exportselection=False)
        player_list.pack()
        chest_visible = []
        player_visible = []

        def update_lists():
            update_spoilage_in_collection(
                chest.contents,
                game_world,
                cold=chest.item_id == "icebox",
                location=chest.pos,
            )
            update_spoilage_in_collection(
                player_character.inventory,
                game_world,
                location=player_character.pos,
            )
            chest_selection = chest_list.curselection()
            chest_selected = chest_visible[chest_selection[0]] if chest_selection else None
            player_selection = player_list.curselection()
            player_selected = player_visible[player_selection[0]] if player_selection else None
            chest_query = chest_search_var.get().strip().casefold()
            player_query = player_search_var.get().strip().casefold()
            chest_visible[:] = [
                item for item in chest.contents
                if chest_query in item.name.casefold()
                or chest_query in item.description.casefold()
            ]
            player_visible[:] = [
                item for item in player_character.inventory
                if player_query in item.name.casefold()
                or player_query in item.description.casefold()
            ]
            chest_list.delete(0, tk.END)
            player_list.delete(0, tk.END)
            for item in chest_visible:
                stack_count = f" x{item.count}" if item.stackable else ""
                chest_list.insert(
                    tk.END, f"{item.icon} {item.name}{stack_count}"
                )
            for item in player_visible:
                stack_count = f" x{item.count}" if item.stackable else ""
                player_list.insert(
                    tk.END, f"{item.icon} {item.name}{stack_count}"
                )
            if chest_selected in chest_visible:
                chest_list.selection_set(chest_visible.index(chest_selected))
            if player_selected in player_visible:
                player_list.selection_set(player_visible.index(player_selected))
            refresh_display()

        def transfer(source, destination, visible, listbox):
            selection = listbox.curselection()
            if not selection:
                status.config(text="Select an item to transfer.")
                return
            item = visible[selection[0]]
            source.remove(item)
            add_item_to_collection(destination, item)
            update_lists()

        chest_search.bind("<KeyRelease>", lambda _event: update_lists())
        player_search.bind("<KeyRelease>", lambda _event: update_lists())
        tk.Button(
            dialog, text="Take selected",
            command=lambda: transfer(
                chest.contents, player_character.inventory, chest_visible, chest_list
            ),
        ).grid(row=1, column=0, padx=8, pady=(0, 8))
        tk.Button(
            dialog, text="Store selected",
            command=lambda: transfer(
                player_character.inventory, chest.contents, player_visible, player_list
            ),
        ).grid(row=1, column=1, padx=8, pady=(0, 8))
        update_lists()

    def open_station(station):
        dialog = tk.Toplevel(root)
        dialog.title(station.name)
        dialog.transient(root)
        dialog.grab_set()
        if isinstance(station, craftingTable):
            station_id = "crafting_table"
        elif isinstance(station, campfire):
            station_id = "campfire"
        else:
            station_id = "furnace"
        recipes = [recipe for recipe in crafting_recipes if recipe["station"] == station_id]

        recipe_column = tk.Frame(dialog, padx=8, pady=8)
        recipe_column.grid(row=0, column=0, sticky="n")
        detail_column = tk.Frame(dialog, padx=8, pady=8)
        detail_column.grid(row=0, column=1, sticky="nsew")
        tk.Label(recipe_column, text="Search recipes").pack(anchor="w")
        recipe_search_var = tk.StringVar()
        recipe_search = tk.Entry(recipe_column, textvariable=recipe_search_var, width=32)
        recipe_search.pack(fill="x", pady=(2, 5))
        recipe_list = tk.Listbox(recipe_column, width=34, height=18, exportselection=False)
        recipe_list.pack(fill="both", expand=True)
        recipe_visible = []
        block_presets = load_block_presets()
        recipe_details = tk.Label(
            detail_column, text="Select a recipe to see its ingredients.",
            justify="left", anchor="nw", width=38, height=12, wraplength=260,
        )
        recipe_details.pack(fill="both", expand=True)
        station_status = tk.Label(detail_column, text="", justify="left", wraplength=260)
        station_status.pack(fill="x", pady=6)

        def selected_recipe():
            selection = recipe_list.curselection()
            return recipe_visible[selection[0]] if selection else None

        def format_ingredient(ingredient):
            item_id = ingredient["id"]
            preset = block_presets.get(item_id)
            name = preset["name"] if preset else item_id.replace("_", " ").title()
            return f"{ingredient['quantity']} x {name}"

        def update_recipe_details(_event=None):
            recipe = selected_recipe()
            if recipe is None:
                recipe_details.config(text="Select a recipe to see its ingredients.")
                return
            ingredients = "\n".join(
                format_ingredient(ingredient) for ingredient in recipe["ingredients"]
            )
            result = recipe["result"]
            recipe_details.config(
                text=(
                    f"{result['icon']} {result['name']} x{result['quantity']}\n\n"
                    f"{recipe.get('description', '')}\n\n"
                    f"Ingredients:\n{ingredients}"
                )
            )

        def update_recipe_list(_event=None):
            previous = selected_recipe()
            query = recipe_search_var.get().strip().casefold()
            recipe_visible[:] = [
                recipe for recipe in recipes
                if query in recipe["name"].casefold()
                or query in recipe["id"].casefold()
                or query in recipe["result"]["name"].casefold()
                or any(query in ingredient["id"].casefold()
                       for ingredient in recipe["ingredients"])
            ]
            recipe_list.delete(0, tk.END)
            for recipe in recipe_visible:
                recipe_list.insert(
                    tk.END,
                    f"{recipe['result']['icon']} {recipe['name']}",
                )
            if previous in recipe_visible:
                recipe_list.selection_set(recipe_visible.index(previous))
            elif recipe_visible:
                recipe_list.selection_set(0)
            update_recipe_details()

        def craft_selected():
            recipe = selected_recipe()
            if recipe is None:
                station_status.config(text="Select a recipe first.")
                return
            crafted, missing = craft_recipe(
                player_character.inventory, recipe, game_world
            )
            if missing:
                station_status.config(text="Missing: " + ", ".join(missing))
                return
            if not crafted:
                station_status.config(text="Unable to craft this recipe.")
                return
            result = recipe["result"]
            station_status.config(
                text=f"Crafted {result['quantity']} x {result['name']}."
            )
            refresh_display()
            update_recipe_list()

        recipe_list.bind("<<ListboxSelect>>", update_recipe_details)
        recipe_search.bind("<KeyRelease>", update_recipe_list)
        tk.Button(
            detail_column, text="Craft selected recipe", command=craft_selected
        ).pack(anchor="e", pady=(4, 0))
        update_recipe_list()

    def interact(target_x, target_y, target_depth=None, expected_target=None):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That object is outside your 4-block interaction radius.")
            return False
        target_depth = selected_layer if target_depth is None else target_depth
        target = game_world.get_tile(target_x, target_y, target_depth)
        if expected_target is not None and target is not expected_target:
            status.config(text="That object is no longer there.")
            return False
        if isinstance(target, door):
            target.toggle()
            refresh_display()
            status.config(
                text=f"{target.name} {'opened' if target.is_open else 'closed'}."
            )
            return True
        elif isinstance(target, storageObject):
            open_chest(target)
            return True
        elif isinstance(target, (craftingTable, furnace)):
            open_station(target)
            return True
        elif isinstance(target, restingPlace):
            restored = player_character.max_health - player_character.health
            limb_damage = any(
                value < player_character.max_health
                for value in player_character.limb_health.values()
            )
            player_character.heal_fully()
            status.config(
                text=(
                    f"Rested at {target.name}; restored {restored} health "
                    "and healed all body parts."
                )
                if restored or limb_damage
                else "Your health and limbs are already fully healed."
            )
            refresh_display()
            return True
        else:
            status.config(text="There is no usable object in that cell.")
            return False

    def attack_entity(target_x, target_y, expected_target=None):
        target = game_world.get_tile(
            target_x, target_y,
            selected_layer if expected_target is None else expected_target.pos[2],
        )
        if expected_target is not None and target is not expected_target:
            status.config(text="That creature is no longer there.")
            return False
        player_x, player_y, player_depth = player_character.pos
        if (
            not isinstance(target, entity)
            or target is player_character
            or target.pos[2] != player_depth
            or abs(target_x - player_x) + abs(target_y - player_y) > 1
        ):
            status.config(text="You must be next to a creature to attack it.")
            return False
        part_id, damage = target.take_damage(
            player_character.effective_attack_power
        )
        if part_id is None:
            status.config(text=f"{target.name} has no attached body parts.")
            return False
        part_label = dict(BODY_PARTS)[part_id]
        if target.health <= 0:
            game_world.set_tile(*target.pos, None)
            bounty = (
                max(1, target.max_health // 10)
                if target.disposition == "hostile" else 0
            )
            politics.wallet += bounty
            result = (
                f"You defeated {target.name} and earned {bounty} "
                f"{'coin' if bounty == 1 else 'coins'}. "
                f"Wallet: {politics.wallet}."
                if bounty else f"You defeated {target.name}."
            )
        else:
            result = (
                f"You hit {target.name}'s {part_label} for {damage} damage "
                f"({target.limb_health[part_id]}/{target.max_health} limb health)."
            )
        refresh_display()
        status.config(text=result)
        return True

    def show_interaction_menu(event, x, y):
        origin_x, origin_y = current_view_origin()
        x, y = origin_x + x, origin_y + y
        target = game_world.get_tile(x, y, selected_layer)
        menu = tk.Menu(root, tearoff=False)
        if not in_interaction_range(x, y):
            menu.add_command(label="Out of reach (4-block radius)", state="disabled")
        else:
            item_to_place = selected_item()
            has_placeable_item_selected = getattr(
                item_to_place, "placeable", False
            )
            mineable_targets = game_world.get_mineable_targets(
                x, y, selected_layer, player_character.pos
            )
            for target_depth, mineable_target in mineable_targets:
                if isinstance(mineable_target, entity):
                    continue
                menu.add_command(
                    label=(
                        f"Break {mineable_target.name} "
                        f"(Y level {world.Y_LEVEL_COUNT - target_depth})"
                    ),
                    command=lambda depth=target_depth, mine_target=mineable_target:
                    enqueue_task(
                        "mine", x, y, depth, mine_target
                    ),
                )
            if (
                has_placeable_item_selected
                and placement_depth(x, y) is not None
            ):
                menu.add_command(
                    label=f"Place {item_to_place.name}",
                    command=lambda item=item_to_place: enqueue_task(
                        "place", x, y, selected_layer, item=item
                    ),
                )
            if isinstance(target, door):
                menu.add_command(
                    label=f"{'Close' if target.is_open else 'Open'} {target.name}",
                    command=lambda obj=target: enqueue_task(
                        "interact", x, y, selected_layer, obj
                    ),
                )
            elif isinstance(target, entity) and target is not player_character:
                player_x, player_y, player_depth = player_character.pos
                if (
                    target.pos[2] == player_depth
                    and abs(x - player_x) + abs(y - player_y) <= 1
                ):
                    menu.add_command(
                        label=f"Attack {target.name}",
                        command=lambda creature=target: enqueue_task(
                            "attack", x, y, creature.pos[2], creature
                        ),
                    )
                else:
                    menu.add_command(
                        label="Creature is too far away to attack",
                        state="disabled",
                    )
            elif isinstance(target, storageObject):
                menu.add_command(
                    label="Open Chest",
                    command=lambda obj=target: enqueue_task(
                        "interact", x, y, selected_layer, obj
                    ),
                )
            elif isinstance(target, (craftingTable, furnace, restingPlace)):
                menu.add_command(
                    label=f"Use {target.name}",
                    command=lambda obj=target: enqueue_task(
                        "interact", x, y, selected_layer, obj
                    ),
                )
            selected = selected_item()
            if isinstance(selected, usableItem):
                menu.add_command(
                    label=f"Use {selected.name}",
                    command=lambda item=selected: enqueue_task(
                        "use", item=item
                    ),
                )
            if menu.index("end") is None:
                menu.add_command(label="No available interactions", state="disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_admin_panel():
        dialog = tk.Toplevel(root)
        dialog.title("Admin access")
        dialog.transient(root)
        dialog.resizable(False, False)
        tk.Label(dialog, text="Enter admin code:").pack(
            padx=12, pady=(12, 4), anchor="w"
        )
        code_var = tk.StringVar()
        code_entry = tk.Entry(dialog, textvariable=code_var, show="*")
        code_entry.pack(fill="x", padx=12)
        access_status = tk.Label(dialog, text="", anchor="w")
        access_status.pack(fill="x", padx=12, pady=(4, 8))
        code_entry.focus_set()

        def grant_access(_event=None):
            if code_var.get() != "1212":
                access_status.config(text="Incorrect admin code.")
                code_var.set("")
                return
            for child in dialog.winfo_children():
                child.destroy()
            dialog.title("Admin panel")
            dialog.geometry("440x620")

            tk.Label(dialog, text="Search blocks and items:").pack(
                padx=12, pady=(12, 4), anchor="w"
            )
            search_var = tk.StringVar()
            search_entry = tk.Entry(dialog, textvariable=search_var)
            search_entry.pack(fill="x", padx=12)
            item_list = tk.Listbox(dialog, height=10, exportselection=False)
            item_list.pack(fill="both", expand=True, padx=12, pady=(4, 8))
            quantity_row = tk.Frame(dialog)
            quantity_row.pack(fill="x", padx=12)
            tk.Label(quantity_row, text="Quantity:").pack(side="left")
            quantity_var = tk.StringVar(value="1")
            quantity_entry = tk.Entry(
                quantity_row, textvariable=quantity_var, width=8
            )
            quantity_entry.pack(side="left", padx=(6, 0))
            result_status = tk.Label(dialog, text="", anchor="w")
            result_status.pack(fill="x", padx=12, pady=8)

            tk.Label(
                dialog, text="Admin-only entity spawn eggs:"
            ).pack(padx=12, pady=(6, 4), anchor="w")
            entity_search_var = tk.StringVar()
            entity_search = tk.Entry(dialog, textvariable=entity_search_var)
            entity_search.pack(fill="x", padx=12)
            entity_list = tk.Listbox(
                dialog, height=7, exportselection=False
            )
            entity_list.pack(fill="x", padx=12, pady=(4, 8))
            entity_status = tk.Label(dialog, text="", anchor="w")
            entity_status.pack(fill="x", padx=12, pady=(0, 6))

            presets = load_item_presets()
            block_presets = load_block_presets()
            catalog = dict(presets)
            for block_id, preset in block_presets.items():
                catalog.setdefault(block_id, preset)
            ordered_catalog = sorted(
                catalog.items(), key=lambda entry: entry[1]["name"].casefold()
            )
            visible_catalog = []
            entity_presets = load_entity_presets()
            ordered_entities = sorted(
                entity_presets.items(),
                key=lambda entry: entry[1]["name"].casefold(),
            )
            visible_entities = []

            def update_catalog(_event=None):
                query = search_var.get().strip().casefold()
                visible_catalog[:] = [
                    (item_id, preset)
                    for item_id, preset in ordered_catalog
                    if query in preset["name"].casefold()
                    or query in item_id.casefold()
                    or query in preset["description"].casefold()
                ]
                item_list.delete(0, tk.END)
                for item_id, preset in visible_catalog:
                    item_list.insert(
                        tk.END, f"{preset['icon']} {preset['name']} [{item_id}]"
                    )
                if visible_catalog:
                    item_list.selection_set(0)

            def update_entity_list(_event=None):
                query = entity_search_var.get().strip().casefold()
                visible_entities[:] = [
                    (entity_id, preset)
                    for entity_id, preset in ordered_entities
                    if query in preset["name"].casefold()
                    or query in entity_id.casefold()
                    or query in preset["disposition"].casefold()
                ]
                entity_list.delete(0, tk.END)
                for entity_id, preset in visible_entities:
                    egg_name = f"{preset['name']} Spawn Egg"
                    entity_list.insert(
                        tk.END,
                        f"{preset['icon']} {egg_name} "
                        f"({preset['disposition']})",
                    )
                if visible_entities:
                    entity_list.selection_set(0)

            def grant_item():
                selection = item_list.curselection()
                if not selection:
                    result_status.config(text="Select an item first.")
                    return
                try:
                    quantity = int(quantity_var.get())
                except ValueError:
                    result_status.config(text="Quantity must be a positive whole number.")
                    return
                if quantity < 1:
                    result_status.config(text="Quantity must be at least 1.")
                    return
                item_id, preset = visible_catalog[selection[0]]
                for _ in range(quantity):
                    player_character.add_to_inventory(
                        game_world.create_crafted_item(item_id)
                    )
                refresh_display()
                result_status.config(
                    text=f"Granted {quantity} x {preset['name']}."
                )

            def spawn_selected_entity():
                selection = entity_list.curselection()
                if not selection:
                    entity_status.config(text="Select an entity spawn egg first.")
                    return
                try:
                    quantity = int(quantity_var.get())
                except ValueError:
                    entity_status.config(
                        text="Quantity must be a positive whole number."
                    )
                    return
                if quantity < 1:
                    entity_status.config(text="Quantity must be at least 1.")
                    return
                _entity_id, preset = visible_entities[selection[0]]
                spawned = 0
                for _ in range(quantity):
                    if game_world.spawn_entity(preset, player_character.pos) is None:
                        break
                    spawned += 1
                if spawned:
                    refresh_display()
                if spawned == quantity:
                    entity_status.config(
                        text=f"Spawned {spawned} x {preset['name']}."
                    )
                else:
                    entity_status.config(
                        text=f"Spawned {spawned} of {quantity}; "
                             "no nearby empty space remains."
                    )

            search_entry.bind("<KeyRelease>", update_catalog)
            entity_search.bind("<KeyRelease>", update_entity_list)
            tk.Button(
                dialog, text="Give selected item", command=grant_item
            ).pack(anchor="e", padx=12, pady=(0, 6))
            tk.Button(
                dialog, text="Use selected spawn egg",
                command=spawn_selected_entity,
            ).pack(anchor="e", padx=12, pady=(0, 12))
            item_list.bind("<Double-Button-1>", lambda _event: grant_item())
            entity_list.bind(
                "<Double-Button-1>",
                lambda _event: spawn_selected_entity(),
            )
            update_catalog()
            update_entity_list()
            search_entry.focus_set()

        tk.Button(dialog, text="Unlock", command=grant_access).pack(
            anchor="e", padx=12, pady=(0, 12)
        )
        code_entry.bind("<Return>", grant_access)

    def handle_movement(event):
        nonlocal selected_layer
        if event.keysym == "F4":
            open_admin_panel()
            return
        if event.keysym in ("1", "2", "3", "4", "5"):
            select_hotbar_slot(int(event.keysym) - 1)
            return
        if event.keysym == "f":
            use_selected_item()
            return
        movement = {
            "Up": (0, -1), "w": (0, -1),
            "Down": (0, 1), "s": (0, 1),
            "Left": (-1, 0), "a": (-1, 0),
            "Right": (1, 0), "d": (1, 0),
        }
        if event.keysym in movement:
            if event.state & 0x4 and event.keysym in ("Up", "Down"):
                change_player_y_level(-1 if event.keysym == "Up" else 1)
                return
            dx, dy = movement[event.keysym]
            previous_position = player_character.pos
            movement_result = game_world.move_player(player_character, dx, dy)
            if movement_result == "stumbled":
                status.config(text="Your injured legs make you stumble.")
                return
            if movement_result != "blocked":
                selected_layer = player_character.pos[2]
                refresh_display(player_moved_from=previous_position)
                if movement_result == "stepped":
                    status.config(text="You stepped up onto a block.")
                elif movement_result == "fell":
                    status.config(text="You fell until you reached solid ground.")
        elif event.keysym in ("bracketleft", "Prior"):
            change_layer(-1)
        elif event.keysym in ("bracketright", "Next"):
            change_layer(1)

    def update_entities():
        nonlocal entity_update_job, last_entity_update
        entity_update_job = None
        if not game_active:
            return
        now = time.monotonic()
        elapsed = max(0.0, now - last_entity_update)
        last_entity_update = now
        spoilage_state = tuple(
            (id(item), item.spoiled, round(item.spoil_remaining))
            for item in player_character.inventory
            if isinstance(item, bodyPartItem)
        )
        update_spoilage_in_collection(
            player_character.inventory,
            game_world,
            location=player_character.pos,
        )
        if spoilage_state != tuple(
            (id(item), item.spoiled, round(item.spoil_remaining))
            for item in player_character.inventory
            if isinstance(item, bodyPartItem)
        ):
            refresh_inventory()
        entity_changed = game_world.update_entity_ai(player_character)
        if player_character.infected:
            player_character.infection_elapsed += elapsed
            while (
                player_character.infection_elapsed
                >= INFECTION_DAMAGE_INTERVAL
                and player_character.health > 0
            ):
                player_character.hp -= 1
                player_character.infection_elapsed -= (
                    INFECTION_DAMAGE_INTERVAL
                )
                entity_changed = True
        else:
            player_character.infection_elapsed = 0.0
        trees_changed = game_world.grow_saplings()
        if entity_changed or trees_changed:
            refresh_display()
        entity_update_job = root.after(500, update_entities)

    def start_game():
        nonlocal game_active, last_entity_update
        menu_frame.pack_forget()
        main_frame.pack()
        root.bind("<KeyPress>", handle_movement)
        last_entity_update = time.monotonic()
        refresh_display()
        game_active = True
        if entity_update_job is None:
            update_entities()

    continue_button = tk.Button(
        menu_buttons,
        text="Continue",
        width=20,
        command=continue_game,
    )
    continue_button.pack(pady=5)
    continue_button.config(state="disabled")
    tk.Button(
        menu_buttons,
        text="Load Game",
        width=20,
        command=open_load_game_menu,
    ).pack(pady=5)
    tk.Button(
        menu_buttons,
        text="New Game",
        width=20,
        command=start_new_game,
    ).pack(pady=5)
    tk.Button(
        menu_buttons,
        text="Quit",
        width=20,
        command=root.destroy,
    ).pack(pady=5)
    tk.Label(
        menu_frame,
        text="Move: WASD / arrow keys    Change layer: scroll wheel",
        font=("Consolas", 9),
        foreground="#AAB6A5",
        background="#202B23",
    ).pack(pady=(24, 0))

    for index in range(5):
        button = tk.Button(
            hotbar_frame,
            text=f"{index + 1}: Empty",
            width=7,
            command=lambda slot=index: select_hotbar_slot(slot),
        )
        button.grid(row=0, column=index, padx=1)
        hotbar_buttons.append(button)
    tk.Button(
        hotbar_frame,
        text="Put selected inventory item in slot",
        command=assign_inventory_to_hotbar,
    ).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(5, 0))
    tk.Button(
        hotbar_frame,
        text="Use selected item",
        command=use_selected_item,
    ).grid(row=2, column=0, columnspan=5, sticky="ew", pady=(4, 0))

    inventory_list.bind(
        "<Button-3>",
        lambda _event: root.after_idle(assign_inventory_to_hotbar),
    )
    map_canvas.bind("<MouseWheel>", handle_layer_scroll)
    map_canvas.bind("<Button-4>", handle_layer_scroll)
    map_canvas.bind("<Button-5>", handle_layer_scroll)
    map_canvas.bind("<Motion>", handle_map_motion)
    map_canvas.bind("<Leave>", handle_map_leave)
    map_canvas.bind(
        "<Button-3>",
        lambda event: show_interaction_menu(
            event,
            event.x // cell_size,
            event.y // cell_size,
        ),
    )
    tk.Button(layer_buttons, text="Raise Y", command=lambda: change_layer(-1)).pack(
        side="left", expand=True, fill="x"
    )
    tk.Button(layer_buttons, text="Lower Y", command=lambda: change_layer(1)).pack(
        side="left", expand=True, fill="x", padx=(5, 0)
    )

    inventory_search.bind("<KeyRelease>", lambda _event: refresh_inventory())
    zoom_label.config(text=f"Zoom: {round(cell_size / 34 * 100)}%")
    build_map_grid()
    refresh_menu_save_buttons()
    root.mainloop()


if __name__ == "__main__":
    main()