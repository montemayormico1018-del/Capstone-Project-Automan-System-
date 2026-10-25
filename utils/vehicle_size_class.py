"""Vehicle size-class classification, used to auto-suggest a pricing tier
(small / midsize / large) for a vehicle based on its make/model — e.g. a
Toyota Vios is 'small' while a Toyota Fortuner is 'large'. Packages/services
can then define a different price per tier instead of one flat price for
every vehicle.

The suggestion is just a starting point: it's stored on the vehicle record
and can be corrected manually if a specific trim/model doesn't fit.
"""

SIZE_CLASSES = ['small', 'midsize', 'large']
DEFAULT_SIZE_CLASS = 'midsize'

SIZE_CLASS_LABELS = {
    'small': 'Small (Sedan/Hatchback)',
    'midsize': 'Midsize (Crossover/SUV/MPV)',
    'large': 'Large (Pickup/Full-size SUV/Van)',
}

# (make, model) -> size_class, covering the common PH-market models offered
# in the vehicle-add dropdowns. Matching is case-insensitive.
_MODEL_SIZE_CLASS = {
    ('toyota', 'vios'): 'small',
    ('toyota', 'wigo'): 'small',
    ('toyota', 'yaris'): 'small',
    ('toyota', 'corolla altis'): 'small',
    ('toyota', 'camry'): 'midsize',
    ('toyota', 'rav4'): 'midsize',
    ('toyota', 'innova'): 'midsize',
    ('toyota', 'fortuner'): 'large',
    ('toyota', 'hilux'): 'large',
    ('toyota', 'hiace'): 'large',

    ('honda', 'city'): 'small',
    ('honda', 'civic'): 'small',
    ('honda', 'jazz'): 'small',
    ('honda', 'cr-v'): 'midsize',
    ('honda', 'hr-v'): 'midsize',
    ('honda', 'br-v'): 'midsize',

    ('mitsubishi', 'mirage'): 'small',
    ('mitsubishi', 'lancer ex'): 'small',
    ('mitsubishi', 'xpander'): 'midsize',
    ('mitsubishi', 'montero sport'): 'large',
    ('mitsubishi', 'triton'): 'large',

    ('ford', 'ecosport'): 'midsize',
    ('ford', 'everest'): 'large',
    ('ford', 'ranger'): 'large',

    ('nissan', 'almera'): 'small',
    ('nissan', 'kicks'): 'midsize',
    ('nissan', 'terra'): 'large',
    ('nissan', 'navara'): 'large',

    ('hyundai', 'accent'): 'small',
    ('hyundai', 'creta'): 'midsize',
    ('hyundai', 'tucson'): 'midsize',
    ('hyundai', 'santa fe'): 'large',
    ('hyundai', 'starex'): 'large',

    ('kia', 'picanto'): 'small',
    ('kia', 'seltos'): 'midsize',
    ('kia', 'sportage'): 'midsize',
    ('kia', 'sorento'): 'large',
    ('kia', 'carnival'): 'large',

    ('mazda', 'cx-3'): 'midsize',
    ('mazda', 'cx-5'): 'midsize',
    ('mazda', 'cx-9'): 'large',

    ('suzuki', 'swift'): 'small',
    ('suzuki', 'jimny'): 'small',
    ('suzuki', 'vitara'): 'midsize',
    ('suzuki', 'ertiga'): 'midsize',

    ('chevrolet', 'spark'): 'small',
    ('chevrolet', 'trax'): 'midsize',
    ('chevrolet', 'trailblazer'): 'large',
    ('chevrolet', 'colorado'): 'large',

    ('isuzu', 'mu-x'): 'large',
    ('isuzu', 'd-max'): 'large',

    ('tesla', 'model 3'): 'midsize',
    ('tesla', 'model y'): 'midsize',
    ('tesla', 'model s'): 'large',
    ('tesla', 'model x'): 'large',
}


def guess_size_class(make, model):
    """Best-guess size class for a make/model, or the default if unknown."""
    key = ((make or '').strip().lower(), (model or '').strip().lower())
    return _MODEL_SIZE_CLASS.get(key, DEFAULT_SIZE_CLASS)


def normalize_size_class(value):
    """Coerce an arbitrary value to a valid size class, or None if invalid."""
    if value in SIZE_CLASSES:
        return value
    return None
