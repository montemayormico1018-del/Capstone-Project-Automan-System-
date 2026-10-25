"""Vehicle data for dropdown selection - Philippines market."""

# Popular vehicle brands and models in the Philippines (main brands only)
PHILIPPINE_VEHICLES = {
    "Toyota": ["Vios", "Fortuner", "Innova", "Hilux", "Camry", "Corolla Altis", "RAV4", "Hiace", "Wigo", "Yaris"],
    "Honda": ["City", "Civic", "CR-V", "HR-V", "Jazz", "BR-V"],
    "Mitsubishi": ["Montero Sport", "Xpander", "Lancer EX", "Mirage", "Triton"],
    "Ford": ["Everest", "EcoSport", "Ranger"],
    "Nissan": ["Terra", "Navara", "Almera", "Kicks"],
    "Hyundai": ["Santa Fe", "Tucson", "Creta", "Starex", "Accent"],
    "Kia": ["Sorento", "Sportage", "Carnival", "Seltos", "Picanto"],
    "Mazda": ["CX-5", "CX-9", "CX-3"],
    "Suzuki": ["Vitara", "Swift", "Ertiga", "Jimny"],
    "Chevrolet": ["Trailblazer", "Colorado", "Trax", "Spark"],
    "Isuzu": ["MU-X", "D-Max"]
}

# Vehicle years (current year down to 2000)
VEHICLE_YEARS = list(range(2026, 1999, -1))

# Common vehicle colors
VEHICLE_COLORS = [
    "White", "Black", "Silver", "Gray", "Red", "Blue", "Brown", "Green", 
    "Orange", "Yellow", "Beige", "Gold", "Navy", "Burgundy"
]


def get_vehicle_brands():
    """Get list of vehicle brands."""
    return list(PHILIPPINE_VEHICLES.keys())


def get_vehicle_models(brand):
    """Get list of models for a specific brand."""
    return PHILIPPINE_VEHICLES.get(brand, [])


def get_all_vehicles():
    """Get all vehicles formatted for dropdown."""
    result = []
    for brand, models in PHILIPPINE_VEHICLES.items():
        for model in models:
            result.append({"brand": brand, "model": model, "label": f"{brand} {model}"})
    return result
