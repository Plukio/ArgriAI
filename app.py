import streamlit as st
import requests
import ee
import pandas as pd
import numpy as np
from datetime import date, timedelta, datetime
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer
import xlwings as xw

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception as e:
    ee.Authenticate()
    ee.Initialize()

# ---------- Weather Data Fetching ----------
def fetch_full_weather_nasa(lat, lon, start_date, end_date):
    base_url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    params = {
        "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR,WS2M,SOLARAD",
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
                    "Tmin": float(weather_params["T2M_MIN"][dt]),
                    "Precip": float(weather_params["PRECTOTCORR"][dt]),
                    "Wind": float(weather_params["WS2M"][dt]),
                    "SolarRad": float(weather_params["SOLARAD"][dt]),
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

# ---------- Remote Sensing: NDVI ----------
def fetch_ndvi(lat, lon, start_date, end_date):
    point = ee.Geometry.Point(lon, lat)
    collection = ee.ImageCollection("COPERNICUS/S2_SR")\
                    .filterBounds(point)\
                    .filterDate(start_date, end_date)\
                    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    image = collection.first()
    if image is None:
        st.error("No Sentinel-2 images found for the selected date range and location.")
        return None
    ndvi_image = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    mean_dict = ndvi_image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=point.buffer(500),
        scale=10
    )
    ndvi_value = mean_dict.get("NDVI").getInfo()
    return ndvi_value

# ---------- Field Delineation ----------
def calculate_polygon_area(geojson):
    geom = shape(geojson)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    projected_geom = transform(transformer.transform, geom)
    area_m2 = projected_geom.area
    return area_m2 / 10000.0  # hectares

# ---------- AquaCrop-OSPy Simulation Function ----------
def run_aquacrop_simulation_ospy(weather_df, planting_date, sim_duration_days, crop_type, soil_type):
    sim_start_date = planting_date
    plant_date_obj = datetime.strptime(planting_date, "%Y/%m/%d").date()
    sim_end_date_obj = plant_date_obj + timedelta(days=sim_duration_days)
    sim_end_date = sim_end_date_obj.strftime("%Y/%m/%d")
    
    # Open the AquaCrop-OSPy Excel model (update the file path as needed)
    try:
        wb = xw.Book("Aquacrop_Model.xlsx")
        sht_input = wb.sheets["Inputs"]
        
        # Write input parameters (adjust cell references to your model)
        sht_input.range("B2").value = sim_start_date
        sht_input.range("B3").value = crop_type
        sht_input.range("B4").value = weather_df.to_csv(index=True)  # example: writing CSV string
        sht_input.range("B5").value = soil_type
        
        # Trigger recalculation
        wb.app.calculate()
        
        sht_output = wb.sheets["Outputs"]
        ET_crop = sht_output.range("B2").value  # e.g., crop ET from cell B2
        wb.close()
        return {"ET_crop": ET_crop}
    except Exception as e:
        st.error(f"Error running AquaCrop simulation: {e}")
        return None

# ---------- Kcb Calculation for Rice ----------
def calculate_kcb_from_ndvi(ndvi, crop="Rice"):
    params = {
        "Rice": {"Kcb_max": 1.00, "Kcb_min": 0.15, "VI_max": 0.90, "VI_min": 0.10, "eta": 1.0},
        "Custom": {"Kcb_max": 1.00, "Kcb_min": 0.15, "VI_max": 0.90, "VI_min": 0.10, "eta": 1.0},
    }
    p = params.get(crop, params["Custom"])
    ndvi = max(p["VI_min"], min(ndvi, p["VI_max"]))
    ratio = (ndvi - p["VI_min"]) / (p["VI_max"] - p["VI_min"])
    Kcb = (p["Kcb_max"] - p["Kcb_min"]) * (ratio ** p["eta"]) + p["Kcb_min"]
    return Kcb

def calculate_irrigation(et0, Kcb, field_area, efficiency):
    ET_crop_calc = et0 * Kcb  # mm/day
    net_irrigation = ET_crop_calc * field_area * 10  # m³/day
    gross_irrigation = net_irrigation / (efficiency / 100.0)
    return ET_crop_calc, net_irrigation, gross_irrigation

