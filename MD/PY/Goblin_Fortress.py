import json
from collections import deque
from pathlib import Path
import time
import tkinter as tk


BLOCK_PRESETS_DIR = Path(__file__).resolve().parent / "blocks"
ITEM_PRESETS_DIR = Path(__file__).resolve().parent / "items"
CRAFTING_RECIPES_FILE = Path(__file__).resolve().parent / "recipes" / "recipes.json"
DEFAULT_FOREGROUND_COLOR = "#222222"
DEFAULT_BLOCK_BACKGROUND = "#8B5A2B"
ITEM_FOREGROUND_PALETTE = (
    "#8C3B2A", "#A34F18", "#866116", "#4C7624",
    "#176B55", "#176C83", "#3459A6", "#63469A",
    "#923F78", "#8B3948", "#585858", "#316E9A",
)


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


def color_at_depth(color, depth, player_depth):
    depth_offset = depth - player_depth
    if depth_offset == 0:
        brightness = 1.12
    elif depth_offset > 0:
        brightness = max(0.2, 0.82 ** (depth_offset - 1))
    else:
        brightness = max(0.2, 0.82 ** abs(depth_offset))
    return scale_color(color, brightness)


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
        self.preset_id = None
        self.tool_type = tool_type
        self.background_color = background_color
        self.break_time = break_time

    @classmethod
    def from_preset(cls, preset, pos):
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
        return result


class craftedItem(gameObject):
    def __init__(
        self, item_id, name, icon, description, pos=(0, 0, 0), placeable=True,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.item_id = item_id
        self.placeable = placeable

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
        )


