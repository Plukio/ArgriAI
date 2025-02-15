import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw
from geopy.geocoders import Nominatim
from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer

def main():
    st.set_page_config(page_title="Area Measurement", layout="wide")
    st.title("Interactive Polygon Area Measurement")

    # Search bar: enter a location to center the map
    location_query = st.text_input("Enter a location to search:", "")
    center_location = [20, 0]  # default center
    zoom_level = 3           # default zoom

    if location_query:
        try:
            geolocator = Nominatim(user_agent="streamlit_app")
            location = geolocator.geocode(location_query)
            if location:
                center_location = [location.latitude, location.longitude]
                zoom_level = 10  # zoom in when location is found
                st.success(f"Found location: {location.address}")
            else:
                st.error("Location not found. Showing default map.")
        except Exception as e:
            st.error(f"Error finding location: {e}")

    # Create the Folium map centered on the searched location (or default)
    m = folium.Map(location=center_location, zoom_start=zoom_level)

    # Add drawing controls (polygon, rectangle, circle) to the map
    draw = Draw(
        draw_options={
            "polygon": True,
            "polyline": False,
            "rectangle": True,
            "circle": True,
            "marker": False,
            "circlemarker": False,
        },
        edit_options={"edit": True}
    )
    draw.add_to(m)

    # Render the map in Streamlit
    output = st_folium(m, width=700, height=500)

    # Process drawn features to calculate areas
    if output and "all_drawings" in output:
        all_drawings = output["all_drawings"]
        if all_drawings:
            st.subheader("Drawn Features and Their Areas")
            for i, feature in enumerate(all_drawings, start=1):
                geometry = feature["geometry"]
                geom_type = geometry["type"]

                if geom_type in ["Polygon", "MultiPolygon"]:
                    # Convert GeoJSON to a Shapely geometry
                    shapely_geom = shape(geometry)

                    # Reproject the geometry from EPSG:4326 to EPSG:3857 for area calculations in meters
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
                    projected_geom = transform(transformer.transform, shapely_geom)

                    area_m2 = projected_geom.area
                    area_ha = area_m2 / 10_000.0  # convert m² to hectares
                    area_km2 = area_m2 / 1_000_000.0  # convert m² to km²

                    st.write(f"**Feature #{i}:**")
                    st.write(f"Type: {geom_type}")
                    st.write(f"Area (m²): {area_m2:,.2f}")
                    st.write(f"Area (ha): {area_ha:,.2f}")
                    st.write(f"Area (km²): {area_km2:,.4f}")
                    st.write("---")
                else:
                    st.write(f"**Feature #{i}:**")
                    st.write(f"Type: {geom_type} (no area calculation)")
                    st.write("---")

if __name__ == "__main__":
    main()