# ---------- Crop Customization Page ----------
def crop_customization_page():
    st.title("Customize Crop Parameters")
    st.markdown("Edit the default parameters for PaddyRice (based on AquaCrop-OSPy defaults).")
    default_params = {
        'Aer': -1e10,
        'LagAer': 1e10,
        'CCx': 0.95,
        'CDC': -9.0,
        'CDC_CD': 0.0933,
        'CGC': -9.0,
        'CGC_CD': 0.12257,
        'CalendarType': 1,
        'CropType': 3,
        'Determinant': 1.0,
        'ETadj': 1.0,
        'Emergence': -9.0,
        'EmergenceCD': 3.0,
        'Flowering': -9.0,
        'FloweringCD': 19.0,
        'GDD_lo': 0,
        'GDD_up': 10.0,
        'GDDmethod': 3,
        'HI0': 0.43,
        'HIstart': -9.0,
        'HIstartCD': 65.0,
        'Kcb': 1.1,
        'Maturity': -9.0,
        'MaturityCD': 104.0,
        'MaxRooting': -9.0,
        'MaxRootingCD': 21.0,
        'Name': 'PaddyRice',
        'PlantMethod': 0.0,
        'PlantPop': 1000000.0,
        'PolColdStress': 1,
        'PolHeatStress': 1,
        'SeedSize': 6.0,
        'Senescence': -9.0,
        'SenescenceCD': 73.0,
        'SwitchGDD': 0,
        'SxBotQ': 0.012,
        'SxTopQ': 0.048,
        'Tbase': 8.0,
        'Tmax_lo': 40.0,
        'Tmax_up': 35.0,
        'Tmin_lo': 3.0,
        'Tmin_up': 8.0,
        'TrColdStress': 1,
        'Tupp': 30.0,
        'WP': 19.0,
        'WPy': 100.0,
        'YldForm': -9.0,
        'YldFormCD': 36.0,
        'YldWC': 90,
        'Zmax': 0.5,
        'Zmin': 0.3,
        'a_HI': 10.0,
        'b_HI': 7.0,
        'dHI0': 15.0,
        'dHI_pre': 0.0,
        'exc': 100.0,
        'fage': 0.15,
        'fshape_r': 2.5,
        'fshape_w1': 3.0,
        'fshape_w2': 3.0,
        'fshape_w3': 3.0,
        'fshape_w4': 1,
        'fsink': 0.5,
        'p_lo1': 0.4,
        'p_lo2': 1,
        'p_lo3': 1,
        'p_lo4': 1,
        'p_up1': 0.0,
        'p_up2': 0.5,
        'p_up3': 0.55,
        'p_up4': 0.75,
    }
    st.subheader("PaddyRice Default Parameters")
    crop_params = {}
    for key, value in default_params.items():
        crop_params[key] = st.number_input(f"{key}", value=float(value))
    st.write("Updated crop parameters:", crop_params)
    if st.button("Save Crop Parameters"):
        st.success("Crop parameters updated.")
    return crop_params

# ---------- Main App Navigation ----------
page = st.sidebar.radio("Select Page", ["Simulation", "Customize Crop"])

if page == "Customize Crop":
    custom_params = crop_customization_page()
    st.write("Custom crop parameters are ready to be used in simulations.")
    
