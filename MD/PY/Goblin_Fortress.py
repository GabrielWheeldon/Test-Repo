import json
from pathlib import Path
import tkinter as tk


BLOCK_PRESETS_DIR = Path(__file__).resolve().parent / "blocks"
ITEM_PRESETS_DIR = Path(__file__).resolve().parent / "items"
CRAFTING_RECIPES_FILE = Path(__file__).resolve().parent / "recipes" / "recipes.json"


class gameObject:
    def __init__(self, name, icon, description, pos):
        self.name = name
        self.icon = icon
        self.description = description
        self.pos = pos

    def move(self, new_pos):
        self.pos = new_pos


class block(gameObject):
    def __init__(self, name, icon, description, pos, solid=True, breakable=True):
        super().__init__(name, icon, description, pos)
        self.solid = solid
        self.breakable = breakable
        self.placeable = True
        self.preset_id = None

    @classmethod
    def from_preset(cls, preset, pos):
        result = cls(
            preset["name"],
            preset["icon"],
            preset["description"],
            pos,
            preset.get("solid", True),
            preset.get("breakable", True),
        )
        result.preset_id = preset.get("id")
        return result


class craftedItem(gameObject):
    def __init__(self, item_id, name, icon, description, pos=(0, 0, 0), placeable=True):
        super().__init__(name, icon, description, pos)
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
        )


class storageObject(gameObject):
    def __init__(self, name, icon, description, pos, contents=None):
        super().__init__(name, icon, description, pos)
        self.contents = contents if contents is not None else []


class craftingTable(gameObject):
    pass


class furnace(gameObject):
    pass


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
        if not isinstance(preset.get("placeable"), bool):
            raise ValueError(f"{preset_path} must define a boolean 'placeable' field.")
        if preset.get("kind", "item") not in ("item", "block"):
            raise ValueError(f"{preset_path} kind must be 'item' or 'block'.")
        presets[preset_id] = preset
    return presets


