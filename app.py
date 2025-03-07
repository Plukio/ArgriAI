import streamlit as st
import requests
import ee
import pandas as pd
from datetime import date, timedelta, datetime, timezone
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer
from google.oauth2 import service_account
from meteostat import Point, Daily
import plotly.express as px
from streamlit_elements import elements, mui, html

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
# Weather Data Module (Meteostat for improved accuracy)
# ============================================================
def fetch_weather_meteostat(lat, lon, start_date, end_date):
    # Ensure start_date and end_date are datetime objects
    if isinstance(start_date, date) and not isinstance(start_date, datetime):
        start_date = datetime.combine(start_date, datetime.min.time())
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_date = datetime.combine(end_date, datetime.min.time())
    location = Point(lat, lon)
    data = Daily(location, start_date, end_date)
    data = data.fetch()
    if data.empty:
        st.error("No weather data available from Meteostat.")
        return None
    data = data.reset_index()
    data.set_index("time", inplace=True)
    return data

# ============================================================
# Remote Sensing Module: NDVI Time Series from Sentinel-2 via Earth Engine
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
            # Convert using timezone-aware fromtimestamp
            image_date = datetime.fromtimestamp(timestamp/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            ndvi_value = props.get("NDVI")
            records.append({"Date": image_date, "NDVI": ndvi_value})
    df = pd.DataFrame(records)
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"])
        df.sort_values("Date", inplace=True)
    return df

# ============================================================
# Remote Sensing Module: Soil Moisture Time Series from SMAP via Earth Engine
# ============================================================
def fetch_soil_moisture_timeseries(geojson, start_date, end_date):
    geometry = ee.Geometry(geojson)
    collection = ee.ImageCollection("NASA/SMAP/SPL3SMP") \
                   .filterBounds(geometry) \
                   .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")) \
                   .sort("system:time_start")
    
    def compute_sm(image):
        ssm = image.select('ssm')
        mean_ssm = ssm.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geometry,
            scale=1000
        )
        return image.set('ssm', mean_ssm.get('ssm'))
    
    collection = collection.map(compute_sm)
    image_list = collection.toList(collection.size())
    records = []
    count = image_list.size().getInfo()
    for i in range(count):
        image = ee.Image(image_list.get(i))
        props = image.getInfo().get('properties', {})
        timestamp = props.get("system:time_start")
        if timestamp is not None:
            image_date = datetime.fromtimestamp(timestamp/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            ssm_value = props.get("ssm")
            records.append({"Date": image_date, "Soil_Moisture": ssm_value})
    df = pd.DataFrame(records)
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"])
        df.sort_values("Date", inplace=True)
    return df

# ============================================================
# Field Module: Calculate Polygon Area from GeoJSON (Optional)
# ============================================================
def calculate_polygon_area(geojson):
    geom = shape(geojson)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_geom = transform(transformer.transform, geom)
    area_m2 = projected_geom.area
    return area_m2 / 10000.0  # converts m² to hectares

# ============================================================
# Main Application: Layout with Map and Time Series Charts using Streamlit Elements
# ============================================================
st.title("Remote Sensing Time Series")
st.markdown("Draw your field boundary on the map to display NDVI, Temperature, and Soil Moisture time series for the past 3 months.")

# Sidebar: Location defaults
lat = st.sidebar.number_input("Latitude", value=10.0, format="%.6f")
lon = st.sidebar.number_input("Longitude", value=105.0, format="%.6f")

# Create folium map with drawing tool
m = folium.Map(location=[lat, lon], zoom_start=15)
from folium.plugins import Draw
draw = Draw(export=True, draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False},
            edit_options={"edit": True})
draw.add_to(m)
# Use st_folium to capture drawn geometry
map_data = st_folium(m, width=700, height=500)

if map_data and map_data.get("all_drawings"):
    drawings = map_data["all_drawings"]
    if drawings:
        geojson = drawings[0]["geometry"]
        field_area = calculate_polygon_area(geojson)
        st.success(f"Field area from drawn polygon: {field_area:.2f} hectares")
        
        # Define time period: past 3 months
        today = date.today()
        three_months_ago = today - timedelta(days=90)
        
        # Fetch time series data
        ndvi_df = fetch_ndvi_timeseries(geojson, three_months_ago, today)
        # For weather, use the centroid of the polygon
        poly = shape(geojson)
        centroid = poly.centroid
        weather_df = fetch_weather_meteostat(centroid.y, centroid.x, three_months_ago, today)
        sm_df = fetch_soil_moisture_timeseries(geojson, three_months_ago, today)
        
        # Create Plotly figures (if data available)
        if not ndvi_df.empty:
            ndvi_fig = px.line(ndvi_df, x="Date", y="NDVI", title="NDVI Time Series")
        else:
            ndvi_fig = None
        
        if weather_df is not None and not weather_df.empty:
            weather_df = weather_df.reset_index().rename(columns={"time": "Date"})
            temp_fig = px.line(weather_df, x="Date", y=["tmax", "tmin"], title="Temperature Time Series")
        else:
            temp_fig = None
        
        if not sm_df.empty:
            sm_fig = px.line(sm_df, x="Date", y="Soil_Moisture", title="Soil Moisture Time Series")
        else:
            sm_fig = None
        
        # Get the map HTML for embedding
        map_html = m._repr_html_()
        
        # Arrange the map and charts in a grid using Streamlit Elements
        with elements("layout"):
            mui.Grid(
                container=True,
                spacing=2,
                children=[
                    mui.Grid(
                        item=True,
                        xs=6,
                        children=[
                            html.Iframe(srcDoc=map_html,
                                        style={"width": "100%", "height": "500px", "border": "none"})
                        ]
                    ),
                    mui.Grid(
                        item=True,
                        xs=6,
                        children=[
                            mui.Tabs(
                                value="ndvi",
                                children=[
                                    mui.Tab(
                                        label="NDVI",
                                        value="ndvi",
                                        children=[
                                            html.Iframe(srcDoc=ndvi_fig.to_html() if ndvi_fig is not None else "<p>No NDVI data available.</p>",
                                                        style={"width": "100%", "height": "400px", "border": "none"})
                                        ]
                                    ),
                                    mui.Tab(
                                        label="Temperature",
                                        value="temp",
                                        children=[
                                            html.Iframe(srcDoc=temp_fig.to_html() if temp_fig is not None else "<p>No Temperature data available.</p>",
                                                        style={"width": "100%", "height": "400px", "border": "none"})
                                        ]
                                    ),
                                    mui.Tab(
                                        label="Soil Moisture",
                                        value="soil",
                                        children=[
                                            html.Iframe(srcDoc=sm_fig.to_html() if sm_fig is not None else "<p>No Soil Moisture data available.</p>",
                                                        style={"width": "100%", "height": "400px", "border": "none"})
                                        ]
                                    ),
                                ]
                            )
                        ]
                    ),
                ]
            )
    else:
        st.info("No field boundary drawn. Please draw your field on the map.")
else:
    st.info("Draw your field boundary on the map to see time series data.")
