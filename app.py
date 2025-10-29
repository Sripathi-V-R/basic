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
# ✅ API Key Load (Supports .env + Streamlit Cloud)
# =========================================
load_dotenv()

if "OPENAI_API_KEY" in st.secrets:
    OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
else:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if "ATTOM_API_KEY" in st.secrets:
    ATTOM_API_KEY = st.secrets["ATTOM_API_KEY"]
else:
    ATTOM_API_KEY = os.getenv("ATTOM_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================================
# ✅ Streamlit UI Setup
# =========================================
st.set_page_config(page_title="Revalix+ (HCAD)", page_icon="🏠", layout="wide")
st.title("🏠 Revalix+ — Property Intelligence (ATTOM + HCAD + OpenAI)")

# ✅ Field Configurations
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
# ✅ Utility Functions
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
    Return only JSON:
    {{"corrected_address": "<FULL USPS FORMAT>"}}
    Input: "{address}"
    """
    res = client.responses.create(model="gpt-4.1-mini", input=prompt)
    d = extract_json_safe(res.output_text, {"corrected_address": address})
    return d.get("corrected_address", address)


def enforce_user_zip(original: str, corrected: str) -> str:
    orig_zip = re.findall(r"\b\d{5}\b", original)
    corr_zip = re.findall(r"\b\d{5}\b", corrected)
    if orig_zip and corr_zip and orig_zip[0] != corr_zip[0]:
        corrected = corrected.replace(corr_zip[0], orig_zip[0])
        st.info(f"ZIP reset to: {orig_zip[0]}")
    return corrected


# =========================================
# ✅ ATTOM Lookup
# =========================================
def fetch_attom(address: str):
    try:
        street, rest = address.split(",", 1)
        city, st_zip = rest.rsplit(",", 1)
        state, zipcode = st_zip.split()
    except:
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"apikey": ATTOM_API_KEY}
    params = {"address1": street.strip(),
              "address2": f"{city.strip()}, {state} {zipcode}"}

    r = requests.get(url, headers=headers, params=params)
    p_list = r.json().get("property", [])
    if not p_list: return {}

    p = p_list[0]

    return {
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
        "country": "USA"
    }


# =========================================
# ✅ HCAD Scraper + Table Structuring (Smart AI)
# =========================================
JUNK_PATTERNS = [
    r"Print|Email|Share|Legend|Layers?|Basemap|Map Tools?|Zoom|Measure|Help",
    r"©.*", r"Esri", r"Sign In|Privacy|Terms|Feedback"
]


def clean_garbage(text):
    for pat in JUNK_PATTERNS:
        text = re.sub(pat, "", text, flags=re.I)

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def scrape_hcad_and_structure(apn: str):
    URL = "https://arcweb.hcad.org/parcel-viewer-v2.0/"
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    wait = WebDriverWait(driver, 50)

    try:
        driver.get(URL)
        time.sleep(5)
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="mySurveyModal"]/div/span')))
            btn.click()
        except: pass

        i = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input.esri-input")))
        i.send_keys(apn)

        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'esri-search__submit-button')]")))
        btn.click()
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".esri-popup__main-container")))

        link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#popupTable tbody tr td a")))
        link.click()
        time.sleep(5)

        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(5)

        for _ in range(7):
            driver.execute_script("window.scrollBy(0,3000);")
            time.sleep(1)

        raw = driver.execute_script("return document.body.innerText;")

    finally:
        driver.quit()

    raw = clean_garbage(raw)

    prompt = f"""
You extract useful real estate property data.

Remove ALL GIS/website UI garbage. Only extract real data.

Group data into meaningful sections like:
Ownership, Situs Address, Mailing Address, Legal Description, Improvements/Structures,
Valuations, Land, Jurisdictions, Exemptions, Sales History, Tax Info, Notes.

Output Format:
## SECTION NAME
| Field | Value |
|------|------|
...data...

No missing fields left unstructured.

RAW:
\"\"\"{raw}\"\"\"
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    return resp.output_text.strip()


# =========================================
# ✅ Markdown Parsing
# =========================================
def markdown_table_to_df(md):
    if "|" not in md:
        return None
    lines = [ln for ln in md.splitlines() if "---" not in ln]
    try:
        df = pd.read_csv(StringIO("\n".join(lines)),
                         sep="|", engine="python", header=0, dtype=str)
        df = df.dropna(axis=1, how="all")
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={df.columns[0]: "Field", df.columns[-1]: "Value"})[["Field","Value"]]
        df = df[df["Field"] != ""]
        return df
    except:
        return None


