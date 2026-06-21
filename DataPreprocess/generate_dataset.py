# generate_dataset.py
import json
import csv
import numpy as np
import pandas as pd
from datetime import timedelta
from pathlib import Path
from collections import deque
from tqdm import tqdm

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
    Flattens the structural tree and yields ordered units.
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
            
            d_active = r_active or active_explicit.get(d_uid, False)
            if d_active and d_idx is not None:
                state_vec[d_idx] = 1

            for community in district.get("communities", []):
                c_uid = community["uid"]
                c_idx = unit_to_idx.get(c_uid)
                
                c_active = d_active or active_explicit.get(c_uid, False)
                if c_active and c_idx is not None:
                    state_vec[c_idx] = 1

    return state_vec

def generate_csv_datasets_stream(hierarchy_json_path, alerts_csv_path, output_x_csv, output_y_csv):
    # Load administrative hierarchy
    with open(hierarchy_json_path, 'r', encoding='utf-8') as f:
        hierarchy = json.load(f)

    unit_to_idx, unit_names, name_to_uid_lookup = build_flat_mapping(hierarchy)
    num_units = len(unit_names)
    print(f"Total structured units: {num_units}")

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

    # Convert pandas Series to fast numpy arrays to bypass indexing overhead in loop
    event_times_np = df['exact_time'].values
    event_regions_np = df['region'].values
    event_statuses_np = df['status'].values

    sample_interval = timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    offset_steps = int(RNN_TARGET_OFFSET_MINUTES / SAMPLE_INTERVAL_MINUTES)

    # Pre-generate the complete timeline sequence
    timeline = pd.date_range(start=start_time, end=end_time, freq=sample_interval)

    # Instantiate the sliding window buffer to pair inputs X(t) with target labels Y(t+10)
    state_buffer = deque(maxlen=offset_steps + 1)

    active_explicit = {}  # Tracks explicit, un-propagated alerts
    event_idx = 0
    num_events = len(df)

    # Setup vectorized tracking states
    last_state_arr = np.zeros(num_units, dtype=np.uint8)
    last_change_time_minutes = np.zeros(num_units, dtype=np.float32)
    last_direction_arr = np.zeros(num_units, dtype=np.int8)

    state_vector_arr = np.zeros(num_units, dtype=np.uint8)
    first_step = True

    # Open CSV files directly for streaming output
    print(f"Streaming outputs directly to '{output_x_csv}' and '{output_y_csv}'...")
    with open(output_x_csv, 'w', encoding='utf-8', newline='') as f_x, \
         open(output_y_csv, 'w', encoding='utf-8', newline='') as f_y:

        writer_x = csv.writer(f_x)
        writer_y = csv.writer(f_y)

        # Write CSV headers: Explicitly grouped so X has States first (essential for the model's skip connection)
        state_headers = [f"{name}_state" for name in unit_names]
        elapsed_headers = [f"{name}_elapsed" for name in unit_names]
        direction_headers = [f"{name}_direction" for name in unit_names]
        
        writer_x.writerow(['timestamp'] + state_headers + elapsed_headers + direction_headers)
        writer_y.writerow(['target_timestamp'] + state_headers)

        # Wrap the timeline iteration inside a tqdm progress bar
        for current_time in tqdm(timeline, desc="Streaming dataset generation"):
            current_time_np = np.datetime64(current_time)
            current_minutes_since_start = (current_time - start_time).total_seconds() / 60.0
            
            # 1. Process all events up to current_time (Using ultra-fast numpy arrays)
            events_processed = False
            while event_idx < num_events and event_times_np[event_idx] <= current_time_np:
                raw_region_name = event_regions_np[event_idx]
                status = int(event_statuses_np[event_idx])

                normalized = normalize_name(raw_region_name)
                uid = name_to_uid_lookup.get(normalized)

                if uid is not None:
                    if status == 1:
                        active_explicit[uid] = True
                    else:
                        active_explicit.pop(uid, None)
                    events_processed = True
                event_idx += 1

            # 2. Lazy Propagation check: Only rebuild the state representation if an event was processed
            if events_processed or first_step:
                state_vector_arr = np.array(
                    build_propagated_state(active_explicit, hierarchy, unit_to_idx), 
                    dtype=np.uint8
                )
                first_step = False

            # 3. Vectorized feature engineering (NumPy)
            changed_mask = state_vector_arr != last_state_arr
            
            # For units that changed state, update their timestamps and transition directions
            last_change_time_minutes[changed_mask] = current_minutes_since_start
            last_direction_arr[changed_mask] = np.where(state_vector_arr[changed_mask] == 1, 1, -1)
            last_state_arr[changed_mask] = state_vector_arr[changed_mask]
            
            # Calculate and scale elapsed time (capped at 24 hours / 1440 minutes)
            elapsed_minutes_arr = current_minutes_since_start - last_change_time_minutes
            scaled_elapsed_arr = np.minimum(elapsed_minutes_arr, 1440.0) / 1440.0
            
            # Build the combined flat step feature representation
            # We convert numpy arrays to fast python lists to optimize writing to CSV
            flat_engineered_step = (
                state_vector_arr.tolist() + 
                np.round(scaled_elapsed_arr, 5).tolist() + 
                last_direction_arr.tolist()
            )
            
            # Push current time and complete feature row to the buffer
            state_buffer.append((current_time, flat_engineered_step, state_vector_arr.tolist()))

            # Once the sliding buffer is full, we can yield and write the pair
            if len(state_buffer) == offset_steps + 1:
                x_time, x_features, _ = state_buffer[0]            # Input features at time t
                y_time, _, y_target_states = state_buffer[-1]      # Target states at time t + 10 mins

                # Format timestamps to standard string layout
                x_time_str = x_time.strftime('%Y-%m-%dT%H:%M:%S')
                y_time_str = y_time.strftime('%Y-%m-%dT%H:%M:%S')

                # Write rows directly to disk
                writer_x.writerow([x_time_str] + x_features)
                writer_y.writerow([y_time_str] + y_target_states)

    print("Processing complete. Memory usage remained minimal.")

if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).resolve().parent
    HIERARCHY_JSON = SCRIPT_DIR / "FinalData" / "hierarchy.json"
    ALERTS_CSV = SCRIPT_DIR / "RawData" / "air_raids_output.csv"

    # Human-readable output dataset files
    OUTPUT_X_CSV = SCRIPT_DIR / "FinalData" / "X_dataset.csv"
    OUTPUT_Y_CSV = SCRIPT_DIR / "FinalData" / "Y_dataset.csv"
    
    generate_csv_datasets_stream(HIERARCHY_JSON, ALERTS_CSV, OUTPUT_X_CSV, OUTPUT_Y_CSV)