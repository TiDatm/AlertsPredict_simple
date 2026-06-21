# create_hierarchy.py
import csv
import json
import re
from pathlib import Path

def clean_name(name):
    """
    Standardizes names by replacing whitespace with underscores 
    and stripping trailing punctuation (e.g. trailing periods).
    """
    if not name:
        return ""
    # Standardize spaces to single underscores
    cleaned = re.sub(r'\s+', '_', name.strip())
    # Strip trailing punctuation
    cleaned = cleaned.rstrip('.! ')
    return cleaned

def parse_hierarchy(csv_path, json_output_path):
    input_file = Path(csv_path)
    if not input_file.exists():
        print(f"Error: Raw hierarchy file '{csv_path}' not found.")
        return

    regions = []
    current_region = None
    current_district = None

    print(f"Parsing raw hierarchy from '{csv_path}'...")
    
    with open(input_file, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        
        # Skip top metadata lines until the header row is found
        header_found = False
        for row in reader:
            if row and row[0] == 'UID':
                header_found = True
                break
                
        if not header_found:
            print("Error: Could not locate the 'UID' header in the raw CSV.")
            return

        for row in reader:
            if len(row) < 3:
                continue
            
            uid_str, name_raw, type_str = row[0].strip(), row[1].strip(), row[2].strip()
            if not uid_str or not name_raw:
                continue
                
            uid = int(uid_str)
            name = clean_name(name_raw)

            # 1. Parse Top-Level Entities (Oblasts & Special Status Cities)
            if type_str in ("Область", "Місто з спеціальним статусом"):
                region_node = {
                    "uid": uid,
                    "name": name,
                    "type": type_str,
                    "districts": []
                }
                regions.append(region_node)
                
                # Active parent state tracker logic:
                # Crimea (UID 29) is treated as a top-level region, but has no districts 
                # in this dataset. We keep current_region as is (e.g. Volyn), 
                # so that subsequent districts are correctly parented.
                if uid != 29:
                    current_region = region_node
                    current_district = None

            # 2. Parse Districts / Rayons
            elif type_str == "Район":
                district_node = {
                    "uid": uid,
                    "name": name,
                    "type": type_str,
                    "communities": []
                }
                if current_region:
                    current_region["districts"].append(district_node)
                    current_district = district_node
                else:
                    print(f"Warning: District '{name}' found without an active parent region.")

            # 3. Parse Communities / Hromadas
            elif type_str in ("Громада", "Територіальна громада"):
                community_node = {
                    "uid": uid,
                    "name": name,
                    "type": type_str
                }
                if current_district:
                    current_district["communities"].append(community_node)
                else:
                    # Special status cities can have direct hromadas or be separate
                    if current_region:
                        # Append directly if no district exists
                        current_region["districts"].append({
                            "uid": uid,
                            "name": name,
                            "type": "Громада",
                            "communities": []
                        })
                    else:
                        print(f"Warning: Community '{name}' found without parent.")

    # Write output to structured JSON
    output_data = {"regions": regions}
    with open(json_output_path, 'w', encoding='utf-8') as out_f:
        json.dump(output_data, out_f, indent=2, ensure_ascii=False)
        
    print(f"Successfully generated structured hierarchy JSON: '{json_output_path}'")

if __name__ == "__main__":
    # Ensure this matches your input raw CSV filename
    SCRIPT_DIR = Path(__file__).resolve().parent
    INPUT_CSV = SCRIPT_DIR / "RawData" / "alerts.in.ua _ Райони, області, громади - Sheet1 (1).csv"
    OUTPUT_JSON = SCRIPT_DIR / "FinalData" / "hierarchy.json"
    parse_hierarchy(INPUT_CSV, OUTPUT_JSON)