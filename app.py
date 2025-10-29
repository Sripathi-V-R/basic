# app.py
import os
import re
import time
import json
import requests
import pandas as pd
import streamlit as st
from io import StringIO
from dotenv import load_dotenv
from openai import OpenAI

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# =========================================
# Setup
# =========================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ATTOM_API_KEY = os.getenv("ATTOM_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

st.set_page_config(page_title="Revalix+ (HCAD-powered)", page_icon="🏠", layout="wide")
st.title("🏠 Revalix+ — Property Intelligence (ATTOM + HCAD + OpenAI)")

# Global fields (Identification + Location — “all fields” superset)
IDENTIFICATION_FIELDS = [
    "Property ID", "Property Name", "Owner Name",
    "Property Type", "Property Subtype",
    "Ownership Type", "Occupancy Status",
    "Building Code / Permit ID"
]

LOCATION_FIELDS = [
    "Address Line 1", "Street Name", "City", "County", "Township",
    "State", "Postal Code", "Latitude", "Longitude", "Facing Direction",
    "Neighborhood Type", "Landmark", "Legal Description", "Census Tract",
    "Market", "Submarket", "CBSA", "State Class Code", "Neighborhood Name",
    "Map Facet", "Key Map", "Tax District", "Tax Code", "Location Type"
]

ALL_FIELDS = IDENTIFICATION_FIELDS + LOCATION_FIELDS


