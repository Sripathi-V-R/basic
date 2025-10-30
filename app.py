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


# Load .env if available (local or from Render Secret File)
load_dotenv("/etc/secrets/.env")  # path Render mounts secret files to
load_dotenv()  # fallback local .env if running locally

# Try to get keys safely
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ATTOM_API_KEY = os.getenv("ATTOM_API_KEY")

# Optional fallback (for Streamlit Cloud only)
if not OPENAI_API_KEY or not ATTOM_API_KEY:
    try:
        OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
        ATTOM_API_KEY = st.secrets["ATTOM_API_KEY"]
    except Exception:
        pass

if not OPENAI_API_KEY or not ATTOM_API_KEY:
    st.error("❌ Missing API keys! Please check Render Secret File or Environment Variables.")
    st.stop()

from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

st.set_page_config(page_title="Revalix+ ", page_icon="🏠", layout="wide")
st.title("🏠 Revalix+ — Property Intelligence")

# Global field configuration
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
    "Map Facet", "Key Map", "Tax District", "Tax Code", "Location Type",
    "Country"
]

ALL_FIELDS = IDENTIFICATION_FIELDS + LOCATION_FIELDS


# =========================================
# Utilities
# =========================================
def extract_json_safe(text: str, fallback=None):
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            return fallback or {}
    return fallback or {}


def normalize_address(address: str) -> str:
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
    orig_zip = re.findall(r"\b\d{5}\b", original or "")
    corr_zip = re.findall(r"\b\d{5}\b", corrected or "")
    if orig_zip and corr_zip and orig_zip[0] != corr_zip[0]:
        corrected = corrected.replace(corr_zip[0], orig_zip[0])
        st.warning(f"ZIP overridden to user input: {orig_zip[0]}")
    return corrected


def fetch_attom(address: str) -> dict:
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
    r = requests.get(url, headers=headers, params=params, timeout=60)

    props = r.json().get("property", [])
    if not props:
        return {}

    p = props[0]
    out = {
        "apn": p.get("identifier", {}).get("apn"),
        "street_name": p.get("address", {}).get("line1"),
        "city": p.get("address", {}).get("locality"),
        "county": p.get("area", {}).get("countrySecSubd"),
        "state": p.get("address", {}).get("countrySubd"),
        "zipcode": p.get("address", {}).get("postal1"),
        "latitude": p.get("location", {}).get("latitude"),
        "longitude": p.get("location", {}).get("longitude"),
        "property_type": p.get("summary", {}).get("propSubType"),
        "property_sub_type": p.get("summary", {}).get("propType"),
        "owner_name": (p.get("owner1", {}) or {}).get("name"),
        "country": "USA"  # always USA
    }
    return out


# ======================================
# HCAD Scraping + GPT Structuring Engine
# ======================================
JUNK_PATTERNS = [
    r"\bPrint\b", r"\bEmail\b", r"\bFeedback\b", r"\bShare\b",
    r"\bLegend\b", r"\bLayers?\b", r"\bBasemap\b", r"\bMap Tools?\b",
    r"\bZoom In\b", r"\bZoom Out\b", r"\bMeasure\b", r"\bHelp\b",
    r"©.*?\d{4}", r"\bEsri\b", r"\bOpen ?Government\b", r"\bSign In\b",
    r"\bPrivacy\b", r"\bTerms\b", r"\bAbout\b"
]

def clean_garbage(text: str) -> str:
    for pat in JUNK_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    # collapse extra whitespace
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def scrape_hcad_and_structure(apn: str):
    URL = "https://arcweb.hcad.org/parcel-viewer-v2.0/"
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/google-chrome")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()),
                              options=chrome_options)
    wait = WebDriverWait(driver, 60)

    try:
        driver.get(URL)
        time.sleep(6)
        try:
            close = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="mySurveyModal"]/div/span')))
            driver.execute_script("arguments[0].click();", close)
        except:
            pass

        i = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input.esri-input")))
        i.clear()
        i.send_keys(apn)

        search_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@class,'esri-search__submit-button')]")))
        driver.execute_script("arguments[0].click();", search_btn)

        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".esri-popup__main-container")))
        link = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "#popupTable tbody tr:nth-child(1) td:nth-child(2) a")))
        driver.execute_script("arguments[0].click();", link)
        time.sleep(3)

        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(6)

        # Scroll to load all content
        last_h = 0
        while True:
            driver.execute_script("window.scrollBy(0,2000);")
            time.sleep(1)
            new_h = driver.execute_script("return document.body.scrollHeight")
            if new_h == last_h:
                break
            last_h = new_h

        raw = driver.execute_script("return document.body.innerText;")
    finally:
        try:
            driver.quit()
        except:
            pass

    raw = clean_garbage(raw)

    # Smarter GPT instruction for meaningful, normalized tables
    prompt = f"""
You are a real estate data structuring AI.

Input: Raw text from HCAD property page. It contains valuable data mixed with website UI text.
Your job:
- REMOVE ALL UI/website noise (buttons, credits, menus, social, logos).
- DETECT the real property sections, such as:
  Ownership Information, Situs/Property Address, Mailing Address, Legal Description,
  Land, Building/Improvements, Structures, Valuations, Jurisdictions, Exemptions,
  Sales/Deed History, Tax Info, Notes, Miscellaneous.

OUTPUT RULES:
- For each section output a header: ## <SECTION TITLE>
- Below it, a table with EXACTLY two columns: Field | Value
- Use clean, human-friendly field names (standardize cryptic labels).
- Split multi-values into separate rows.
- No duplicate rows. No blank rows. No UI garbage. Do not summarize.

Now transform the following text:

\"\"\"{raw}\"\"\"
"""
    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )
    structured = res.output_text.strip()
    return raw, structured


