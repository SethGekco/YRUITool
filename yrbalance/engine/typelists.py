"""
Type-list parsing and classification helpers.

Reads the [InfantryTypes]/[VehicleTypes]/[BuildingTypes]/[AircraftTypes] index
sections into ordered name lists, and splits vehicles into land/naval and
buildings into regular/defensive.
"""

from .ini_model import get_val


def parse_type_list(section_map, section_name):
    """Return the values of an index section (e.g. [InfantryTypes]) in file order."""
    s = section_map.get(section_name.lower())
    if s is None:
        return []
    return [v.split(";")[0].strip()
            for _, (_, _, v) in sorted(s["keys"].items(), key=lambda x: x[1][0])
            if v.split(";")[0].strip()]


def split_vehicles_naval(section_map, vehicle_list):
    """Split a vehicle list into (land, naval) by the Naval= tag."""
    vehicles, naval = [], []
    for name in vehicle_list:
        if get_val(section_map, name, "Naval", "no").lower() == "yes":
            naval.append(name)
        else:
            vehicles.append(name)
    return vehicles, naval


def split_buildings_defensive(section_map, building_list):
    """Split a building list into (regular, defensive) by presence of a weapon."""
    regular, defensive = [], []
    for name in building_list:
        has_primary = get_val(section_map, name, "Primary") is not None
        has_secondary = get_val(section_map, name, "Secondary") is not None
        if has_primary or has_secondary:
            defensive.append(name)
        else:
            regular.append(name)
    return regular, defensive