# =========================================
# Utilities
# =========================================
def extract_json_safe(text: str, fallback: dict | None = None) -> dict:
    """
    Extracts the first JSON object from the text and parses it.
    Returns fallback (or {}) if parsing fails.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return fallback or {}


def normalize_address(address: str) -> str:
    """
    Step 1: Normalize address via OpenAI (strict JSON).
    """
    prompt = f"""
    Return only pure JSON:
    {{
      "corrected_address": "<USPS-style full address>"
    }}
    Address: "{address}"
    """
    res = client.responses.create(model="gpt-4.1-mini", input=prompt)
    data = extract_json_safe(res.output_text, {"corrected_address": address})
    return data.get("corrected_address", address)


def enforce_user_zip(original: str, corrected: str) -> str:
    """
    Keeps user's typed ZIP if both have ZIPs and they differ.
    """
    orig_zip = re.findall(r"\b\d{5}\b", original or "")
    corr_zip = re.findall(r"\b\d{5}\b", corrected or "")
    if orig_zip and corr_zip and orig_zip[0] != corr_zip[0]:
        corrected = re.sub(r"\b\d{5}\b", orig_zip[0], corrected, count=1)
        st.warning(f"ZIP overridden to user input: {orig_zip[0]}")
    return corrected


def fetch_attom(address: str) -> dict:
    """
    Step 2: ATTOM fetch APN + County + core attributes.
    """
    try:
        street, rest = address.split(",", 1)
        city, st_zip = rest.rsplit(",", 1)
        state, zipcode = st_zip.strip().split()
    except Exception:
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"apikey": ATTOM_API_KEY, "accept": "application/json"}
    params = {"address1": street.strip(),
              "address2": f"{city.strip()}, {state} {zipcode}"}
    r = requests.get(url, headers=headers, params=params, timeout=60)

    if r.status_code != 200:
        return {}

    properties = r.json().get("property", [])
    if not properties:
        return {}

    p = properties[0]
    out = {
        "apn": p.get("identifier", {}).get("apn"),
        "street_name": p.get("address", {}).get("line1"),
        "city": p.get("address", {}).get("locality"),
        "county": p.get("area", {}).get("countrySecSubd"),
        "state": p.get("address", {}).get("countrySubd"),
        "zipcode": p.get("address", {}).get("postal1"),
        "country": p.get("address", {}).get("country"),
        "latitude": p.get("location", {}).get("latitude"),
        "longitude": p.get("location", {}).get("longitude"),
        "property_type": p.get("summary", {}).get("propSubType"),
        "property_sub_type": p.get("summary", {}).get("propType"),
        # sometimes available:
        "owner_name": (p.get("owner1", {}) or {}).get("name"),
    }
    return out


# ---------- HCAD scraping and structuring ----------
def scrape_hcad_and_structure(apn: str) -> tuple[str, str]:
    """
    Step 3 (Harris only): Scrape HCAD parcel site by APN, scroll fully,
    then send full text to OpenAI to structure into Markdown tables.
    Returns (raw_text, structured_markdown).
    """
    URL = "https://arcweb.hcad.org/parcel-viewer-v2.0/"
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-popup-blocking")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=chrome_options)
    wait = WebDriverWait(driver, 60)

    try:
        driver.get(URL)
        time.sleep(7)

        # Close modal if present
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="mySurveyModal"]/div/span')))
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            pass

        # Enter APN in search
        search_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input.esri-input")))
        search_input.clear()
        search_input.send_keys(apn)

        # Robust search click
        search_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@class,'esri-search__submit-button')]")))
        driver.execute_script("arguments[0].click();", search_btn)

        # Wait popup -> click APN link
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".esri-popup__main-container")))
        link = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "#popupTable tbody tr:nth-child(1) td:nth-child(2) a")))
        driver.execute_script("arguments[0].click();", link)
        time.sleep(3)

        # Switch new tab
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(6)

        # Scroll to load all content
        last_h = 0
        while True:
            driver.execute_script("window.scrollBy(0, 1750);")
            time.sleep(1.2)
            new_h = driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h

        time.sleep(2)
        raw_text = driver.execute_script("return document.body.innerText;")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    # Structure with OpenAI: strict “Field | Value” per section
    prompt = f"""
    You will receive raw text from a county property page (HCAD).
    Convert EVERYTHING into Markdown by sections.

    Rules:
    - For each logical section, start with: ## <SECTION NAME>
    - Immediately after, a 2-column table with headers: Field | Value
    - Include every data point; no summarizing or omissions.
    - Split list values into separate rows.
    - Preserve exact numbers and text.

    Raw:
    \"\"\"{raw_text}\"\"\"
    """
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "You convert text to complete, lossless tables."},
                  {"role": "user", "content": prompt}],
        temperature=0.0,
    )
    structured_md = resp.choices[0].message.content.strip()
    return raw_text, structured_md


# ---------- Markdown tables parsing helpers ----------
def markdown_table_to_df(md_table: str) -> pd.DataFrame | None:
    """
    Convert a Markdown table string to DataFrame. Returns None on failure.
    """
    if "|" not in md_table:
        return None
    # Clean leading/trailing pipes
    lines = [ln.strip() for ln in md_table.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    # Sometimes markdown tables include a separator row like |---|---|
    # We'll rely on pandas with sep="|" and then clean separator columns.
    try:
        df = pd.read_csv(StringIO("\n".join(lines)), sep="|", engine="python", header=0)
        # Drop completely empty columns (from leading/trailing pipes)
        df = df.dropna(axis=1, how="all")
        # Strip whitespace from headers & cells
        df.columns = [c.strip() for c in df.columns]
        for c in df.columns:
            df[c] = df[c].map(lambda x: str(x).strip() if pd.notna(x) else x)
        # Remove separator row if present (e.g., ---)
        if df.shape[0] >= 1 and all(re.match(r"^\s*-{3,}\s*$", str(v)) for v in df.iloc[0].tolist()):
            df = df.iloc[1:].reset_index(drop=True)
        return df
    except Exception:
        return None


def parse_structured_sections(structured_md: str) -> tuple[dict, dict]:
    """
    Parse the structured Markdown into:
    - sections: dict[section_name] = DataFrame(Field, Value)
    - flat_kv: flattened dict of all "Field: Value" pairs (last one wins)
    """
    sections: dict[str, pd.DataFrame] = {}
    flat_kv: dict[str, str] = {}

    chunks = re.split(r"^##\s+", structured_md, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        section_name = lines[0].strip()
        table_md = "\n".join(lines[1:]).strip()
        df = markdown_table_to_df(table_md)
        if df is None or df.shape[1] < 2:
            continue

        # normalize columns to Field/Value
        cols = list(df.columns)
        # Heuristic: two-column table: first is Field, last is Value
        field_col = cols[0]
        value_col = cols[-1]

        df = df.rename(columns={field_col: "Field", value_col: "Value"})
        df = df[["Field", "Value"]]
        # build flat map
        for _, row in df.iterrows():
            k = str(row["Field"]).strip()
            v = (str(row["Value"]).strip()) if pd.notna(row["Value"]) else None
            if k:
                flat_kv[k] = v
        sections[section_name] = df

    return sections, flat_kv


# ---------- AI fill for NULLs only ----------
def ai_fill_missing(context: dict, target_fields: list[str]) -> dict:
    """
    Use OpenAI to fill only missing fields based on address, ATTOM, and (if available) scraped context.
    We return a dict of guesses; caller will only use where value is currently null.
    """
    prompt = {
        "task": "Fill only the missing values. If unknown, return null.",
        "expected_fields": target_fields,
        "context": context
    }
    res = client.responses.create(model="gpt-4.1-mini", input=json.dumps(prompt))
    data = extract_json_safe(res.output_text, {})
    # Ensure only requested keys
    guesses = {k: data.get(k) for k in target_fields}
    return guesses


# ---------- Field mapping / merge with priority ----------
def build_identification(attom: dict, scraped: dict | None, ai: dict | None, harris: bool) -> dict:
    """
    Build Identification table using priority:
      Harris: SCRAPED > ATTOM > AI
      Non-Harris: ATTOM > AI
    """
    out = {f: None for f in IDENTIFICATION_FIELDS}

    def pick(key_candidates):
        # helper to pick from multiple scraped keys fallback
        for k in key_candidates:
            if k in (scraped or {}) and scraped.get(k):
                return scraped.get(k)
        return None

    # Property ID (APN)
    out["Property ID"] = (scraped or {}).get("Account Number") or attom.get("apn") or (ai or {}).get("Property ID")

    # Owner Name
    if harris:
        out["Owner Name"] = pick(["Owner", "Owner Name"])
    if not out["Owner Name"]:
        out["Owner Name"] = attom.get("owner_name") or (ai or {}).get("Owner Name")

    # Property Type / Subtype
    if harris:
        out["Property Type"] = pick(["Property Type"])
        out["Property Subtype"] = pick(["Property Subtype"])
    out["Property Type"] = out["Property Type"] or attom.get("property_type") or (ai or {}).get("Property Type")
    out["Property Subtype"] = out["Property Subtype"] or attom.get("property_sub_type") or (ai or {}).get("Property Subtype")

    # Ownership Type, Occupancy Status, Permit
    if harris:
        out["Ownership Type"] = pick(["Ownership Type", "Ownership"])
        out["Occupancy Status"] = pick(["Occupancy Status", "Occupancy"])
        out["Building Code / Permit ID"] = pick(["Permit", "Permit ID", "Building Permit", "Building Code / Permit ID"])

    # Fill nulls with AI (but do not override ATTOM/SCRAPED)
    if ai:
        for k in out:
            if not out[k]:
                out[k] = ai.get(k)

    return out


def build_location(attom: dict, scraped: dict | None, ai: dict | None, harris: bool) -> dict:
    """
    Build Location table using priority:
      Harris: SCRAPED > ATTOM > AI
      Non-Harris: ATTOM > AI
    """
    out = {f: None for f in LOCATION_FIELDS}

    def sget(*names):
        for n in names:
            v = (scraped or {}).get(n)
            if v:
                return v
        return None

    # ATTOM base
    out["Address Line 1"] = attom.get("street_name")
    out["Street Name"] = attom.get("street_name")
    out["City"] = attom.get("city")
    out["County"] = attom.get("county")
    out["State"] = attom.get("state")
    out["Postal Code"] = attom.get("zipcode")
    out["Latitude"] = attom.get("latitude")
    out["Longitude"] = attom.get("longitude")

    # Harris overrides from scraped
    if harris:
        out["Legal Description"] = sget("Legal Description", "Legal", "Legal Desc")
        out["Neighborhood Name"] = sget("Neighborhood Name", "Neighborhood")
        out["State Class Code"] = sget("State Class Code", "State Class", "Class Code")
        out["Tax District"] = sget("Tax District", "Taxing Jurisdictions", "Jurisdictions")
        out["Tax Code"] = sget("Tax Code", "Tax Code Area")

        # Sometimes HCAD has address block values:
        out["Address Line 1"] = sget("Site Address", "Situs Address") or out["Address Line 1"]
        out["City"] = sget("City") or out["City"]
        out["Postal Code"] = sget("Zip", "ZIP", "Zip Code") or out["Postal Code"]

    # Fill remaining using AI (only where null)
    if ai:
        for k in out:
            if not out[k]:
                out[k] = ai.get(k)

    return out


def render_sections_area(structured_md: str):
    """
    Under the tabs, show “Additional Data From County Site” and
    render each section as its own table.
    """
    st.markdown("### 📌 Additional Data From County Site")
    sections, _ = parse_structured_sections(structured_md)
    if not sections:
        st.info("No structured sections parsed.")
        return
    for sec_name, df in sections.items():
        # Cosmetic cleanup: ensure two columns present
        if list(df.columns)[:2] != ["Field", "Value"]:
            continue
        st.markdown(f"#### 🔸 {sec_name}")
        st.dataframe(df, use_container_width=True)


# =========================================
# APP
# =========================================
address_input = st.text_input("Enter Address")

if st.button("Run"):
    if not address_input.strip():
        st.error("Please enter an address.")
        st.stop()

    with st.spinner("1) Normalizing address..."):
        normalized = normalize_address(address_input)
        normalized = enforce_user_zip(address_input, normalized)

    with st.spinner("2) Fetching APN & County from ATTOM..."):
        attom = fetch_attom(normalized)

    if not attom:
        st.error("ATTOM did not return data for this address.")
        st.stop()

    county = (attom.get("county") or "").strip().lower()
    apn = attom.get("apn")

    structured_md = None
    flattened_scraped = None

    if county == "harris" and apn:
        with st.spinner("3) Harris detected → Scraping HCAD & structuring data..."):
            raw_text, structured_md = scrape_hcad_and_structure(apn)
        # Build scraped dict (flat Key→Value map) for field fetching and merging
        _, flattened_scraped = parse_structured_sections(structured_md)

    # 4) Field fetching + 5) AI fill (NULLs only), with conflicts resolved
    context_for_ai = {
        "address": normalized,
        "attom": attom,
        "scraped": flattened_scraped or {},
        "notes": "Fill only missing keys; if unknown use null."
    }
    ai_guesses = ai_fill_missing(context_for_ai, ALL_FIELDS)

    # Build final Identification and Location with priorities per county case
    is_harris = county == "harris" and structured_md is not None

    identification = build_identification(attom, flattened_scraped, ai_guesses, harris=is_harris)
    location = build_location(attom, flattened_scraped, ai_guesses, harris=is_harris)

    # 6) UI — Tabs first; then Additional County data (for Harris)
    tab_id, tab_loc = st.tabs(["🆔 Identification", "📍 Location"])

    with tab_id:
        st.dataframe(pd.DataFrame(identification.items(), columns=["Field", "Value"]),
                     use_container_width=True)

    with tab_loc:
        st.dataframe(pd.DataFrame(location.items(), columns=["Field", "Value"]),
                     use_container_width=True)

    if is_harris and structured_md:
        st.markdown("---")
        render_sections_area(structured_md)
    else:
        st.info("ℹ️ County is not Harris — showing ATTOM + AI-based Identification & Location only.")
import os
import time
import re
import json
import requests
import pandas as pd
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# =================================================
# ✅ Load Secrets
# =================================================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ATTOM_API_KEY = os.getenv("ATTOM_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# =================================================
# ✅ Streamlit UI
# =================================================
st.set_page_config(page_title="Revalix+", page_icon="🏠")
st.title("🏠 Revalix Property Intelligence System")


# =================================================
# ✅ Helper Functions
# =================================================

def extract_json_safe(text):
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            return {}
    return {}


# ✅ 1️⃣ Normalize Address
def normalize_address(address: str):
    prompt = f"""
    Return only JSON:
    {{
        "corrected_address": "<FULL NORMALIZED USPS FORMAT>"
    }}
    Address: "{address}"
    """
    res = client.responses.create(model="gpt-4.1-mini", input=prompt)
    data = extract_json_safe(res.output_text)
    return data.get("corrected_address", address)


# ✅ 2️⃣ ATTOM Data
def fetch_attom(address: str):
    try:
        street, rest = address.split(",", 1)
        city, st_zip = rest.rsplit(",", 1)
        state, zipcode = st_zip.strip().split()
    except:
        return {}
    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"apikey": ATTOM_API_KEY}
    params = {"address1": street.strip(),
              "address2": f"{city.strip()}, {state} {zipcode}"}

    r = requests.get(url, headers=headers, params=params)
    props = r.json().get("property", [])
    if not props: return {}

    p = props[0]
    return {
        "apn": p.get("identifier", {}).get("apn"),
        "street": p.get("address", {}).get("line1"),
        "city": p.get("address", {}).get("locality"),
        "county": p.get("area", {}).get("countrySecSubd"),
        "state": p.get("address", {}).get("countrySubd"),
        "zip": p.get("address", {}).get("postal1"),
        "lat": p.get("location", {}).get("latitude"),
        "lon": p.get("location", {}).get("longitude"),
        "property_type": p.get("summary", {}).get("propSubType"),
        "property_subtype": p.get("summary", {}).get("propType"),
    }


# ✅ 3️⃣ HCAD Scraping
def scrape_hcad(apn):
    URL = "https://arcweb.hcad.org/parcel-viewer-v2.0/"

    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    wait = WebDriverWait(driver, 60)

    driver.get(URL)
    time.sleep(6)

    try:
        wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//*[@id="mySurveyModal"]/div/span'))).click()
    except: pass

    search_box = wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "input.esri-input")))
    search_box.clear()
    search_box.send_keys(apn)

    search_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//button[contains(@class,'esri-search__submit-button')]")))
    driver.execute_script("arguments[0].click();", search_btn)

    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, ".esri-popup__main-container")))

    result_link = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "#popupTable tbody tr:nth-child(1) td:nth-child(2) a")))
    driver.execute_script("arguments[0].click();", result_link)
    time.sleep(3)
    driver.switch_to.window(driver.window_handles[-1])

    # ✅ Scroll
    last = 0
    while True:
        driver.execute_script("window.scrollBy(0,2000)")
        time.sleep(1.2)
        nh = driver.execute_script("return document.body.scrollHeight")
        if nh == last: break
        last = nh

    raw = driver.execute_script("return document.body.innerText")
    driver.quit()

    # ✅ 4️⃣ Send text to OpenAI for full structured tables
    prompt = f"""
    Convert all text into Markdown tables:
    - Each section header must start with "##"
    - Every data point must appear, no summarizing
    - Table: Field | Value
    Raw Data:
    \"\"\"{raw}\"\"\"
    """

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    structured = resp.choices[0].message.content.strip()
    return raw, structured


# ✅ New UI Sections Table Generator
def show_data_section(title, data_dict):
    st.markdown(f"### 🔹 {title}")
    st.dataframe(pd.DataFrame(data_dict.items(),
                              columns=["Field", "Value"]),
                 use_container_width=True)


# =================================================
# ✅ MAIN EXECUTION
# =================================================
address = st.text_input("Enter Property Address")

if st.button("Start"):
    if not address: st.stop()

    with st.spinner("Normalizing Address…"):
        normalized = normalize_address(address)

    with st.spinner("Fetching APN + Base Property Data…"):
        attom = fetch_attom(normalized)

    apn = attom.get("apn")

    if not apn:
        st.error("APN not found — Cannot process HCAD")
        st.stop()

    with st.spinner("Scraping County Property Records…"):
        raw, structured = scrape_hcad(apn)

    # ✅ Field Filling Engine: merge HCAD + ATTOM + AI logic
    final = attom.copy()  # ATTOM base

    # ✅ Extract Legal From Scraped Text (if available)
    legal_match = re.search(r"Legal Description.*", raw)
    if legal_match:
        final["legal_description"] = legal_match.group(0).split(":")[-1].strip()

    # ✅ First Display → Tabs
    tab1, tab2 = st.tabs(["🆔 Identification", "📍 Location"])

    with tab1:
        show_data_section("Identification", {
            "Property ID": apn,
            "Property Type": final.get("property_type"),
            "Property Subtype": final.get("property_subtype"),
            "Ownership Type": None,
            "Occupancy Status": None,
            "Building Code/Permit ID": None
        })

    with tab2:
        show_data_section("Location", {
            "Address Line 1": final.get("street"),
            "City": final.get("city"),
            "County": final.get("county"),
            "State": final.get("state"),
            "Postal Code": final.get("zip"),
            "Latitude": final.get("lat"),
            "Longitude": final.get("lon"),
            "Legal Description": final.get("legal_description")
        })

    # ✅ Additional HCAD Data Section
    st.markdown("---")
    st.subheader("📌 Additional Data (From County Site)")

    # ✅ Display Structured Tables by section
    sections = re.split(r"^## ", structured, flags=re.MULTILINE)
    for sec in sections:
        if not sec.strip(): continue
        lines = sec.split("\n")
        header = lines[0].strip()
        data_lines = "\n".join(lines[1:]).strip()
        if "|" not in data_lines: continue
        try:
            df = pd.read_csv(pd.io.common.StringIO(data_lines),
                             sep="|", engine="python")
            df = df.dropna(axis=1, how='all')
            df.columns = df.columns.str.strip()
            df = df.applymap(lambda x: str(x).strip() if isinstance(x, str) else x)
            st.markdown(f"### 🔸 {header}")
            st.dataframe(df, use_container_width=True)
        except:
            st.text_area(f"{header} Data", data_lines, height=250)

