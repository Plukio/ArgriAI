import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw

from shapely.geometry import shape
from shapely.ops import transform
from pyproj import Transformer

def main():
    st.set_page_config(page_title="Area Measurement", layout="wide")
    st.title("Interactive Polygon Area Measurement")

    # Create a Folium map centered on a default location (e.g., lat=20, lon=0)
    # Adjust to your area of interest as needed.
    m = folium.Map(location=[20, 0], zoom_start=3)

    # Add drawing controls (polygon, rectangle, circle, etc.) to the map
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

    # Embed the Folium map in Streamlit
    # st_folium returns a dictionary with info about drawn objects, zoom, bounds, etc.
    output = st_folium(m, width=700, height=500)

    # If the user drew something, output['all_drawings'] will contain the GeoJSON data
    if output and "all_drawings" in output:
        all_drawings = output["all_drawings"]
        if all_drawings:
            st.subheader("Drawn Features and Their Areas")

            for i, feature in enumerate(all_drawings, start=1):
                geometry = feature["geometry"]
                geom_type = geometry["type"]

                if geom_type in ["Polygon", "MultiPolygon"]:
                    # Convert geojson to a Shapely geometry
                    shapely_geom = shape(geometry)

                    # Reproject the geometry from EPSG:4326 (lat/lon) to EPSG:3857 (meters)
                    # so that .area gives square meters
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
                    projected_geom = transform(transformer.transform, shapely_geom)

                    area_m2 = projected_geom.area
                    area_ha = area_m2 / 10_000.0  # convert sq meters to hectares
                    area_km2 = area_m2 / 1_000_000.0  # convert sq meters to sq km

                    st.write(f"**Feature #{i}:**")
                    st.write(f"Type: {geom_type}")
                    st.write(f"Area (m²): {area_m2:,.2f}")
                    st.write(f"Area (ha): {area_ha:,.2f}")
                    st.write(f"Area (km²): {area_km2:,.4f}")
                    st.write("---")

                else:
                    # For lines, points, or other geometry, area doesn't apply
                    st.write(f"**Feature #{i}:**")
                    st.write(f"Type: {geom_type} (no area calculation)")
                    st.write("---")


if __name__ == "__main__":
    main()
