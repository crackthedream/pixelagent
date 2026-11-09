"""Display names are text; file names are derived from them safely (review finding F04).

A character called 'Knight/Paladin', 'Mage:Blue', '../x' or 'CON' must never break an export or write outside its
folder. Letters of any script, digits, '-' and '_' are kept; everything else becomes '_'."""
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name, fallback="item", max_len=60):
    s = "".join(ch if (ch.isalnum() or ch in "-_") else "_" if not ch.isspace() else " " for ch in str(name or ""))
    s = "_".join(s.split())                       # spaces (and runs of them) become one '_'
    s = s.strip("._-")[:max_len].rstrip("._-")    # no leading/trailing dots: Windows trims them; no hidden names
    if not s:
        return fallback
    if s.split(".")[0].upper() in _RESERVED:      # CON, NUL, COM1... are devices on Windows, not files
        s += "_"
    return s
