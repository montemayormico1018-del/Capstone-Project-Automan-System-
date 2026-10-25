"""Shared Philippine LTO plate / conduction-sticker validation.

Single source of truth for the accepted formats, used by both the vehicle
routes (staff-registered vehicles) and the appointment booking route
(customer-typed plate at booking time). Add/adjust accepted formats by
editing PLATE_FORMAT_RULES below — nothing else needs to change.
"""
import re

# Each rule: (id, compiled regex, human-readable expected format, example).
# 'plate' accepts ANY of these; 'conduction' has its own single rule.
PLATE_FORMAT_RULES = {
    'plate': [
        ('new_car', re.compile(r'^[A-Z]{3}[0-9]{4}$'),
         '3 letters followed by 4 digits', 'NBC 1234'),
        ('old_car', re.compile(r'^[A-Z]{3}[0-9]{3}$'),
         '3 letters followed by 3 digits', 'ABC 123'),
        ('motorcycle', re.compile(r'^[A-Z0-9]{5,7}$'),
         '5 to 7 letters/digits', 'AB1234'),
    ],
    'conduction': [
        ('conduction_sticker', re.compile(r'^[A-Z]{2}[0-9]{4}$'),
         '2 letters followed by 4 digits', 'NC 1234'),
    ],
}

PLATE_TYPE_LABELS = {'plate': 'plate number', 'conduction': 'conduction sticker'}


def normalize_plate(value):
    """Uppercase and strip spaces/hyphens: 'nbc-1234' / 'NBC 1234' / 'nbc1234'
    all normalize to 'NBC1234'."""
    if not value:
        return ''
    return re.sub(r'[\s\-]', '', str(value)).upper()


def format_plate_display(normalized, plate_type=None):
    """Insert a space between the letter prefix and the rest, for display
    only (e.g. 'NBC1234' -> 'NBC 1234'). Falls back to the raw normalized
    value when the split isn't clean (covers the permissive motorcycle
    format, which may not be a simple letters-then-digits string)."""
    if not normalized:
        return ''
    m = re.match(r'^([A-Z]+)([0-9A-Z]+)$', normalized)
    if m and m.group(1) and m.group(2):
        return f'{m.group(1)} {m.group(2)}'
    return normalized


def validate_plate(value, plate_type):
    """Validate + normalize a plate number or conduction sticker.

    Args:
        value: raw user input, any case/spacing.
        plate_type: 'plate' or 'conduction'.

    Returns:
        (True, normalized_value, None) if valid.
        (False, None, error_message) if invalid or missing.
    """
    if plate_type not in PLATE_FORMAT_RULES:
        plate_type = 'plate'

    normalized = normalize_plate(value)
    if not normalized:
        return False, None, 'Plate number or conduction sticker is required.'

    rules = PLATE_FORMAT_RULES[plate_type]
    for _id, pattern, _desc, _example in rules:
        if pattern.match(normalized):
            return True, normalized, None

    if plate_type == 'conduction':
        _id, _pattern, desc, example = rules[0]
        message = (
            f'Invalid conduction sticker format. Expected {desc} '
            f'(e.g., {example}).'
        )
    else:
        options = '; '.join(f'{desc} (e.g., {example})' for _id, _p, desc, example in rules)
        message = f'Invalid plate number format. Expected one of: {options}.'

    return False, None, message
