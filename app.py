import streamlit as st
import folium
from folium.plugins import Draw
from streamlit_folium import st_folium
import plotly.express as px
import pandas as pd
from datetime import date, datetime, timedelta, timezone
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer
from meteostat import Point, Daily
import ee
from google.oauth2 import service_account
from streamlit_elements import elements, mui, html

# ------------------------------------------------------------
# 1. Configure Streamlit for wide layout
# ------------------------------------------------------------
st.set_page_config(layout="wide")

# ------------------------------------------------------------
# 2. Session State Initialization
# ------------------------------------------------------------
if "show_map" not in st.session_state:
    st.session_state.show_map = True  # True means show the map, False means show the charts
if "geometry" not in st.session_state:
    st.session_state.geometry = None
if "ndvi_df" not in st.session_state:
    st.session_state.ndvi_df = pd.DataFrame()
if "weather_df" not in st.session_state:
    st.session_state.weather_df = pd.DataFrame()

# ------------------------------------------------------------
# Earth Engine Authentication
# ------------------------------------------------------------
def ee_authentication():
    service_account_info = st.secrets["gee_service_account"]
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials)

try:
    ee_authentication()
except Exception as e:
    st.error(f"Error initializing Earth Engine: {e}")
    st.stop()

# ------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------
def calculate_polygon_area(geojson):
    geom = shape(geojson)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_geom = transform(transformer.transform, geom)
    return projected_geom.area / 10000.0  # m² to hectares

def fetch_ndvi_timeseries(geojson, start_date, end_date):
    """
    For the given geometry and date range, first retrieves the raw Sentinel-2 SR
    collection (selecting only bands B4 and B8), then for each day in the range,
    creates a median composite of that day and computes NDVI from the composite.
    Returns a pandas DataFrame with one NDVI value per day.
    """

    geometry = ee.Geometry(geojson)
    # Filter the raw collection to just B4 and B8 (red and NIR)
    collection = (ee.ImageCollection("COPERNICUS/S2_SR")
                  .filterBounds(geometry)
                  .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
                  .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
                  .select(["B4", "B8"])
                  .sort("system:time_start"))
    
    # Generate a list of days in the period
    nDays = (end_date - start_date).days + 1
    dateList = [start_date + timedelta(days=i) for i in range(nDays)]
    
    def composite_for_day(day):
        # Create start and end dates for the day
        start = ee.Date(day.strftime("%Y-%m-%d"))
        end = start.advance(1, "day")
        # Filter to images within that day
        daily = collection.filterDate(start, end)
        # Create a median composite to reduce noise and gaps
        composite = daily.median()
        # Compute NDVI from the composite: NDVI = (B8 - B4) / (B8 + B4)
        ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
        # Set the time property to the start of the day
        return ndvi.set("system:time_start", start.millis())
    
    # Build a new image collection of daily NDVI images
    daily_ndvi = ee.ImageCollection([composite_for_day(d) for d in dateList])
    
    # Get the size and if there is no data, return an empty DataFrame
    size = daily_ndvi.size().getInfo()
    if size <= 0:
        return pd.DataFrame()
    
    image_list = daily_ndvi.toList(size)
    records = []
    for i in range(size):
        img = ee.Image(image_list.get(i))
        props = img.getInfo().get("properties", {})
        ts = props.get("system:time_start")
        if ts is not None:
            # Convert timestamp to a date string
            dt_str = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            ndvi_val = props.get("NDVI")
            records.append({"Date": dt_str, "NDVI": ndvi_val})
    
    df = pd.DataFrame(records)
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"])
        df.sort_values("Date", inplace=True)
    return df


def fetch_weather_meteostat(lat, lon, start_date, end_date):
    # Ensure datetime objects
    if isinstance(start_date, date) and not isinstance(start_date, datetime):
        start_date = datetime.combine(start_date, datetime.min.time())
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_date = datetime.combine(end_date, datetime.min.time())

    location = Point(lat, lon)
    data = Daily(location, start_date, end_date).fetch()
    if data.empty:
        return None
    data = data.reset_index()
    data.set_index("time", inplace=True)
    return data

# ------------------------------------------------------------
# Main UI
# ------------------------------------------------------------
st.title("Remote Sensing Time Series")
st.markdown("Draw your field boundary on the map to display NDVI and Temperature time series for the past 3 months.")

# Sidebar for default location
lat = st.sidebar.number_input("Latitude", value=15.8700, format="%.6f")
lon = st.sidebar.number_input("Longitude", value=100.9925, format="%.6f")

def draw_map():
    """Renders the Folium map and processes the drawn geometry."""
    m = folium.Map(location=[lat, lon], zoom_start=15)
    draw = Draw(
        export=True,
        draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False},
        edit_options={"edit": True}
    )
    draw.add_to(m)
    return st_folium(m, width=900, height=600)  # slightly larger map

if st.session_state.show_map:
    # --------------------------------------------------------
    # Show the map so the user can draw an area
    # --------------------------------------------------------
    map_data = draw_map()
    if map_data and map_data.get("all_drawings"):
        drawings = map_data["all_drawings"]
        if drawings:
            geojson = drawings[0]["geometry"]
            st.session_state.geometry = geojson

            # Calculate area
            area_ha = calculate_polygon_area(geojson)
            st.success(f"Field area from drawn polygon: {area_ha:.2f} hectares")

            # Define time period
            today = date.today()
            three_months_ago = today - timedelta(days=90)

            # Fetch NDVI
            ndvi_df = fetch_ndvi_timeseries(geojson, three_months_ago, today)
            st.session_state.ndvi_df = ndvi_df

            # Fetch Weather
            poly = shape(geojson)
            centroid = poly.centroid
            weather = fetch_weather_meteostat(centroid.y, centroid.x, three_months_ago, today)
            if weather is not None:
                st.session_state.weather_df = weather.reset_index().rename(columns={"time": "Date"})
            else:
                st.session_state.weather_df = pd.DataFrame()

            # Once we have data, hide the map
            st.session_state.show_map = False
            st.rerun()  # <-- Updated to st.rerun()
else:
    # --------------------------------------------------------
    # Show the data/charts, hide the map
    # --------------------------------------------------------
    ndvi_df = st.session_state.ndvi_df
    weather_df = st.session_state.weather_df

    # Create NDVI chart
    if not ndvi_df.empty:
        ndvi_fig = px.line(ndvi_df, x="Date", y="NDVI", title="NDVI Time Series")
    else:
        ndvi_fig = None

    # Create Temperature chart
    if not weather_df.empty:
        temp_fig = px.line(weather_df, x="Date", y=["tmax", "tmin"], title="Temperature Time Series")
    else:
        temp_fig = None

    # Layout with Streamlit Elements
    with elements("layout"):
        mui.Grid(
            container=True,
            spacing=2,
            children=[
                # NDVI chart
                mui.Grid(
                    item=True,
                    xs=12,
                    children=[
                        html.Iframe(
                            srcDoc=ndvi_fig.to_html() if ndvi_fig else "<p>No NDVI data.</p>",
                            style={"width": "100%", "height": "400px", "border": "none"}
                        )
                    ]
                ),
                # Temperature chart
                mui.Grid(
                    item=True,
                    xs=12,
                    children=[
                        html.Iframe(
                            srcDoc=temp_fig.to_html() if temp_fig else "<p>No Temperature data.</p>",
                            style={"width": "100%", "height": "400px", "border": "none"}
                        )
                    ]
                ),
            ]
        )

    # Button to show the map again
    st.button("Select New Area", on_click=lambda: setattr(st.session_state, "show_map", True))
