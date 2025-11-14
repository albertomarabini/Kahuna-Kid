# 🧭 Project Goal: “Cub3D-lite (Python/pygame)”

Build a **first-person 3D maze viewer** using **ray-casting** (Wolf3D-style). Render a pseudo-3D view of a map, let the player move and rotate, and that’s it—**no enemies, pickups, doors, or sprites**.

---

## 🔧 Tech & Constraints

* **Language / Libs**: **Python 3.x** + **pygame 2.x**. No other third-party deps. ([PyPI][1])
* **Stability**: No unhandled exceptions; clean shutdown on quit/ESC.
* **Performance**: Target ~60 FPS; use delta-time movement so it feels consistent.

---

## 🧠 What You Gotta Build

A minimal ray-caster that:

* Renders vertical wall slices per ray (DDA or similar).
* Supports player **movement** (W/A/S/D), **rotation** (←/→), and **collision** against walls.
* **No gameplay systems** beyond navigation.

---

## 🖼️ Window Handling (pygame)

* Create a resizable window (e.g., **1024×768**).
* Respond to:

  * **Window close (QUIT/X)** → clean exit.
  * **ESC** → clean exit.
  * Window focus loss/gain should not crash; rendering can continue gracefully.

---

## 🎨 Graphics

* **Floor & ceiling**: flat colors from the config (RGB).
* **Walls** (pick one; both acceptable):

  * **MVP**: Solid color walls with simple **distance shading**.
  * **Optional**: Per-side **textures** (N/S/E/W). If any texture is missing, fall back to solid color for that side.
* **No sprites**, no minimap required (you may add an optional 2D debug minimap if you want—it’s not graded).

---

## 🎮 Controls

* **W A S D**: forward / strafe left / back / strafe right
* **← / →**: rotate view left/right
* **ESC** or window **X**: quit cleanly

Movement/rotation speeds should be tunable (e.g., constants like `MOVE_SPEED`, `ROT_SPEED`). Use a small collision radius to avoid clipping into walls.

---

## 📄 The `.cub` Map File (kept for compatibility, lightly relaxed)

Your program takes **one** `.cub` file path as argv[1]. It contains, in any order before the map:

### Textures (optional but supported)

```
NO ./path_to_the_north_texture
SO ./path_to_the_south_texture
WE ./path_to_the_west_texture
EA ./path_to_the_east_texture
```

If a texture path is present, you must attempt to load it; if missing or fails to load, use a solid color fallback.

### Colors (required)

```
F 220,100,0     # Floor RGB
C 225,30,0      # Ceiling RGB
```

* Each component **0–255**; three comma-separated ints.

### Map (required; after headers)

* Allowed chars:

  * `1` = wall
  * `0` = empty space
  * `N`, `S`, `E`, `W` = **single** player spawn + initial facing
  * Spaces are allowed; treat as **void/out-of-bounds** (not walkable)
* **Exactly one** spawn marker must exist.
* Map must be **fully enclosed** by walls; no leaks to void.

---

## ✅ Validation Rules (fail fast)

If invalid, exit with:

```
Error
<your message>
```

Cases that must trigger an error:

* Missing F/C color or invalid RGB format/range.
* Duplicate or missing player spawn.
* Texture path declared but file not found/unloadable (okay to continue with color fallback if you explicitly choose that route; otherwise error—pick one policy and document it).
* Map not enclosed (perform flood-fill or boundary checks).
* Unknown symbol in the map.

---

## 🧯 Runtime Behavior

* **Clean exit** on ESC/X (free surfaces, quit pygame).
* **No memory/resource leaks** beyond Python’s normal lifetime; don’t leak open files or leave threads.
* **Robustness**: window minimize/restore shouldn’t crash; frame timing should remain stable.

---

## 🗺️ Example `.cub`

(Identical structure to the original; textures optional. This is just a sample.)

```
NO ./path_to_the_north_texture
SO ./path_to_the_south_texture
WE ./path_to_the_west_texture
EA ./path_to_the_east_texture
F 220,100,0
C 225,30,0
        1111111111111111111111111
        1000000000110000000000001
        1011000001110000000000001
        1001000000000000000000001
111111111011000001110000000000001
100000000011000001110111111111111
11110111111111011100000010001
11110111111111011101010010001
11000000110101011100000010001
10000000000000001100000010001
10000000000000001101010010001
11000001110101011111011110N0111
11110111 1110101 101111010001
11111111 1111111 111111111111
```

---

## 🧱 Minimum Deliverables

* `main.py` that:

  * Parses argv for a `.cub` file.
  * Loads config (textures optional), colors, and map.
  * Validates and spawns the player.
  * Runs the render loop with ray-casting + controls.
* A short `README.md` with:

  * Install: `python -m pip install pygame`
  * Run: `python main.py <map.cub>` ([pygame.org][2])

---

## 🎯 Non-Goals (explicitly out)

* Enemies, doors, keys, pickups, shooting, HUD, sound, saving/loading, scripting.

---

## 🔎 Implementation Hints (non-binding)

* Use DDA stepping per ray, perpendicular wall distance for slice height, simple fisheye correction.
* For shading: scale wall color by 1/(1+distance) or a clamped factor.
* Keep FOV ~**60–66°**; tweak to taste.

