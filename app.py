import streamlit as st
import requests
import ee
import pandas as pd
from datetime import date, timedelta, datetime
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer
from google.oauth2 import service_account

# ============================================================
# Earth Engine Authentication Using Service Account from st.secrets
# ============================================================
def ee_authentication():
    service_account_info = st.secrets["gee_service_account"]
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials)

try:
    ee_authentication()
    st.success("Google Earth Engine initialized successfully!")
except Exception as e:
    st.error("Error initializing Earth Engine: " + str(e))
    st.stop()

# ============================================================
# Weather Data Module (NASA POWER)
# ============================================================
def fetch_full_weather_nasa(lat, lon, start_date, end_date):
    base_url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    params = {
        "parameters": "T2M_MAX,T2M_MIN",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": start_str,
        "end": end_str,
        "format": "JSON",
    }
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        data = response.json()
        try:
            weather_params = data["properties"]["parameter"]
            dates = list(weather_params["T2M_MAX"].keys())
            records = []
            for dt in dates:
                record = {
                    "Date": pd.to_datetime(dt, format="%Y%m%d"),
                    "Tmax": float(weather_params["T2M_MAX"][dt]),
                    "Tmin": float(weather_params["T2M_MIN"][dt])
                }
                records.append(record)
            df = pd.DataFrame(records)
            df.set_index("Date", inplace=True)
            return df
        except Exception as e:
            st.error("Error parsing NASA POWER weather data.")
            return None
    else:
        st.error("Error fetching weather data from NASA POWER.")
        return None

# ============================================================
# Remote Sensing Module: NDVI Time Series for Drawn Area
# ============================================================
def fetch_ndvi_timeseries(geojson, start_date, end_date):
    geometry = ee.Geometry(geojson)
    collection = ee.ImageCollection("COPERNICUS/S2_SR") \
                    .filterBounds(geometry) \
                    .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) \
                    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20)) \
                    .sort("system:time_start")
                    
    def compute_ndvi(image):
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        mean_ndvi = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geometry,
            scale=10
        )
        return image.set("NDVI", mean_ndvi.get("NDVI"))
    
    collection = collection.map(compute_ndvi)
    image_list = collection.toList(collection.size())
    records = []
    count = image_list.size().getInfo()
    for i in range(count):
        image = ee.Image(image_list.get(i))
        props = image.getInfo().get('properties', {})
        timestamp = props.get("system:time_start")
        if timestamp is not None:
            image_date = datetime.utcfromtimestamp(timestamp/1000).strftime("%Y-%m-%d")
            ndvi_value = props.get("NDVI")
            records.append({"Date": image_date, "NDVI": ndvi_value})
    df = pd.DataFrame(records)
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"])
        df.sort_values("Date", inplace=True)
    return df

# ============================================================
# Field Module: Calculate Area from GeoJSON (Optional)
# ============================================================
def calculate_polygon_area(geojson):
    geom = shape(geojson)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_geom = transform(transformer.transform, geom)
    area_m2 = projected_geom.area
    return area_m2 / 10000.0  # converts m² to hectares

# ============================================================
# Main Application: Remote Sensing Time Series
# ============================================================
st.title("Remote Sensing Time Series")
st.markdown("Draw your field boundary on the map to display NDVI and Temperature time series for the past 3 months.")

# Sidebar for location defaults
lat = st.sidebar.number_input("Latitude", value=10.0, format="%.6f")
lon = st.sidebar.number_input("Longitude", value=105.0, format="%.6f")

# Create folium map with drawing tool
m = folium.Map(location=[lat, lon], zoom_start=15)
from folium.plugins import Draw
draw = Draw(export=True, draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False},
            edit_options={"edit": True})
draw.add_to(m)
map_data = st_folium(m, width=700, height=500)

# Once the user draws a polygon, display the time series charts
if map_data and map_data.get("all_drawings"):
    drawings = map_data["all_drawings"]
    if drawings:
        geojson = drawings[0]["geometry"]
        field_area = calculate_polygon_area(geojson)
        st.success(f"Field area from drawn polygon: {field_area:.2f} hectares")
        
        # Define time period for the past three months
        today = date.today()
        three_months_ago = today - timedelta(days=90)
        
        # NDVI time series
        st.markdown("#### NDVI Time Series")
        ndvi_df = fetch_ndvi_timeseries(geojson, three_months_ago, today)
        if not ndvi_df.empty:
            st.line_chart(ndvi_df.set_index("Date"))
        else:
            st.info("No NDVI data available for the selected area and time period.")
        
        # Temperature time series using the centroid of the drawn polygon
        st.markdown("#### Temperature Time Series")
        poly = shape(geojson)
        centroid = poly.centroid
        temp_df = fetch_full_weather_nasa(centroid.y, centroid.x, three_months_ago, today)
        if temp_df is not None and not temp_df.empty:
            st.line_chart(temp_df[["Tmax", "Tmin"]])
        else:
            st.info("No temperature data available for the selected area and time period.")
    else:
        st.info("No field boundary drawn. Please draw your field on the map.")
else:
    st.info("Draw your field boundary on the map to see time series data.")