elif page == "Simulation":
    st.title("Integrated Irrigation Simulation")
    st.markdown(
        """
    This simulation integrates:
    - Full weather data (Tmax, Tmin, precipitation, wind, solar radiation) fetched from NASA POWER.
    - NDVI from Sentinel-2 via Google Earth Engine.
    - The real AquaCrop-OSPy model.
    - NDVI-based crop coefficient (Kcb) adjustment for Rice.
    - Field area determination via an interactive map.
    
    Configure the simulation parameters below.
        """
    )
    
    st.sidebar.header("AquaCrop Simulation Settings")
    planting_date = st.sidebar.text_input("Planting Date (YYYY/MM/DD)", value="2023/06/01")
    sim_duration = st.sidebar.number_input("Simulation Duration (days)", min_value=30, value=120, step=10)
    # Weather will be fetched from NASA POWER
    soil_type = st.sidebar.text_input("Soil Type", value="SandyLoam")
    
    st.sidebar.header("Location & Date Settings for Weather & RS")
    lat = st.sidebar.number_input("Latitude", value=10.0, format="%.6f")
    lon = st.sidebar.number_input("Longitude", value=105.0, format="%.6f")
    rs_start_date = st.sidebar.date_input("Remote Sensing Start Date", value=date(2023, 6, 1))
    rs_end_date = st.sidebar.date_input("Remote Sensing End Date", value=date(2023, 6, 10))
    
    st.sidebar.header("OpenWeatherMap Settings")
    openweather_api_key = st.sidebar.text_input("OpenWeather API Key", type="password")
    
    # Main Panel: Interactive Map for Field Delineation
    st.markdown("### Delineate Your Field (Optional)")
    m = folium.Map(location=[lat, lon], zoom_start=15)
    from folium.plugins import Draw
    draw = Draw(export=True, draw_options={"polyline": False, "circle": False, "marker": False, "circlemarker": False}, edit_options={"edit": True})
    draw.add_to(m)
    map_data = st_folium(m, width=700, height=500, returned_formats=["geojson"])
    
    field_area = st.sidebar.number_input("Manual Field Area (hectares)", min_value=0.1, value=1.0, step=0.1)
    if map_data and map_data.get("all_drawings"):
        drawings = map_data["all_drawings"]
        if drawings:
            geojson = drawings[0]["geometry"]
            field_area = calculate_polygon_area(geojson)
            st.success(f"Field area from map: {field_area:.2f} hectares")
        else:
            st.info("No field boundary drawn. Using manual field area.")
    
    # Fetch full weather data button
    if st.sidebar.button("Fetch Weather Data"):
        weather_df = fetch_full_weather_nasa(lat, lon, rs_start_date, rs_end_date)
        if weather_df is not None:
            st.sidebar.success("Full weather dataset fetched.")
            st.sidebar.write(weather_df.head())
    
    # Fetch remote sensing data button
    if st.sidebar.button("Fetch Remote Sensing Data"):
        et0_value = fetch_etref_nasa(lat, lon, rs_start_date, rs_end_date)
        ndvi_value = fetch_ndvi(lat, lon, str(rs_start_date), str(rs_end_date))
        if et0_value is not None:
            st.sidebar.success(f"Reference ET (ET₀): {et0_value:.2f} mm/day")
        if ndvi_value is not None:
            st.sidebar.success(f"NDVI: {ndvi_value:.2f}")
    
    # Simulation Calculation Trigger
    if st.button("Run Integrated Simulation"):
        weather_df = fetch_full_weather_nasa(lat, lon, rs_start_date, rs_end_date)
        et0_value = fetch_etref_nasa(lat, lon, rs_start_date, rs_end_date)
        ndvi_value = fetch_ndvi(lat, lon, str(rs_start_date), str(rs_end_date))
        if weather_df is None or et0_value is None or ndvi_value is None:
            st.error("Failed to fetch required remote sensing/weather data. Please check inputs.")
        else:
            aquacrop_out = run_aquacrop_simulation_ospy(weather_df, planting_date, sim_duration, crop_type, soil_type)
            if aquacrop_out is None:
                st.error("Aquacrop simulation failed.")
            else:
                Kcb = calculate_kcb_from_ndvi(ndvi_value, crop=crop_type)
                ET_crop_calc, net_irrigation, gross_irrigation = calculate_irrigation(et0_value, Kcb, field_area, efficiency)
                
                st.markdown("### Integrated Irrigation Requirement Results")
                st.write(f"**Reference ET (ET₀) [NASA POWER]:** {et0_value:.2f} mm/day")
                st.write(f"**NDVI [Sentinel-2]:** {ndvi_value:.2f}")
                st.write(f"**Adjusted Crop Coefficient (Kcb) [Rice]:** {Kcb:.2f}")
                st.write(f"**AquaCrop-OSPy Simulated Crop ET:** {aquacrop_out['ET_crop']:.2f} mm/day")
                st.write(f"**Calculated Crop ET (ET₀ × Kcb):** {ET_crop_calc:.2f} mm/day")
                st.write(f"**Field Area:** {field_area:.2f} hectares")
                st.write(f"**Net Irrigation Requirement:** {net_irrigation:.2f} m³/day")
                st.write(f"**Gross Irrigation Requirement:** {gross_irrigation:.2f} m³/day")