# ======================================
# Markdown → DataFrame + flatten
# ======================================
def markdown_table_to_df(md_table: str):
    if "|" not in md_table:
        return None
    try:
        # remove alignment rows like |---|---|
        cleaned = [ln for ln in md_table.splitlines()
                   if not re.match(r"^\s*\|?\s*[:-]+", ln)]
        df = pd.read_csv(StringIO("\n".join(cleaned)),
                         sep="|", engine="python", dtype=str, header=0)
        df = df.dropna(axis=1, how="all")
        df.columns = [c.strip() for c in df.columns]
        for c in df.columns:
            df[c] = df[c].fillna("").str.strip()
        # Always collapse to Field|Value
        if len(df.columns) > 2:
            df["Value"] = df[df.columns[1:]].agg(" | ".join, axis=1)
            df = df[[df.columns[0], "Value"]]
        elif len(df.columns) == 1:
            df["Value"] = ""
        df = df.rename(columns={df.columns[0]: "Field"})[["Field", "Value"]]
        df = df[df["Field"] != ""]
        return df.reset_index(drop=True)
    except:
        return None


def parse_structured_sections(structured_md: str):
    sections = {}
    flat = {}
    blocks = re.split(r"^##\s+", structured_md, flags=re.MULTILINE)
    for b in blocks:
        if not b.strip():
            continue
        lines = b.splitlines()
        name = lines[0].strip()
        md = "\n".join(lines[1:]).strip()
        df = markdown_table_to_df(md)
        if df is not None and not df.empty:
            # flatten
            for _, row in df.iterrows():
                flat[row["Field"]] = row["Value"]
            sections[name] = df
    return sections, flat


# ======================================
# AI helpers (fill/normalize)
# ======================================
def ai_fill_missing(context, keys):
    prompt = {
        "task": "Fill only missing. If unknown return null.",
        "fields": keys,
        "context": context
    }
    res = client.responses.create(model="gpt-4.1-mini", input=json.dumps(prompt))
    return extract_json_safe(res.output_text)


def normalize_field_names_with_ai(scraped_fields_dict: dict) -> dict:
    """
    Ask AI to map raw scraped keys to standardized field names.
    Returns a new dict with normalized keys.
    """
    prompt = {
        "task": "Map each key to a clean, standardized field name for appraisal tables.",
        "raw_keys": list(scraped_fields_dict.keys()),
        "return_format": "JSON mapping of raw_key -> normalized_key"
    }
    res = client.responses.create(model="gpt-4.1-mini", input=json.dumps(prompt))
    mapping = extract_json_safe(res.output_text, {})
    rebuilt = {}
    for k, v in scraped_fields_dict.items():
        rebuilt[mapping.get(k, k)] = v
    return rebuilt


def final_ai_fix(identification, location, address, apn, attom, scraped):
    missing_keys = [k for k in identification if not identification[k]] + \
                   [k for k in location if not location[k]]

    if not missing_keys:
        return identification, location

    payload = {
        "task": "Fill only these fields; if unknown, return null.",
        "address": address,
        "apn": apn,
        "attom": attom,
        "scraped": scraped,
        "fields_requested": missing_keys
    }
    res = client.responses.create(model="gpt-4.1-mini",
                                  input=json.dumps(payload))
    fix = extract_json_safe(res.output_text, {})

    # apply only where missing; then remove nulls
    for d in [identification, location]:
        for k in list(d.keys()):
            if not d[k]:
                v = fix.get(k)
                if v and str(v).strip().lower() != "null":
                    d[k] = v
                else:
                    d.pop(k)
    return identification, location


