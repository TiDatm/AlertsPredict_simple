import json
import csv
import re
from pathlib import Path

def extract_raw_text(text_field):
    """
    Extracts raw string text from Telegram's 'text' structure.
    In Telegram JSON exports, formatting might split text into lists of objects.
    """
    if isinstance(text_field, str):
        return text_field
    elif isinstance(text_field, list):
        parts = []
        for part in text_field:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
        return "".join(parts)
    return ""

def clean_region_name(region):
    """
    Cleans up trailing punctuation, hashtags, drops common boilerplate 
    trailing phrases, and replaces all spaces with underscores.
    """
    if not region:
        return ""
    
    # 1. Truncate common boilerplates that might end up on the same line as the region
    boilerplates = [
        "слідкуйте за",
        "перейдіть в",
        "пройдіть в",
        "будьте в",
        "усі в укриття",
        "всі в укриття",
        "зберігайте спокій",
        "не публікуйте"
    ]
    
    region_lower = region.lower()
    for bp in boilerplates:
        if bp in region_lower:
            idx = region_lower.find(bp)
            region = region[:idx]
            region_lower = region.lower() # Update lowercase representation

    # 2. Clean trailing and leading spaces/punctuation (removes trailing periods, exclamation marks, etc.)
    region = region.strip().rstrip('.! ')
    
    # 3. Strip trailing hashtags (e.g., #Київ)
    region = re.sub(r'\s*#\w+', '', region)
    
    # 4. Final safety strip
    region = region.strip().rstrip('.! ')
    
    # 5. Skip invalid single-word/abbreviation leftovers
    if region.lower() in ("м", "м.", "область", "район", "громада", ""):
        return ""
        
    # 6. Replace spaces and consecutive whitespaces with underscores
    region = re.sub(r'\s+', '_', region)
        
    return region

def parse_message_content(text):
    """
    Parses raw message text to find the status (1 = alert, 0 = rebound)
    and the list of affected regions/districts, applying cleanup rules.
    """
    text = text.strip()
    if not text:
        return None, []

    # Determine status (1 = Alert, 0 = Rebound/Clear)
    is_alert = None
    if "🔴" in text or "Повітряна тривога" in text or "УВАГА" in text:
        is_alert = 1
    elif "🟢" in text or "Відбій" in text:
        is_alert = 0

    if is_alert is None:
        return None, []

    regions = []

    # 1. Check for consolidated bullet-point format first (•, -, *)
    bullet_points = re.findall(r'[•\-\*]\s*([^\n]+)', text)
    if bullet_points:
        for bp in bullet_points:
            cleaned = clean_region_name(bp)
            if cleaned:
                regions.append(cleaned)
    else:
        # 2. Check for single-line format
        # Using [^\n]+ allows us to match internal periods (like in "м. Київ")
        match = re.search(r'(?:тривога|тривоги)(?:\s+в)?\s+([^\n]+)', text, re.IGNORECASE)
        if match:
            cleaned = clean_region_name(match.group(1))
            if cleaned:
                regions.append(cleaned)
        else:
            # 3. Fallback: Extract from hashtags if text extraction falls through
            hashtags = re.findall(r'#(\w+)', text)
            for tag in hashtags:
                cleaned_tag = tag.replace('_', ' ').strip()
                if cleaned_tag and "тривога" not in cleaned_tag.lower():
                    cleaned = clean_region_name(cleaned_tag)
                    if cleaned:
                        regions.append(cleaned)

    # Remove duplicates while maintaining order
    seen = set()
    unique_regions = []
    for r in regions:
        if r not in seen:
            seen.add(r)
            unique_regions.append(r)

    return is_alert, unique_regions

def process_air_raid_history(input_json_path, output_csv_path):
    input_file = Path(input_json_path)
    if not input_file.exists():
        print(f"Error: The input file '{input_json_path}' was not found.")
        return

    print(f"Loading data from '{input_json_path}'...")
    with open(input_file, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            return

    messages = []
    if "messages" in data:
        messages = data["messages"]
    elif "chats" in data and isinstance(data["chats"], dict) and "list" in data["chats"]:
        for chat in data["chats"]["list"]:
            if "messages" in chat:
                messages.extend(chat["messages"])
    else:
        print("Could not locate standard Telegram 'messages' fields in the JSON structure.")
        return

    print(f"Found {len(messages)} total entries. Parsing alerts...")

    parsed_records = []
    for msg in messages:
        if msg.get("type") != "message":
            continue

        raw_text = extract_raw_text(msg.get("text", ""))
        
        # Skip technical updates or non-alert messages to prevent false entries
        system_keywords = ["api", "інтегратор", "розробник", "користувач"]
        if any(keyword in raw_text.lower() for keyword in system_keywords):
            continue

        exact_time = msg.get("date", "")
        status, regions = parse_message_content(raw_text)
        
        if status is not None and regions:
            for region in regions:
                parsed_records.append({
                    "status": status,
                    "region": region,
                    "exact_time": exact_time
                })

    print(f"Writing {len(parsed_records)} parsed records to '{output_csv_path}'...")
    with open(output_csv_path, 'w', encoding='utf-8', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["status", "region", "exact_time"])
        writer.writeheader()
        writer.writerows(parsed_records)

    print("Processing complete.")


if __name__ == "__main__":
    # Adjust paths if necessary
    INPUT_JSON = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\RawData\\air_raids_history.json"
    OUTPUT_CSV = "C:\\Users\\yablo\\OneDrive\\Desktop\\CodeStudio\\AlertsPredict_simple\\DataPreprocess\\RawData\\air_raids_output.csv"
    
    process_air_raid_history(INPUT_JSON, OUTPUT_CSV)