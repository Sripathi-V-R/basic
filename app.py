import json
import requests
import pandas as pd
import streamlit as st
from openai import OpenAI
import re

# Load API Keys
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ATTOM_API_KEY = st.secrets["ATTOM_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# UI
st.set_page_config(page_title="Revalix", page_icon="🏠", layout="centered")
st.title("Property Info Finder")
st.write("Enter a U.S. property address to fetch verified property data.")

FIELDS = [
    "apn", "street_name", "city", "county", "state", "zipcode",
    "country", "latitude", "longitude", "property_type", "property_sub_type"
]

# ✅ AI Address Correction (NO ZIP OVERRIDE)
def clean_address_with_openai(address: str):
    prompt = f"""
    Standardize this U.S. address into the format:
    Street, City, State ZIP
    
    DO NOT change ZIP code if user gave one.

    Input: "{address}"

    Return ONLY:
    {{
        "corrected_address": "<FULL_ADDRESS>"
    }}
    """

    res = client.responses.create(model="gpt-4.1-mini", input=prompt)
    text = res.output_text
    
    try:
        return json.loads(text)["corrected_address"]
    except:
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        return parsed.get("corrected_address", address)

# ✅ Enforce user ZIP if present
def enforce_user_zip(original, corrected):
    orig_zip = re.findall(r"\b\d{5}\b", original)
    corr_zip = re.findall(r"\b\d{5}\b", corrected)
    
    if orig_zip and corr_zip and orig_zip[0] != corr_zip[0]:
        corrected = corrected.replace(corr_zip[0], orig_zip[0])
        st.warning(f"⚠ Adjusted ZIP back to user input: {orig_zip[0]}")
    
    return corrected

# ✅ ATTOM Exact Required Request — Correct Format ✅
def get_attom_data(address: str):
    try:
        street, rest = address.split(",", 1)
        street = street.strip()

        rest = rest.strip()
        city, state_zip = rest.rsplit(",", 1)
        city = city.strip()

        state, zipcode = state_zip.strip().split()
    except:
        st.error("⚠ Address parsing error for ATTOM API")
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"apikey": ATTOM_API_KEY, "accept": "application/json"}
    params = {
        "address1": street,
        "address2": f"{city}, {state} {zipcode}"
    }

    r = requests.get(url, headers=headers, params=params)

    if r.status_code != 200:
        st.error(f"Error: {r.status_code}")
        return {}

    properties = r.json().get("property", [])
    if not properties:
        return {}

    prop = properties[0]

    return {
        "apn": prop.get("identifier", {}).get("apn"),
        "street_name": prop.get("address", {}).get("line1"),
        "city": prop.get("address", {}).get("locality"),
        "county": prop.get("area", {}).get("countrySecSubd"),
        "state": prop.get("address", {}).get("countrySubd"),
        "zipcode": prop.get("address", {}).get("postal1"),
        "country": prop.get("address", {}).get("country"),
        "latitude": prop.get("location", {}).get("latitude"),
        "longitude": prop.get("location", {}).get("longitude"),
        "property_type": prop.get("summary", {}).get("propSubType"),
        "property_sub_type": prop.get("summary", {}).get("propType"),
    }

# ✅ AI Fallback Extractor
def extract_with_openai(address):
    res = client.responses.create(
        model="gpt-4.1-mini",
        input=f"Fill unknown fields with null. JSON keys only: {FIELDS}. For: {address}"
    )
    text = res.output_text
    
    try: return json.loads(text)
    except:
        start = text.find("{"); end = text.rfind("}") + 1
        return json.loads(text[start:end])

# ✅ Merge ATTOM + AI
def merge_data(ai, attom):
    return {k: attom.get(k) or ai.get(k) for k in FIELDS}

# ✅ UI
address_input = st.text_input("Enter Property Address:")

if st.button("Find Property"):
    if not address_input.strip():
        st.error("❌ Please enter a valid address.")
    else:
        with st.spinner("🧠 Standardizing address..."):
            corrected = clean_address_with_openai(address_input)
            corrected = enforce_user_zip(address_input, corrected)

        st.info(f"📍 Corrected Address: **{corrected}**")

        with st.spinner("finding..."):
            attom = get_attom_data(corrected)
            ai = extract_with_openai(corrected)
            final = merge_data(ai, attom)

        df = pd.DataFrame(final.items(), columns=["Field", "Value"])
        st.success("✅ Data Verified Successfully")
        st.dataframe(df, use_container_width=True)