def parse_structured_sections(md):
    sections = {}
    flat = {}
    blocks = re.split(r"^##\s+", md, flags=re.M)
    for b in blocks:
        if not b.strip(): continue
        lines = b.splitlines()
        sec = lines[0].strip()
        tbl = "\n".join(lines[1:]).strip()
        df = markdown_table_to_df(tbl)
        if df is None: continue
        for _, r in df.iterrows():
            flat[r["Field"]] = r["Value"]
        sections[sec] = df
    return sections, flat


# =========================================
# ✅ AI Fill + Normalize
# =========================================
def ai_fill_missing(ctx, keys):
    prompt = {"fields": keys, "context": ctx}
    r = client.responses.create(model="gpt-4.1-mini", input=json.dumps(prompt))
    return extract_json_safe(r.output_text, {})


def final_ai_fix(idn, loc, n_addr, apn, attom, scraped):
    missing = [k for k in idn if not idn[k]] + [k for k in loc if not loc[k]]
    if not missing: return idn, loc

    req = {
        "address": n_addr,
        "apn": apn,
        "attom": attom,
        "scraped": scraped,
        "fields": missing
    }
    r = client.responses.create(model="gpt-4.1-mini", input=json.dumps(req))
    fix = extract_json_safe(r.output_text, {})

    for d in [idn, loc]:
        for k in list(d.keys()):
            if not d[k]:
                v = fix.get(k)
                if v: d[k] = v
                else: d.pop(k)
    return idn, loc


# =========================================
# ✅ Build Identification + Location
# =========================================
def merge_fields(attom, scraped, ai, fields):
    out = {}
    for f in fields:
        out[f] = scraped.get(f) if scraped and f in scraped else \
                  attom.get(f.lower()) if attom and f.lower() in attom else \
                  ai.get(f)
    return out


# =========================================
# ✅ Streamlit Form (ENTER to RUN)
# =========================================
with st.form("search", clear_on_submit=False):
    address = st.text_input("Enter Address", placeholder="123 Main St, City ST ZIP")
    submit = st.form_submit_button("Run (Press Enter)")

if submit and address.strip():
    with st.spinner("Normalize..."):
        n_addr = normalize_address(address)
        n_addr = enforce_user_zip(address, n_addr)

    with st.spinner("Lookup ATTOM..."):
        attom = fetch_attom(n_addr)

    if not attom.get("apn"):
        st.error("APN not found. Try a full address.")
        st.stop()

    county = attom.get("county", "").lower()
    apn = attom["apn"]
    scraped_sections = {}
    scraped_flat = {}

    if county == "harris":
        with st.spinner("Scraping HCAD + AI Structuring..."):
            structured_md = scrape_hcad_and_structure(apn)
        scraped_sections, scraped_flat = parse_structured_sections(structured_md)

    with st.spinner("AI Fill..."):
        ai_data = ai_fill_missing({"address": n_addr, "attom": attom, "scraped": scraped_flat}, ALL_FIELDS)

    harris = bool(scraped_sections)

    with st.spinner("Merge..."):
        identification = merge_fields(attom, scraped_flat, ai_data, IDENTIFICATION_FIELDS)
        location = merge_fields(attom, scraped_flat, ai_data, LOCATION_FIELDS)

    with st.spinner("Final AI Clean..."):
        identification, location = final_ai_fix(identification, location, n_addr, apn, attom, scraped_flat)

    # ✅ UI: Tabs
    t1, t2 = st.tabs(["🆔 Identification", "📍 Location"])
    with t1:
        df = pd.DataFrame(identification.items(), columns=["Field", "Value"])
        df.index += 1; df.index.name = "S.No"
        st.dataframe(df, use_container_width=True)

    with t2:
        df = pd.DataFrame(location.items(), columns=["Field", "Value"])
        df.index += 1; df.index.name = "S.No"
        st.dataframe(df, use_container_width=True)

    if harris:
        st.markdown("---")
        st.subheader("📌 Additional County Data")
        for sec, df in scraped_sections.items():
            df.index += 1; df.index.name = "S.No"
            st.markdown(f"#### 🔸 {sec}")
            st.dataframe(df, use_container_width=True)
    else:
        st.info("ℹ️ Not Harris County — ATTOM + AI only.")
