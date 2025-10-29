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


# ✅ STEP 1 — Correct messy address with AI
def clean_address_with_openai(address: str):
    prompt = f"""
    Fix and standardize this U.S. address into a valid USPS-compliant address:

    "{address}"

    Ensure output format: Street, City, State ZIP
    Validate ZIP belongs geographically to city/street. Correct if mismatched.

    Return ONLY JSON:
    {{
        "corrected_address": "<FULL_ADDRESS>"
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


# ✅ STEP 2 — Extract as fallback using OpenAI
def extract_with_openai(address: str):
    prompt = f"""
    Extract property info for this U.S. address:
    "{address}"

    Only return this JSON:
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

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    text = res.output_text
    try:
        return json.loads(text)
    except:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])


# ✅ STEP 3 — Split + ATTOM API
def get_attom_data(corrected_address: str):
    try:
        street_address, rest = corrected_address.split(",", 1)
        street_address = street_address.strip()

        rest = rest.strip()
        city, state_zip = rest.rsplit(",", 1)

        city = city.strip()
        state_zip = state_zip.strip()
        state, zipcode = state_zip.split()

        state = state.strip()
        zipcode = zipcode.strip()
    except Exception:
        st.warning("⚠️ Address parsing issue for ATTOM lookup.")
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {"apikey": ATTOM_API_KEY, "accept": "application/json"}
    params = {
        "address1": street_address,
        "address2": f"{city}, {state} {zipcode}"
    }

    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        return {}

    data = r.json()
    properties = data.get("property", [])
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
        "country": prop.get("address", {}).get("country", "USA"),
        "latitude": prop.get("location", {}).get("latitude"),
        "longitude": prop.get("location", {}).get("longitude"),
        "property_type": prop.get("summary", {}).get("propSubType"),
        "property_sub_type": prop.get("summary", {}).get("propType"),
    }


# ✅ STEP 4 — Merge ATTOM + AI
def merge_data(ai_data, attom_data):
    final = {}
    for key in FIELDS:
        final[key] = attom_data.get(key) or ai_data.get(key)
    return final


# ✅ UI Actions
address_input = st.text_input("Enter Property Address:")

if st.button("Find Property"):
    if not address_input.strip():
        st.error("❌ Please enter a valid U.S. address.")
    else:
        with st.spinner("🧠 Correcting address..."):
            corrected_address = clean_address_with_openai(address_input)

        st.info(f"📍 Corrected Address: **{corrected_address}**")

        with st.spinner("🔍 Fetching property data..."):
            ai_data = extract_with_openai(corrected_address)
            attom_data = get_attom_data(corrected_address)
            final_data = merge_data(ai_data, attom_data)

        df = pd.DataFrame(final_data.items(), columns=["Field", "Value"])
        st.success("✅ Data Verified Successfully")
        st.dataframe(df, use_container_width=True)