def load_crafting_recipes():
    with CRAFTING_RECIPES_FILE.open(encoding="utf-8") as recipe_file:
        recipes = json.load(recipe_file)
    if not isinstance(recipes, list):
        raise ValueError(f"{CRAFTING_RECIPES_FILE} must contain a JSON list.")
    if len(recipes) != 50:
        raise ValueError(
            f"{CRAFTING_RECIPES_FILE} must contain exactly 50 crafting recipes."
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
        if recipe["station"] not in ("crafting_table", "furnace"):
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
    def __init__(self, name, icon, description, pos, health, attack_power):
        super().__init__(name, icon, description, pos)
        self.health = health
        self.attack_power = attack_power


class player(entity):
    def __init__(self, name, icon, description, pos, health, attack_power, inventory):
        super().__init__(name, icon, description, pos, health, attack_power)
        self.inventory = inventory

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

    def __init__(self):
        self.layers: list[list[list[gameObject | None]]] = []
        self.map: list[list[gameObject | None]] = []
        self.subsurface: list[list[list[gameObject | None]]] = []

    def generate_world(self, width: int, height: int):
        presets = load_block_presets()
        dirt_preset = presets["dirt"]
        self.layers = []
        for depth in range(self.Y_LEVEL_COUNT):
            layer = [
                [
                    block.from_preset(dirt_preset, (x, y, depth))
                    for x in range(width)
                ]
                for y in range(height)
            ]
            self.layers.append(layer)

        center_x, center_y = width // 2, height // 2
        if width >= 5 and height >= 2:
            self.layers[0][center_y][center_x - 2] = storageObject(
                "Chest", "C", "A chest for storing items.", (center_x - 2, center_y, 0)
            )
            self.layers[0][center_y][center_x - 1] = craftingTable(
                "Crafting Table", "T", "A table for crafting blocks.", (center_x - 1, center_y, 0)
            )
            self.layers[0][center_y][center_x + 1] = furnace(
                "Furnace", "F", "A furnace for smelting blocks.", (center_x + 1, center_y, 0)
            )

        self.map = self.layers[0]
        self.subsurface = self.layers[1:]

    def get_tile(self, x, y, depth):
        if not (0 <= depth < len(self.layers)):
            return None
        if not (0 <= y < len(self.layers[depth]) and 0 <= x < len(self.layers[depth][y])):
            return None
        return self.layers[depth][y][x]

    def set_tile(self, x, y, depth, tile):
        if not (0 <= depth < len(self.layers)):
            raise IndexError("Layer is outside the generated world.")
        if not (0 <= y < len(self.layers[depth]) and 0 <= x < len(self.layers[depth][y])):
            raise IndexError("Position is outside the generated world.")
        self.layers[depth][y][x] = tile

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
        return craftedItem.from_preset(item_preset, pos)

    def break_block(self, x, y, depth):
        target = self.get_tile(x, y, depth)
        if isinstance(target, craftedItem) and target.placeable:
            self.set_tile(x, y, depth, None)
            return target
        if not isinstance(target, block):
            return None
        if not target.breakable:
            raise ValueError(f"{target.name} cannot be broken.")
        self.set_tile(x, y, depth, None)
        return target

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

    status = tk.Label(main_frame, anchor="w", justify="left")
    status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    controls = tk.Label(
        main_frame,
        text="Move: arrows/WASD | Right-click a cell to interact (within 4 blocks) | Y level: [ / ]",
        anchor="w",
    )
    controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def selected_item():
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
            inventory_list.insert(tk.END, f"{item.icon} {item.name}")
        if selected in inventory_visible:
            inventory_list.selection_set(inventory_visible.index(selected))

    def refresh_display():
        player_x, player_y, player_depth = player_character.pos
        below_depth = selected_layer + 1
        y_level = world.Y_LEVEL_COUNT - selected_layer
        below_y_level = y_level - 1
        map_frame.config(
            text=(
                f"Y Level {y_level} over Y Level {below_y_level}"
                if below_depth < len(game_world.layers)
                else f"Y Level {y_level} (no Y level below)"
            )
        )
        layer_label.config(
            text=f"Looking at Y level: {y_level} (1-{world.Y_LEVEL_COUNT})"
        )
        for y in range(height):
            for x in range(width):
                tile = game_world.get_tile(x, y, selected_layer)
                is_player = (x, y, player_depth) == (player_x, player_y, selected_layer)
                below_tile = game_world.get_tile(x, y, below_depth)
                cell = cells[y][x]
                cell.delete("all")
                if below_tile is not None and tile is not None:
                    cell.create_text(
                        27, 27, text=below_tile.icon, fill="#888888",
                        font=("Consolas", 10),
                    )
                if is_player:
                    cell.create_text(
                        17, 16, text=player_character.icon, fill="#111111",
                        font=("Consolas", 16, "bold"),
                    )
                elif tile is not None:
                    cell.create_text(
                        15, 14, text=tile.icon, fill="#111111",
                        font=("Consolas", 14),
                    )
                elif below_tile is not None:
                    cell.create_text(
                        15, 14, text=below_tile.icon, fill="#555555",
                        font=("Consolas", 14),
                    )

        refresh_inventory()
        status.config(
            text=f"Position: ({player_x}, {player_y})  Health: {player_character.health}"
        )

    def change_layer(amount):
        nonlocal selected_layer
        selected_layer = max(0, min(len(game_world.layers) - 1, selected_layer + amount))
        x, y, _ = player_character.pos
        player_character.move((x, y, selected_layer))
        refresh_display()

    def in_interaction_range(x, y):
        player_x, player_y, _ = player_character.pos
        return (x - player_x) ** 2 + (y - player_y) ** 2 <= 4 ** 2

    def break_target(target_x, target_y):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That block is outside your 4-block interaction radius.")
            return
        _, _, depth = player_character.pos
        try:
            target = game_world.break_block(target_x, target_y, depth)
        except ValueError as error:
            status.config(text=str(error))
            return
        if target is None:
            status.config(text="There is no placeable object in that cell.")
            return
        player_character.add_to_inventory(target)
        status.config(text=f"Collected {target.name}.")
        refresh_display()

    def place_selected(target_x, target_y):
        if not in_interaction_range(target_x, target_y):
            status.config(text="That space is outside your 4-block interaction radius.")
            return
        item = selected_item()
        if not isinstance(item, (block, craftedItem)) or not item.placeable:
            status.config(text="Select a placeable item in your inventory first.")
            return
        _, _, depth = player_character.pos
        if (target_x, target_y) == player_character.pos[:2]:
            status.config(text="You cannot place a block where you are standing.")
            return
        if game_world.get_tile(target_x, target_y, depth) is not None:
            status.config(text="That space is occupied.")
            return
        item_id = item_identifier(item)
        placed_item = game_world.create_crafted_item(
            item_id, (target_x, target_y, depth)
        )
        game_world.set_tile(target_x, target_y, depth, placed_item)
        player_character.remove_from_inventory(item)
        status.config(text=f"Placed {item.name}.")
        refresh_display()

    def open_chest(chest):
        dialog = tk.Toplevel(root)
        dialog.title("Chest")
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
        station_id = "crafting_table" if isinstance(station, craftingTable) else "furnace"
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
        _, _, depth = player_character.pos
        target = game_world.get_tile(target_x, target_y, depth)
        if isinstance(target, storageObject):
            open_chest(target)
        elif isinstance(target, (craftingTable, furnace)):
            open_station(target)
        else:
            status.config(text="There is no chest or workstation in that cell.")

    def show_interaction_menu(event, x, y):
        _, _, depth = player_character.pos
        target = game_world.get_tile(x, y, depth)
        menu = tk.Menu(root, tearoff=False)
        if not in_interaction_range(x, y):
            menu.add_command(label="Out of reach (4-block radius)", state="disabled")
        else:
            item_to_place = selected_item()
            has_placeable_item_selected = (
                isinstance(item_to_place, (block, craftedItem))
                and item_to_place.placeable
            )
            if isinstance(target, block) or (
                isinstance(target, craftedItem) and target.placeable
            ):
                menu.add_command(
                    label=f"Break {target.name}",
                    command=lambda: break_target(x, y),
                    state=(
                        "normal"
                        if not isinstance(target, block) or target.breakable
                        else "disabled"
                    ),
                )
            if target is None and has_placeable_item_selected:
                menu.add_command(
                    label=f"Place {item_to_place.name}",
                    command=lambda: place_selected(x, y),
                )
            if isinstance(target, storageObject):
                menu.add_command(
                    label="Open Chest", command=lambda: open_chest(target)
                )
            elif isinstance(target, (craftingTable, furnace)):
                menu.add_command(
                    label=f"Use {target.name}",
                    command=lambda: interact(x, y),
                )
            if menu.index("end") is None:
                menu.add_command(label="No available interactions", state="disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def handle_movement(event):
        movement = {
            "Up": (0, -1), "w": (0, -1),
            "Down": (0, 1), "s": (0, 1),
            "Left": (-1, 0), "a": (-1, 0),
            "Right": (1, 0), "d": (1, 0),
        }
        if event.keysym in movement:
            dx, dy = movement[event.keysym]
            x, y, depth = player_character.pos
            new_x = max(0, min(width - 1, x + dx))
            new_y = max(0, min(height - 1, y + dy))
            player_character.move((new_x, new_y, depth))
            refresh_display()
        elif event.keysym in ("bracketleft", "Prior"):
            change_layer(-1)
        elif event.keysym in ("bracketright", "Next"):
            change_layer(1)

    for y, row in enumerate(cells):
        for x, cell in enumerate(row):
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