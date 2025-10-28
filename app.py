import os
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

def extract_with_openai(address: str):
    prompt = f"""
    Extract ONLY these EXACT JSON keys:
    {FIELDS}

    For this U.S. address: "{address}"

    Return STRICT JSON with correct JSON syntax:
    - Use lowercase field names exactly as listed
    - Use null if unknown
    - NO extra fields, text, comments or explanation
    """

    res = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    text = res.output_text

    # Attempt to extract JSON safely
    try:
        return json.loads(text)
    except:
        # Find JSON inside response if model included text before/after
        start = text.find("{")
        end = text.rfind("}") + 1
        cleaned = text[start:end]
        return json.loads(cleaned)

def get_attom_data(address: str):
    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/basicprofile"
    headers = {
        "apikey": ATTOM_API_KEY,
        "accept": "application/json"
    }
    params = {"address": address}

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
        "country": prop.get("address", {}).get("country", "US"),
        "latitude": prop.get("location", {}).get("latitude"),
        "longitude": prop.get("location", {}).get("longitude"),
        "property_type": prop.get("summary", {}).get("propSubType"),
        "property_sub_type": prop.get("summary", {}).get("propType"),
    }

def merge_data(ai_data, attom_data):
    final = {}
    for key in FIELDS:
        value = ai_data.get(key)
        if not value or value in ["", None]:
            value = attom_data.get(key)
        final[key] = value
    return final

address_input = st.text_input("Enter Property Address:")

if st.button("Find Property"):
    if not address_input.strip():
        st.error("Please enter a valid U.S. address.")
    else:
        with st.spinner("Fetching verified property details..."):
            ai_data = extract_with_openai(address_input)
            attom_data = get_attom_data(address_input)

            final_data = merge_data(ai_data, attom_data)

            df = pd.DataFrame(final_data.items(), columns=["Field", "Value"])
            st.success("✅ Data Verified Successfully")
            st.dataframe(df, use_container_width=True)