class toolItem(craftedItem):
    def __init__(
        self, item_id, name, icon, description, pos, tool_type, durability,
        drop_bonus, mining_speed=2.0,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        super().__init__(item_id, name, icon, description, pos, False, foreground_color)
        self.tool_type = tool_type
        self.durability = durability
        self.drop_bonus = drop_bonus
        self.mining_speed = mining_speed


class usableItem(craftedItem):
    def __init__(
        self, item_id, name, icon, description, pos, heal_amount,
        foreground_color=DEFAULT_FOREGROUND_COLOR,
    ):
        super().__init__(item_id, name, icon, description, pos, False, foreground_color)
        self.heal_amount = heal_amount


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
        if preset.get("kind", "item") not in (
            "item", "block", "tool", "usable", "storage",
            "crafting_station", "furnace", "campfire", "resting_place",
        ):
            raise ValueError(f"{preset_path} has an unsupported item kind.")
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


def use_item(item, player_character):
    if not isinstance(item, usableItem):
        return False, "That item cannot be used directly."
    missing_health = player_character.max_health - player_character.health
    if missing_health <= 0:
        return False, "Your health is already full."
    restored = min(item.heal_amount, missing_health)
    player_character.health += restored
    player_character.remove_from_inventory(item)
    return True, f"Used {item.name}; restored {restored} health."


def craft_recipe(inventory, recipe, game_world):
    required = {}
    for ingredient in recipe["ingredients"]:
        item_id = ingredient["id"]
        required[item_id] = required.get(item_id, 0) + ingredient["quantity"]

    available = {}
    for inventory_item in inventory:
        item_id = item_identifier(inventory_item)
        available[item_id] = available.get(item_id, 0) + 1
    missing = [
        f"{quantity - available.get(item_id, 0)} more {item_id.replace('_', ' ')}"
        for item_id, quantity in required.items()
        if available.get(item_id, 0) < quantity
    ]
    if missing:
        return False, missing

    result = recipe["result"]
    crafted_items = [
        game_world.create_crafting_result(result)
        for _ in range(result["quantity"])
    ]
    for item_id, quantity in required.items():
        for _ in range(quantity):
            ingredient_item = next(
                item for item in inventory if item_identifier(item) == item_id
            )
            inventory.remove(ingredient_item)
    inventory.extend(crafted_items)
    return True, []


class entity(gameObject):
    def __init__(
        self, name, icon, description, pos, health, attack_power,
        foreground_color="#D4A72C",
    ):
        super().__init__(name, icon, description, pos, foreground_color)
        self.health = health
        self.attack_power = attack_power


class player(entity):
    def __init__(self, name, icon, description, pos, health, attack_power, inventory):
        super().__init__(name, icon, description, pos, health, attack_power)
        self.inventory = inventory
        self.max_health = health

    def add_to_inventory(self, item):
        self.inventory.append(item)

    def remove_from_inventory(self, item):
        if item in self.inventory:
            self.inventory.remove(item)

    def display_inventory(self):
        if not self.inventory:
            print("Your inventory is empty.")
        else:
            print("Inventory:")
            for item in self.inventory:
                print(f"- {item.name}: {item.description}")


class world:
    Y_LEVEL_COUNT = 10
    VISIBLE_DEPTHS_BELOW = 6

    def __init__(self):
        self.layers: list[list[list[gameObject | None]]] = []
        self.map: list[list[gameObject | None]] = []
        self.subsurface: list[list[list[gameObject | None]]] = []
        self.chunks: dict[
            tuple[int, int], list[list[list[gameObject | None]]]
        ] = {}
        self.chunk_width = 0
        self.chunk_height = 0
        self.active_chunk = (0, 0)

    def generate_world(self, width: int, height: int):
        if width < 1 or height < 1:
            raise ValueError("Chunk width and height must be positive.")
        self.chunk_width = width
        self.chunk_height = height
        self.chunks.clear()
        self.active_chunk = (0, 0)
        self.layers = self._generate_chunk(0, 0)
        self.map = self.layers[0]
        self.subsurface = self.layers[1:]
        self.chunks[(0, 0)] = self.layers

    def _generate_chunk(self, chunk_x: int, chunk_y: int):
        dirt_preset = load_block_presets()["dirt"]
        origin_x = chunk_x * self.chunk_width
        origin_y = chunk_y * self.chunk_height
        chunk_layers = []
        for depth in range(self.Y_LEVEL_COUNT):
            layer = []
            for local_y in range(self.chunk_height):
                row = []
                for local_x in range(self.chunk_width):
                    global_x = origin_x + local_x
                    global_y = origin_y + local_y
                    tile = (
                        None
                        if depth == 0
                        else block.from_preset(
                            dirt_preset, (global_x, global_y, depth)
                        )
                    )
                    row.append(tile)
                layer.append(row)
            chunk_layers.append(layer)

        if (chunk_x, chunk_y) == (0, 0) and self.chunk_width >= 5 and self.chunk_height >= 2:
            center_x, center_y = self.chunk_width // 2, self.chunk_height // 2
            chunk_layers[0][center_y][center_x - 2] = storageObject(
                "Chest", "C", "A chest for storing items.",
                (center_x - 2, center_y, 0), item_id="chest",
                background_color="#573714",
            )
            chunk_layers[0][center_y][center_x - 1] = craftingTable(
                "Crafting Table", "T", "A table for crafting blocks.",
                (center_x - 1, center_y, 0),
                background_color="#54351F",
            )
            chunk_layers[0][center_y][center_x + 1] = furnace(
                "Furnace", "F", "A furnace for smelting blocks.",
                (center_x + 1, center_y, 0),
                background_color="#303944",
            )
        return chunk_layers

    def load_chunk(self, chunk_x: int, chunk_y: int):
        chunk_key = (chunk_x, chunk_y)
        if chunk_key not in self.chunks:
            self.chunks[chunk_key] = self._generate_chunk(chunk_x, chunk_y)
        self.active_chunk = chunk_key
        self.layers = self.chunks[chunk_key]
        self.map = self.layers[0]
        self.subsurface = self.layers[1:]
        return self.layers

    def _chunk_location(self, x: int, y: int):
        chunk_x, local_x = divmod(x, self.chunk_width)
        chunk_y, local_y = divmod(y, self.chunk_height)
        return chunk_x, chunk_y, local_x, local_y

    @staticmethod
    def is_solid(tile):
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
        x, y, depth = entity.pos
        target_x = x + dx
        target_y = y + dy
        target_tile = self.get_tile(target_x, target_y, depth)
        stepped_up = False
        if self.is_solid(target_tile):
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
            self.chunks[chunk_key] = self._generate_chunk(chunk_x, chunk_y)
        chunk_layers = self.chunks[chunk_key]
        return chunk_layers[depth][local_y][local_x]

    def get_visible_tiles(self, x, y, depth):
        return [
            (visible_depth, tile)
            for visible_depth in range(
                depth,
                min(self.Y_LEVEL_COUNT, depth + self.VISIBLE_DEPTHS_BELOW + 1),
            )
            if (tile := self.get_tile(x, y, visible_depth)) is not None
        ]

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
            self.chunks[chunk_key] = self._generate_chunk(chunk_x, chunk_y)
        chunk_layers = self.chunks[chunk_key]
        chunk_layers[depth][local_y][local_x] = tile

    def create_block(self, preset_id, pos):
        presets = load_block_presets()
        if preset_id not in presets:
            raise KeyError(f"Unknown block preset: {preset_id}")
        return block.from_preset(presets[preset_id], pos)

    def create_crafting_result(self, result):
        preset_id = result["id"] if isinstance(result, dict) else result
        return self.create_crafted_item(preset_id)

    def create_crafted_item(self, item_id, pos=(0, 0, 0)):
        item_presets = load_item_presets()
        item_preset = item_presets.get(item_id)
        if item_preset is None:
            block_presets = load_block_presets()
            if item_id not in block_presets:
                raise KeyError(f"Unknown crafted item preset: {item_id}")
            return block.from_preset(block_presets[item_id], pos)
        if item_preset.get("kind") == "block":
            block_presets = load_block_presets()
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

def main():
    game_world = world()
    width, height = 10, 10
    game_world.generate_world(width, height)
    crafting_recipes = load_crafting_recipes()
    player_x, player_y = width // 2 - 1, height // 2 - 1
    player_character = player(
        "Player", "@", "The adventurer.", (player_x, player_y, 0), 100, 10, []
    )
    selected_layer = 0

    root = tk.Tk()
    root.title("Goblin Fortress")
    root.resizable(False, False)

    main_frame = tk.Frame(root, padx=12, pady=12)
    main_frame.pack()
    map_column = tk.Frame(main_frame)
    map_column.grid(row=0, column=0, sticky="n")
    right_panel = tk.Frame(main_frame)
    right_panel.grid(row=0, column=1, padx=(12, 0), sticky="n")

    map_frame = tk.LabelFrame(map_column, text="World", padx=8, pady=8)
    map_frame.pack(anchor="w")
    cells = []
    for y in range(height):
        row = []
        for x in range(width):
            cell = tk.Canvas(
                map_frame, width=34, height=34, highlightthickness=1,
                highlightbackground="#888888", background="#f0f0f0",
            )
            cell.grid(row=y, column=x)
            row.append(cell)
        cells.append(row)

    layer_frame = tk.LabelFrame(right_panel, text="Y Level", padx=8, pady=8)
    layer_frame.pack(fill="x")
    layer_label = tk.Label(layer_frame, anchor="w")
    layer_label.pack(fill="x")
    layer_buttons = tk.Frame(layer_frame)
    layer_buttons.pack(fill="x", pady=(5, 0))

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
    inventory_visible = []
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
            "Right-click inventory item to assign"
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
            label = f"{index + 1}: {item.icon} {item.name}" if item else f"{index + 1}: Empty"
            button.config(
                text=label,
                relief="sunken" if index == selected_hotbar_slot else "raised",
            )

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
        success, message = use_item(item, player_character)
        if success:
            refresh_display()
        status.config(text=message)

    def selected_item_from_inventory():
        selection = inventory_list.curselection()
        return inventory_visible[selection[0]] if selection else None

    def refresh_inventory():
        selection = inventory_list.curselection()
        selected = inventory_visible[selection[0]] if selection else None
        query = inventory_search_var.get().strip().casefold()
        inventory_visible[:] = [
            item for item in player_character.inventory
            if query in item.name.casefold() or query in item.description.casefold()
        ]
        inventory_list.delete(0, tk.END)
        for item in inventory_visible:
            durability = (
                f" ({item.durability} uses)"
                if isinstance(item, toolItem) else ""
            )
            inventory_list.insert(tk.END, f"{item.icon} {item.name}{durability}")
        if selected in inventory_visible:
            inventory_list.selection_set(inventory_visible.index(selected))
        refresh_hotbar()

    def set_hovered_cell(x, y):
        nonlocal hovered_cell
        hovered_cell = (x, y)
        update_hovered_name()

    def clear_hovered_cell(x, y):
        nonlocal hovered_cell
        if hovered_cell == (x, y):
            hovered_cell = None
            update_hovered_name()

    def update_hovered_name():
        if hovered_cell is None:
            hovered_name.config(text="Hover over a map tile")
            return

        origin_x, origin_y = current_chunk_origin()
        cell_x, cell_y = hovered_cell
        x, y = origin_x + cell_x, origin_y + cell_y
        player_x, player_y, player_depth = player_character.pos
        if (x, y) == (player_x, player_y) and player_depth == selected_layer:
            hovered_name.config(text=f"@ {player_character.name}")
            return

        visible_tiles = game_world.get_visible_tiles(x, y, selected_layer)
        hovered_name.config(
            text=visible_tiles[0][1].name if visible_tiles else "Air"
        )

    def refresh_display():
        player_x, player_y, player_depth = player_character.pos
        game_world.load_chunk(
            player_x // game_world.chunk_width,
            player_y // game_world.chunk_height,
        )
        origin_x, origin_y = current_chunk_origin()
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
        for y in range(height):
            for x in range(width):
                world_x, world_y = origin_x + x, origin_y + y
                visible_tiles = game_world.get_visible_tiles(
                    world_x, world_y, selected_layer
                )
                tile = visible_tiles[0][1] if visible_tiles else None
                is_player = (world_x, world_y, selected_layer) == player_character.pos
                cell = cells[y][x]
                cell.delete("all")
                cell.configure(background="#000000")
                if hasattr(tile, "background_color"):
                    cell.create_rectangle(
                        0, 0, 34, 34,
                        fill=color_at_depth(
                            tile.background_color,
                            visible_tiles[0][0],
                            player_depth,
                        ),
                        outline="",
                    )
                if is_player:
                    cell.create_text(
                        17, 16, text=player_character.icon,
                        fill=color_at_depth(
                            player_character.foreground_color,
                            player_depth,
                            player_depth,
                        ),
                        font=("Consolas", 16, "bold"),
                    )
                elif tile is not None:
                    cell.create_text(
                        15, 14, text=tile.icon,
                        fill=color_at_depth(
                            tile.foreground_color,
                            visible_tiles[0][0],
                            player_depth,
                        ),
                        font=("Consolas", 14),
                    )
                if (
                    mining_state is not None
                    and (world_x, world_y)
                    == mining_state["position"][:2]
                ):
                    progress = min(
                        1.0,
                        (time.monotonic() - mining_state["started_at"])
                        / mining_state["duration"],
                    )
                    cell.create_rectangle(
                        1, 1, 33, 5, fill="#202020", outline="#FFFFFF"
                    )
                    if progress > 0:
                        cell.create_rectangle(
                            2, 2, 2 + round(30 * progress), 4,
                            fill="#52D273", outline="",
                        )

        refresh_inventory()
        update_hovered_name()
        status.config(
            text=f"Position: ({player_x}, {player_y})  Health: {player_character.health}"
        )

    def change_layer(amount):
        nonlocal selected_layer
        selected_layer = max(
            0, min(world.Y_LEVEL_COUNT - 1, selected_layer + amount)
        )
        refresh_display()

    def handle_layer_scroll(event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            change_layer(-1)
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            change_layer(1)
        return "break"

    def change_player_y_level(amount):
        nonlocal selected_layer
        if game_world.move_player_y(player_character, amount):
            selected_layer = player_character.pos[2]
            refresh_display()

    def in_interaction_range(x, y):
        player_x, player_y, _ = player_character.pos
        return (x - player_x) ** 2 + (y - player_y) ** 2 <= 4 ** 2

    def placement_depth(x, y):
        return game_world.get_placement_depth(x, y, player_character.pos)

    def current_chunk_origin():
        player_x, player_y, _ = player_character.pos
        return (
            (player_x // game_world.chunk_width) * game_world.chunk_width,
            (player_y // game_world.chunk_height) * game_world.chunk_height,
        )

    def break_target(target_x, target_y, depth, tool):
        nonlocal selected_layer
        if not in_interaction_range(target_x, target_y):
            status.config(text="That block is outside your 4-block interaction radius.")
            return
        player_pos = player_character.pos
        if abs(depth - player_pos[2]) > 4:
            status.config(text="You can mine only within 4 Y levels of your position.")
            return
        if not game_world.can_mine_target(
            target_x, target_y, depth, player_pos
        ):
            status.config(text="There is no open path to mine that block.")
            return
        try:
            target = game_world.break_block(target_x, target_y, depth, tool)
        except ValueError as error:
            status.config(text=str(error))
            return
        if target is None:
            status.config(text="There is no placeable object in that cell.")
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

    def cancel_mining(message):
        nonlocal mining_state
        mining_state = None
        refresh_display()
        status.config(text=message)

    def start_mining(target_x, target_y, depth):
        nonlocal mining_state
        if not in_interaction_range(target_x, target_y):
            status.config(text="That block is outside your 4-block interaction radius.")
            return
        depth = selected_layer
        player_pos = player_character.pos
        if (
            abs(depth - player_pos[2]) > 4
            or not game_world.can_mine_target(
                target_x, target_y, depth, player_pos
            )
        ):
            status.config(text="There is no open path to mine that block.")
            return
        target = game_world.get_tile(target_x, target_y, depth)
        if target is None:
            status.config(text="There is no breakable object in that cell.")
            return
        tool = selected_item()
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

    def place_selected(target_x, target_y):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That space is outside your 4-block interaction radius.")
            return
        item = selected_item()
        if not getattr(item, "placeable", False):
            status.config(text="Select a placeable item in your inventory first.")
            return
        depth = placement_depth(target_x, target_y)
        if depth is None:
            status.config(
                text="You can place only on your Y level or the level below, "
                     "and both spaces are occupied."
            )
            return
        item.move((target_x, target_y, depth))
        game_world.set_tile(target_x, target_y, depth, item)
        player_character.remove_from_inventory(item)
        status.config(text=f"Placed {item.name}.")
        refresh_display()

    def open_chest(chest):
        dialog = tk.Toplevel(root)
        dialog.title(chest.name)
        dialog.transient(root)
        dialog.grab_set()
        chest_column = tk.LabelFrame(dialog, text="Chest contents", padx=8, pady=6)
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
                chest_list.insert(tk.END, f"{item.icon} {item.name}")
            for item in player_visible:
                player_list.insert(tk.END, f"{item.icon} {item.name}")
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
            destination.append(item)
            source.remove(item)
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

    def interact(target_x, target_y):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That object is outside your 4-block interaction radius.")
            return
        target = game_world.get_tile(target_x, target_y, selected_layer)
        if isinstance(target, storageObject):
            open_chest(target)
        elif isinstance(target, (craftingTable, furnace)):
            open_station(target)
        elif isinstance(target, restingPlace):
            restored = player_character.max_health - player_character.health
            player_character.health = player_character.max_health
            status.config(
                text=f"Rested at {target.name}; restored {restored} health."
                if restored else "Your health is already full."
            )
            refresh_display()
        else:
            status.config(text="There is no usable object in that cell.")

    def show_interaction_menu(event, x, y):
        origin_x, origin_y = current_chunk_origin()
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
                menu.add_command(
                    label=(
                        f"Break {mineable_target.name} "
                        f"(Y level {world.Y_LEVEL_COUNT - target_depth})"
                    ),
                    command=lambda depth=target_depth: start_mining(
                        x, y, depth
                    ),
                )
            if (
                has_placeable_item_selected
                and placement_depth(x, y) is not None
            ):
                menu.add_command(
                    label=f"Place {item_to_place.name}",
                    command=lambda: place_selected(x, y),
                )
            if isinstance(target, storageObject):
                menu.add_command(
                    label="Open Chest", command=lambda: open_chest(target)
                )
            elif isinstance(target, (craftingTable, furnace, restingPlace)):
                menu.add_command(
                    label=f"Use {target.name}",
                    command=lambda: interact(x, y),
                )
            selected = selected_item()
            if isinstance(selected, usableItem):
                menu.add_command(
                    label=f"Use {selected.name}",
                    command=use_selected_item,
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
            dialog.title("Admin item panel")
            dialog.geometry("420x420")

            tk.Label(dialog, text="Search blocks and items:").pack(
                padx=12, pady=(12, 4), anchor="w"
            )
            search_var = tk.StringVar()
            search_entry = tk.Entry(dialog, textvariable=search_var)
            search_entry.pack(fill="x", padx=12)
            item_list = tk.Listbox(dialog, height=14, exportselection=False)
            item_list.pack(fill="both", expand=True, padx=12, pady=8)
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
            presets = load_item_presets()
            block_presets = load_block_presets()
            catalog = dict(presets)
            for block_id, preset in block_presets.items():
                catalog.setdefault(block_id, preset)
            ordered_catalog = sorted(
                catalog.items(), key=lambda entry: entry[1]["name"].casefold()
            )
            visible_catalog = []

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

            search_entry.bind("<KeyRelease>", update_catalog)
            tk.Button(
                dialog, text="Give selected item", command=grant_item
            ).pack(anchor="e", padx=12, pady=(0, 12))
            item_list.bind("<Double-Button-1>", lambda _event: grant_item())
            update_catalog()
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
            movement_result = game_world.move_player(player_character, dx, dy)
            if movement_result != "blocked":
                selected_layer = player_character.pos[2]
                refresh_display()
                if movement_result == "stepped":
                    status.config(text="You stepped up onto a block.")
                elif movement_result == "fell":
                    status.config(text="You fell until you reached solid ground.")
        elif event.keysym in ("bracketleft", "Prior"):
            change_layer(-1)
        elif event.keysym in ("bracketright", "Next"):
            change_layer(1)

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
    for y, row in enumerate(cells):
        for x, cell in enumerate(row):
            cell.bind("<MouseWheel>", handle_layer_scroll)
            cell.bind("<Button-4>", handle_layer_scroll)
            cell.bind("<Button-5>", handle_layer_scroll)
            cell.bind(
                "<Enter>",
                lambda _event, cell_x=x, cell_y=y: set_hovered_cell(cell_x, cell_y),
            )
            cell.bind(
                "<Leave>",
                lambda _event, cell_x=x, cell_y=y: clear_hovered_cell(cell_x, cell_y),
            )
            cell.bind(
                "<Button-3>",
                lambda event, cell_x=x, cell_y=y: show_interaction_menu(
                    event, cell_x, cell_y
                ),
            )

    tk.Button(layer_buttons, text="Raise Y", command=lambda: change_layer(-1)).pack(
        side="left", expand=True, fill="x"
    )
    tk.Button(layer_buttons, text="Lower Y", command=lambda: change_layer(1)).pack(
        side="left", expand=True, fill="x", padx=(5, 0)
    )

    root.bind("<KeyPress>", handle_movement)
    inventory_search.bind("<KeyRelease>", lambda _event: refresh_inventory())
    refresh_display()
    root.mainloop()


if __name__ == "__main__":
    main()