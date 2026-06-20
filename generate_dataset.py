# generate_dataset.py
import json
import csv
import pandas as pd
from datetime import timedelta
from pathlib import Path
from collections import deque
from tqdm import tqdm  # Progress bar library

# Configurable constants
SAMPLE_INTERVAL_MINUTES = 5
RNN_TARGET_OFFSET_MINUTES = 10  # Future target state offset (Y)

def normalize_name(name):
    """
    Normalizes names to simplify matching across files,
    accounting for regional synonyms and short-hand city designations.
    """
    if not name:
        return ""
    name = name.strip().lower().replace(' ', '_')
    name = name.strip('_').strip('.')
    
    # Common regional/city-level alert synonyms to official database matches
    synonyms = {
        "франківська_область": "івано-франківська_область",
        "франківська_область.": "івано-франківська_область",
        "франківщина": "івано-франківська_область",
        "івано-франківщина": "івано-франківська_область",
        "київщина": "київська_область",
        "тернопільщина": "тернопільська_область",
        "львівщина": "львівська_область",
        "волинь": "волинська_область",
        "буковина": "чернівецька_область",
        "закарпаття": "закарпатська_область",
        "бахмут": "бахмутська_територіальна_громада",
        "умань": "уманська_територіальна_громада",
        "васильків": "м._васильків_та_васильківська_територіальна_громада",
        "кривий_ріг": "м._кривий_ріг_та_криворізька_територіальна_громада",
        "нікополь": "м._нікополь_та_нікопольська_територіальна_громада",
        "дніпро": "м._дніпро_та_дніпровська_територіальна_громада",
        "запоріжжя": "м._запоріжжя_та_запорізька_територіальна_громада",
        "харків": "м._харків_та_харківська_територіальна_громада",
        "чернігів": "м._чернігів_та_чернігівська_територіальна_громада",
        "херсон": "м._херсон_та_херсонська_територіальна_громада",
        "миколаїв": "м._миколаїв_та_миколаївська_територіальна_громада",
        "черкаси": "м._черкаси_та_черкаська_територіальна_громада",
        "суми": "м._суми_та_сумська_територіальна_громада",
        "полтава": "м._полтава_та_полтавська_територіальна_громада",
        "рівне": "м._рівне_та_рівненська_територіальна_громада",
        "одеса": "м._одеса_та_одеська_територіальна_громада",
        "вінниця": "м._вінниця_та_вінницька_територіальна_громада",
        "хмельницький": "м._хмельницький_та_хмельницька_територіальна_громада",
        "луцьк": "м._луцьк_та_луцька_територіальна_громада",
        "житомир": "м._житомир_та_житомирська_територіальна_громада",
        "ужгород": "м._ужгород_та_ужгородська_територіальна_громада",
        "кропивницький": "м._кропивницький_та_кропивницька_територіальна_громада",
        "чернівці": "м._чернівці_та_чернівецька_територіальна_громада",
    }
    return synonyms.get(name, name)

def build_flat_mapping(hierarchy):
    """
    Flattens the structural tree and yields ordered units to form CSV headers.
    """
    unit_to_idx = {}
    unit_names = []
    lookup = {}
    idx = 0

    for region in hierarchy["regions"]:
        r_uid, r_name = region["uid"], region["name"]
        unit_to_idx[r_uid] = idx
        unit_names.append(r_name)
        lookup[normalize_name(r_name)] = r_uid
        idx += 1

        for district in region.get("districts", []):
            d_uid, d_name = district["uid"], district["name"]
            unit_to_idx[d_uid] = idx
            unit_names.append(d_name)
            lookup[normalize_name(d_name)] = d_uid
            idx += 1

            for community in district.get("communities", []):
                c_uid, c_name = community["uid"], community["name"]
                unit_to_idx[c_uid] = idx
                unit_names.append(c_name)
                lookup[normalize_name(c_name)] = c_uid
                idx += 1

    return unit_to_idx, unit_names, lookup

def build_propagated_state(active_explicit, hierarchy, unit_to_idx):
    """
    Constructs a flattened state vector using downward propagation.
    Oblast alert activation automatically cascades to nested Rayons and Hromadas.
    Under upward isolation, selective Hromada alerts do not propagate up to parents.
    """
    state_vec = [0] * len(unit_to_idx)

    for region in hierarchy["regions"]:
        r_uid = region["uid"]
        r_idx = unit_to_idx.get(r_uid)
        
        r_active = active_explicit.get(r_uid, False)
        if r_active and r_idx is not None:
            state_vec[r_idx] = 1

        for district in region.get("districts", []):
            d_uid = district["uid"]
            d_idx = unit_to_idx.get(d_uid)
            
            # District is active if explicitly active OR if parent region is active
            d_active = r_active or active_explicit.get(d_uid, False)
            if d_active and d_idx is not None:
                state_vec[d_idx] = 1

            for community in district.get("communities", []):
                c_uid = community["uid"]
                c_idx = unit_to_idx.get(c_uid)
                
                # Hromada is active if parent district is active OR if explicitly active
                c_active = d_active or active_explicit.get(c_uid, False)
                if c_active and c_idx is not None:
                    state_vec[c_idx] = 1

    return state_vec

