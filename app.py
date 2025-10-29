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
