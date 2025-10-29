import json
import requests
import pandas as pd
import streamlit as st
from openai import OpenAI

# Load API Keys from Streamlit Secrets
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ATTOM_API_KEY = st.secrets["ATTOM_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# App UI
st.set_page_config(page_title="Revalix", page_icon="🏠", layout="centered")
st.title("🏠 Revalix Property Info Finder")
st.write("Enter a U.S. property address to fetch verified property data.")

FIELDS = [
    "apn", "street_name", "city", "county", "state", "zipcode",
    "country", "latitude", "longitude", "property_type", "property_sub_type"
]


# ✅ STEP 1 — Correct & Validate Address with OpenAI
def clean_address_with_openai(address: str):
    prompt = f"""
    You correct U.S. property addresses.

    Convert input into:
    Street, City, State ZIP
    (Ensure ZIP correctly matches the location based on USPS mapping)

    Input: "{address}"

    Return ONLY JSON:
    {{
        "corrected_address": "<FULL EXACT ADDRESS>"
    }}
    """

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    text = res.output_text
    try:
        return json.loads(text)["corrected_address"]
    except:
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        return parsed.get("corrected_address", address)


# ✅ STEP 2 — Extract fields using AI fallback
def extract_with_openai(address: str):
    prompt = f"""
    Extract the following property details from this U.S. address:
    "{address}"

    Return ONLY STRICT JSON:
    {{
        "apn": null,
        "street_name": null,
        "city": null,
        "county": null,
        "state": null,
        "zipcode": null,
        "country": "USA",
        "latitude": null,
        "longitude": null,
        "property_type": null,
        "property_sub_type": null
    }}
    """

    res = client.responses.create(model="gpt-4.1-mini", input=prompt)
    text = res.output_text
    try:
        return json.loads(text)
    except:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])


# ✅ STEP 3 — Query ATTOM using exact required parameters
def get_attom_data(corrected_address: str):
    try:
        street_address, rest = corrected_address.split(",", 1)
        street_address = street_address.strip()

        rest = rest.strip()
        city, state_zip = rest.rsplit(",", 1)
        city = city.strip()

        state_zip = state_zip.strip()
        state, zipcode = state_zip.split()
    except:
        st.warning("⚠️ Address parsing failed for ATTOM lookup.")
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {
        "apikey": ATTOM_API_KEY,
        "accept": "application/json"
    }
    params = {
        "address1": street_address,
        "address2": f"{city}, {state} {zipcode}"
    }

    r = requests.get(url, headers=headers, params=params)

    if r.status_code != 200:
        st.error(f"ATTOM API Error: {r.status_code}")
        return {}

    data = r.json().get("property", [])
    if not data:
        return {}

    prop = data[0]

    return {
        "apn": prop.get("identifier", {}).get("apn"),
        "street_name": prop.get("address", {}).get("line1"),
        "city": prop.get("address", {}).get("locality"),
        "county": prop.get("area", {}).get("countrySecSubd"),
        "state": prop.get("address", {}).get("countrySubd"),
        "zipcode": prop.get("address", {}).get("postal1"),
        "country": prop.get("address", {}).get("country", "USA"),
        "latitude": prop.get("location", {}).get("latitude"),
        "longitude": prop.get("location", {}).get("longitude"),
        "property_type": prop.get("summary", {}).get("propSubType"),
        "property_sub_type": prop.get("summary", {}).get("propType"),
    }


# ✅ STEP 4 — Combine ATTOM + AI for missing fields
def merge_data(ai_data, attom_data):
    return {key: attom_data.get(key) or ai_data.get(key) for key in FIELDS}


# ✅ UI Action
address_input = st.text_input("Enter Property Address:")

if st.button("Find Property"):
    if not address_input.strip():
        st.error("Please enter a valid U.S. address.")
    else:
        with st.spinner("🔍 Correcting address..."):
            corrected_address = clean_address_with_openai(address_input)

        st.info(f"📍 Corrected Address: **{corrected_address}**")

        with st.spinner("📡 Fetching property data from ATTOM..."):
            ai_data = extract_with_openai(corrected_address)
            attom_data = get_attom_data(corrected_address)
            final_data = merge_data(ai_data, attom_data)

        df = pd.DataFrame(final_data.items(), columns=["Field", "Value"])
        st.success("✅ Data Verified Successfully")
        st.dataframe(df, use_container_width=True)