def generate_csv_datasets_stream(hierarchy_json_path, alerts_csv_path, output_x_csv, output_y_csv):
    # Load administrative hierarchy
    with open(hierarchy_json_path, 'r', encoding='utf-8') as f:
        hierarchy = json.load(f)

    unit_to_idx, unit_names, name_to_uid_lookup = build_flat_mapping(hierarchy)
    print(f"Total structured units to be mapped as CSV columns: {len(unit_names)}")

    # Load alert history
    if not Path(alerts_csv_path).exists():
        print(f"Error: Alert history file '{alerts_csv_path}' not found.")
        return

    print("Loading historical alerts timeline...")
    df = pd.read_csv(alerts_csv_path)
    df['exact_time'] = pd.to_datetime(df['exact_time'])
    df = df.sort_values('exact_time').reset_index(drop=True)

    start_time = df['exact_time'].min()
    end_time = df['exact_time'].max()
    print(f"Historical timeline bounds: {start_time} to {end_time}")

    sample_interval = timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    offset_steps = int(RNN_TARGET_OFFSET_MINUTES / SAMPLE_INTERVAL_MINUTES)

    # Pre-generate the complete timeline sequence for progress calculation
    timeline = pd.date_range(start=start_time, end=end_time, freq=sample_interval)

    # Instantiate the sliding window buffer
    # It will contain tuples of (timestamp, state_vector)
    state_buffer = deque(maxlen=offset_steps + 1)

    active_explicit = {}  # Tracks explicit, un-propagated alerts
    event_idx = 0
    num_events = len(df)

    # Open CSV files directly for streaming output
    print(f"Streaming outputs directly to '{output_x_csv}' and '{output_y_csv}'...")
    with open(output_x_csv, 'w', encoding='utf-8', newline='') as f_x, \
         open(output_y_csv, 'w', encoding='utf-8', newline='') as f_y:

        writer_x = csv.writer(f_x)
        writer_y = csv.writer(f_y)

        # Write CSV headers
        writer_x.writerow(['timestamp'] + unit_names)
        writer_y.writerow(['target_timestamp'] + unit_names)

        # Wrap the timeline iteration inside a tqdm progress bar
        for current_time in tqdm(timeline, desc="Streaming dataset generation"):
            # Update explicit active dictionary with events occurring in the current interval window
            while event_idx < num_events and df.loc[event_idx, 'exact_time'] <= current_time:
                row = df.loc[event_idx]
                raw_region_name = row['region']
                status = int(row['status'])

                normalized = normalize_name(raw_region_name)
                uid = name_to_uid_lookup.get(normalized)

                if uid is not None:
                    if status == 1:
                        active_explicit[uid] = True
                    else:
                        active_explicit.pop(uid, None)
                event_idx += 1

            # Run tree propagation and build state vector
            state_vector = build_propagated_state(active_explicit, hierarchy, unit_to_idx)
            
            # Push current time and state to the buffer
            state_buffer.append((current_time, state_vector))

            # Once the sliding buffer is full, we can yield and write the pair
            if len(state_buffer) == offset_steps + 1:
                x_time, x_state = state_buffer[0]  # Oldest item in buffer (t)
                y_time, y_state = state_buffer[-1] # Newest item in buffer (t + 10 mins)

                # Format timestamps to standard string layout
                x_time_str = x_time.strftime('%Y-%m-%dT%H:%M:%S')
                y_time_str = y_time.strftime('%Y-%m-%dT%H:%M:%S')

                # Write rows directly to disk
                writer_x.writerow([x_time_str] + x_state)
                writer_y.writerow([y_time_str] + y_state)

    print("Processing complete. Memory usage remained minimal.")




if __name__ == "__main__":
    HIERARCHY_JSON = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\FinalData\\hierarchy.json"
    ALERTS_CSV = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\RawData\\air_raids_output.csv"

    # Human-readable output dataset files
    OUTPUT_X_CSV = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\FinalData\\X_dataset.csv"
    OUTPUT_Y_CSV = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\FinalData\\Y_dataset.csv"
    
    generate_csv_datasets_stream(HIERARCHY_JSON, ALERTS_CSV, OUTPUT_X_CSV, OUTPUT_Y_CSV)