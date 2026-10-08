"""The movie-resolution values every sup stores in the camera's settings
(user decision 2026-10-07: "A", one fixed value per format; QS_SHARE.md §10).

This is the ONE table. A sup's resolution_qs() takes its value from here and
the build fails for a value that is not here, or that names another format.
On the camera each sup also claims its value (claim_res UIS_RES_ENUM_BASE +
value, EXCL; qs_layer.c qs_check), so two sups with the same value cannot both
load. Values 2 and 3 are the firmware's own rows.

The numbers match Jose Hurtado's Formats (his pair table: S16 5, OG2K 7,
OG3K 4, OG3.5K 9, OG4K 8). A new format takes the next free number here first.
"""

RESOLUTION = {
    'FHD': 2,           # stock
    'UHD': 3,           # stock
    'OG3K': 4,
    'S16': 5,
    'OG2K': 7,
    'OG4K': 8,
    'OG3.5K': 9,
}
STOCK = {'FHD', 'UHD'}
UIS_RES_ENUM_BASE = 0x4D4E4500          # claim_res id = base + value ("\0ENM" + value)


class EnumError(ValueError):
    pass


def _check_table():
    values = list(RESOLUTION.values())
    if len(set(values)) != len(values):
        raise EnumError('two formats share a resolution value')
    if any(not 0 <= v <= 0xFF for v in values):
        raise EnumError('a resolution value does not fit a byte')


_check_table()


def resolution(label, value):
    """`value` for the format the Settings row `label` names ('OG3K 3008x2000'
    -> 'OG3K'). Fails unless the table gives that format exactly that value."""
    name = label.split()[0] if label else ''
    if name in STOCK:
        raise EnumError(f'{name} is a stock row, not a sup format')
    if name not in RESOLUTION:
        raise EnumError(f'{name!r} is not in the resolution table (ui/enums.py)')
    if RESOLUTION[name] != value:
        raise EnumError(f'{name} is {RESOLUTION[name]} in the table, not {value}')
    return value
