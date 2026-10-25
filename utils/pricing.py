"""Auto-computed price estimate for an appointment, based on the selected
services/packages and the booked vehicle's size class — e.g. the same
21-Point Checkup Package prices differently for a Vios (small) vs a
Fortuner (large), so staff no longer type a price range by hand."""
from utils.vehicle_size_class import DEFAULT_SIZE_CLASS


def vehicle_size_class(vehicle):
    if vehicle is None:
        return DEFAULT_SIZE_CLASS
    return getattr(vehicle, 'size_class', None) or DEFAULT_SIZE_CLASS


def compute_suggested_estimate(appointment):
    """Sum of each selected package/service's price for the appointment's
    vehicle size class. Returns 0.0 if nothing is selected."""
    size_class = vehicle_size_class(appointment.vehicle)
    total = 0.0
    for package in appointment.packages:
        total += package.price_for(size_class)
    for service in appointment.services:
        total += service.price_for(size_class)
    return total
