"""Shared vehicle-powertrain applicability helpers.

Used to filter both flat Service records and ServicePackage/PackageItem
checklist rows by vehicle type (e.g. excluding an oil-change item for a BEV).
Extracted from routes/appointments.py so routes/packages.py can reuse the
exact same logic without duplicating/drifting from it.
"""


def normalize_vehicle_powertrain(vehicle):
    """Normalize vehicle/fuel type into one of: BEV, PHEV, HEV, Diesel, Gasoline."""
    vehicle_type_str = (vehicle.vehicle_type or '').strip().lower()
    make_str = (vehicle.make or '').strip().lower()
    model_str = (vehicle.model or '').strip().lower()

    if (
        'bev' in vehicle_type_str or
        'battery electric' in vehicle_type_str
    ):
        return 'BEV'

    is_hybrid_tag = ('hev' in vehicle_type_str) or ('phev' in vehicle_type_str)
    if (
        (vehicle_type_str == 'ev') or
        ('electric' in vehicle_type_str) or
        (' - ev' in vehicle_type_str) or
        (vehicle_type_str.endswith('ev') and not is_hybrid_tag and 'bev' not in vehicle_type_str) or
        ('tesla' in make_str) or
        ('tesla' in model_str)
    ):
        return 'BEV'

    if 'phev' in vehicle_type_str or 'plug-in hybrid' in vehicle_type_str:
        return 'PHEV'

    if 'hev' in vehicle_type_str or 'hybrid' in vehicle_type_str:
        return 'HEV'

    if 'diesel' in vehicle_type_str:
        return 'Diesel'

    return 'Gasoline'


def _label(obj):
    """Best-effort display name for debug logging (Service uses .name,
    PackageItem uses .item_name)."""
    return getattr(obj, 'name', None) or getattr(obj, 'item_name', None) or '?'


def is_service_applicable_by_flag(service, powertrain):
    """Check if a Service or PackageItem is applicable to a powertrain using
    its applicable_* flags."""
    flag_map = {
        'Gasoline': 'applicable_gasoline',
        'Diesel': 'applicable_diesel',
        'HEV': 'applicable_hev',
        'PHEV': 'applicable_phev',
        'BEV': 'applicable_bev'
    }
    flag = flag_map.get(powertrain)
    if flag:
        # Check if attribute exists on the model
        if not hasattr(service, flag):
            # Flag column doesn't exist in DB — fall back to name-based check
            return not is_service_blocked_by_name(_label(service), powertrain)
        val = getattr(service, flag, True)
        # None or non-boolean defaults to True for safety
        if val is None:
            return True
        return bool(val)
    return True


# Name-based fallback compatibility for when compatibility table is incomplete.
BLOCKED_SERVICE_KEYWORDS = {
    # BEV has no ICE drivetrain, so these services are not applicable.
    'BEV': [
        'oil change',
        'engine oil',
        'spark plug',
        'fuel filter',
        'engine tune up',
        'engine tune-up'
    ]
}


def is_service_blocked_by_name(service_name, powertrain):
    name = (service_name or '').strip().lower()
    blocked_terms = BLOCKED_SERVICE_KEYWORDS.get(powertrain, [])
    return any(term in name for term in blocked_terms)
