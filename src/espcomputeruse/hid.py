"""US keyboard layout -> standard USB HID usage IDs, also used by BLE HID."""

MODIFIERS = {"CTRL": 1, "SHIFT": 2, "ALT": 4, "GUI": 8, "META": 8, "CMD": 8}
SPECIAL = {"ENTER": 40, "ESC": 41, "BACKSPACE": 42, "TAB": 43, "SPACE": 44,
           "CAPSLOCK": 57, "PRINTSCREEN": 70, "INSERT": 73, "HOME": 74,
           "PAGEUP": 75, "DELETE": 76, "END": 77, "PAGEDOWN": 78,
           "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82}
SPECIAL.update({f"F{i}": 57+i for i in range(1, 13)})
BASE = "1234567890-=[]\\;\x27`,./"
SHIFTED = "!@#$%^&*()_+{}|:\"~<>?"
USAGES = list(range(30, 40)) + [45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56]


def character(c: str) -> tuple[int, int]:
    if "a" <= c <= "z":
        return 0, 4 + ord(c) - ord("a")
    if "A" <= c <= "Z":
        return 2, 4 + ord(c) - ord("A")
    if c in " \n\t":
        return 0, {" ": 44, "\n": 40, "\t": 43}[c]
    if c in BASE:
        return 0, USAGES[BASE.index(c)]
    if c in SHIFTED:
        return 2, USAGES[SHIFTED.index(c)]
    raise ValueError("type_text supports printable US ASCII plus newline/tab only; use explicit IME keystrokes for Chinese")


def text_reports(text: str) -> list[dict]:
    return [{"op": "key", "mod": mod, "keys": [usage]} for mod, usage in map(character, text)]


def chord(keys: list[str]) -> dict:
    mod, usages = 0, []
    for key in keys:
        key = key.upper()
        if key in MODIFIERS:
            mod |= MODIFIERS[key]
        elif key in SPECIAL:
            usages.append(SPECIAL[key])
        elif len(key) == 1:
            # Upper-case names denote physical keys; SHIFT must be explicit for chords.
            _, usage = character(key.lower())
            usages.append(usage)
        else:
            raise ValueError(f"Unknown key: {key}")
    if len(set(usages)) != len(usages):
        raise ValueError("duplicate keys")
    return {"op": "key", "mod": mod, "keys": usages}


def decode(mod: int, usage: int) -> str:
    """For the simulated target only."""
    if 4 <= usage <= 29:
        c = chr(usage - 4 + ord("a"))
        return c.upper() if mod & 2 else c
    if usage in USAGES:
        i = USAGES.index(usage)
        return SHIFTED[i] if mod & 2 else BASE[i]
    return {40: "\n", 43: "\t", 44: " "}.get(usage, "")