# ======================================
# BUILD TABLES (merge with priority)
# ======================================
def build_identification(attom, scraped, ai, harris):
    out = {f: None for f in IDENTIFICATION_FIELDS}

    def g(*xs):
        for x in xs:
            if scraped and scraped.get(x):
                return scraped[x]
        return None

    out["Property ID"] = g("Account Number", "APN", "Property ID") or attom.get("apn") or ai.get("Property ID")
    out["Owner Name"] = g("Owner", "Owner Name") or attom.get("owner_name") or ai.get("Owner Name")
    out["Property Type"] = g("Property Type") or attom.get("property_type") or ai.get("Property Type")
    out["Property Subtype"] = g("Property Subtype") or attom.get("property_sub_type") or ai.get("Property Subtype")

    if harris:
        out["Ownership Type"] = g("Ownership", "Ownership Type") or ai.get("Ownership Type")
        out["Occupancy Status"] = g("Occupancy", "Occupancy Status") or ai.get("Occupancy Status")
        out["Building Code / Permit ID"] = g("Permit", "Permit ID", "Building Permit") or ai.get("Building Code / Permit ID")

    return out


def build_location(attom, scraped, ai, harris):
    out = {f: None for f in LOCATION_FIELDS}

    def g(*xs):
        for x in xs:
            if scraped and scraped.get(x):
                return scraped[x]
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
    out["Country"] = "USA"

    if harris:
        out["Legal Description"] = g("Legal Description", "Legal")
        out["Neighborhood Name"] = g("Neighborhood Name", "Neighborhood")
        out["State Class Code"] = g("State Class Code", "State Class")
        out["Tax District"] = g("Tax District")
        out["Tax Code"] = g("Tax Code")
        # override situs details if present
        out["Address Line 1"] = g("Situs Address", "Site Address") or out["Address Line 1"]
        out["City"] = g("City") or out["City"]
        out["Postal Code"] = g("Zip", "ZIP") or out["Postal Code"]

    # Fill any remaining from AI
    for k, v in out.items():
        if not v and ai:
            out[k] = ai.get(k)
    out["Country"] = "USA"
    return out


# ======================================
# Show HCAD tables
# ======================================
def render_sections_area(md: str):
    st.markdown("### 📌 Additional Data From County Site")
    sections, _ = parse_structured_sections(md)
    for sec, df in sections.items():
        df.index = df.index + 1
        df.index.name = "S.No"
        st.markdown(f"#### 🔸 {sec}")
        st.dataframe(df, use_container_width=True)


# ======================================
# FORM (Enter to Run)
# ======================================
with st.form("search_form", clear_on_submit=False):
    address_input = st.text_input("Enter Address", placeholder="e.g., 8633 Eldridge Pkwy, Houston TX 77083")
    submitted = st.form_submit_button("Run (Press Enter ↵)")

if submitted:
    if not address_input.strip():
        st.error("Address required.")
        st.stop()

    with st.spinner("1️⃣ Normalize address..."):
        normalized = normalize_address(address_input)
        normalized = enforce_user_zip(address_input, normalized)

    with st.spinner("2️⃣ Fetch ATTOM data..."):
        attom = fetch_attom(normalized)

    if not attom.get("apn"):
        st.error("APN not found in ATTOM.")
        st.stop()

    county = (attom.get("county") or "").strip().lower()
    apn = attom["apn"]
    scraped_flat, structured_md = None, None

    if county == "harris":
        with st.spinner("3️⃣ Harris County detected — Scraping & structuring HCAD..."):
            _, structured_md = scrape_hcad_and_structure(apn)
        # Parse and flatten
        sections, scraped_flat = parse_structured_sections(structured_md)
        # Normalize scraped field names with AI for better merging
        with st.spinner("🧠 Normalizing scraped field names..."):
            scraped_flat = normalize_field_names_with_ai(scraped_flat)

    # Pre-fill AI for missing fields (context)
    with st.spinner("4️⃣ AI pre-fill (missing only)..."):
        ai_data = ai_fill_missing(
            {"address": normalized, "apn": apn, "attom": attom, "scraped": scraped_flat},
            ALL_FIELDS
        )

    harris = (county == "harris") and (structured_md is not None)

    with st.spinner("5️⃣ Merge data with priority..."):
        identification = build_identification(attom, scraped_flat, ai_data, harris)
        location = build_location(attom, scraped_flat, ai_data, harris)

    # Final AI fix & remove null fields
    with st.spinner("6️⃣ Final AI fix & cleanup..."):
        identification, location = final_ai_fix(
            identification, location,
            normalized, apn, attom, scraped_flat
        )

    # UI Tabs
    tab1, tab2 = st.tabs(["🆔 Identification", "📍 Location"])

    with tab1:
        df = pd.DataFrame(identification.items(), columns=["Field", "Value"])
        df.index = df.index + 1
        df.index.name = "S.No"
        st.dataframe(df, use_container_width=True)

    with tab2:
        df = pd.DataFrame(location.items(), columns=["Field", "Value"])
        df.index = df.index + 1
        df.index.name = "S.No"
        st.dataframe(df, use_container_width=True)

    if harris:
        st.markdown("---")
        render_sections_area(structured_md)
    else:
        st.info("ℹ️ Non-Harris: Only ATTOM + AI-based Identification & Location shown.")



