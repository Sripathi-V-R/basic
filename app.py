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

# ✅ STEP 1 — Correct messy address
def clean_address_with_openai(address: str):
    prompt = f"""
    You are an expert in correcting U.S. property addresses.

    Convert the following messy/incomplete input into a correct,
    standardized USPS-compliant full address:

    "{address}"

    Return ONLY this JSON:
    {{
        "corrected_address": "<FULL_ADDRESS>"
    }}

    Rules:
    - Ensure format: Street, City, State ZIP
    - NEVER return null values
    - No additional keys or comments
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


# ✅ STEP 2 — Extract Property Fields (AI fallback)
def extract_with_openai(address: str):
    prompt = f"""
    Extract property information for this U.S. address:

    "{address}"

    Return ONLY this JSON structure, lowercase keys:
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


# ✅ STEP 3 — Split + Pass Correct Params to ATTOM ✅
def get_attom_data(address: str):
    try:
        street, rest = address.split(",", 1)
        rest = rest.strip()
        city, state_zip = rest.rsplit(",", 1)
        state, zipcode = state_zip.strip().split()
    except:
        return {}

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    
    headers = {
        "apikey": ATTOM_API_KEY,
        "accept": "application/json"
    }

    params = {
        "address1": street.strip(),
        "address2": f"{city.strip()}, {state.strip()} {zipcode.strip()}"
    }

    r = requests.get(url, headers=headers, params=params)

    if r.status_code != 200:
        return {}

    data = r.json()
    if not data.get("property"):
        return {}

    prop = data["property"][0]

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


# ✅ STEP 4 — Merge AI + ATTOM Data
def merge_data(ai_data, attom_data):
    final = {}
    for key in FIELDS:
        value = attom_data.get(key) or ai_data.get(key)
        final[key] = value
    return final


# ✅ User Input UI
address_input = st.text_input("Enter Property Address:")

if st.button("Find Property"):
    if not address_input.strip():
        st.error("Please enter a valid U.S. address.")
    else:
        with st.spinner("Correcting address..."):
            corrected_address = clean_address_with_openai(address_input)

        st.info(f"📍 Corrected Address: **{corrected_address}**")

        with st.spinner("Fetching property details..."):
            ai_data = extract_with_openai(corrected_address)
            attom_data = get_attom_data(corrected_address)
            final_data = merge_data(ai_data, attom_data)

        df = pd.DataFrame(final_data.items(), columns=["Field", "Value"])
        st.success("✅ Data Verified Successfully")
        st.dataframe(df, use_container_width=True)
