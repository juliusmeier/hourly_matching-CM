import pypsa

import pandas as pd
import geopandas as gpd
from shapely import wkt
from scipy.spatial import distance

import numpy as np

# file paths
buses = "10"
file_n ="input/2030/elec_s_10_ec_lv1.0_1H.nc"
files_ely = {   "uniform_static_elys": "input/ProductionPlant_LH2_uniformFlat_withCars.csv",
                "uniform_flexible_elys": "input/ProductionPlant_LH2_uniformRealtime_withCars.csv",
                "nodal_static_elys": "input/ProductionPlant_LH2_nodalFlat_withCars.csv",
                "nodal_flexible_elys": "input/ProductionPlant_LH2_nodalRealtime_withCars.csv",
}


# import network
n = pypsa.Network(file_n)

# get closest bus for each ely scenario
for key, value in files_ely.items():

    ely_df = pd.read_csv(value, index_col=0, delimiter=";") 
    ely_df['geometry'] = ely_df['geometry'].apply(wkt.loads)
    ely_gdf = gpd.GeoDataFrame(ely_df, crs='epsg:4326')

    # Get buses dataframe
    buses_df = n.buses
    buses_df = buses_df[~buses_df.index.str.contains('battery|H2')] # The tilde (~) operator is the logical NOT operator in Python. The | character acts as a logical OR operator within the regular expression.

    # Reset the index of the buses dataframe
    buses_df = buses_df.reset_index()

    # Get electrolyser geodataframe
    electrolysers_gdf = ely_gdf
    electrolysers_gdf = electrolysers_gdf.rename(columns={"Result":"kg_H2/day"})

    buses_coords = buses_df[['x', 'y']].values


    # Initialize a list to store the nearest bus for each electrolyser
    nearest_bus = []

    # Iterate over each electrolyser point and find the nearest bus
    for idx, row in electrolysers_gdf.iterrows():
        electrolyser_coords = np.array([row.geometry.x, row.geometry.y])
        distances = distance.cdist(buses_coords, electrolyser_coords.reshape(1, -1))
        nearest_bus_idx = distances.argmin()
        nearest_bus_id = buses_df.loc[nearest_bus_idx, 'Bus']
        nearest_bus.append(nearest_bus_id)

    # Assign the nearest bus to each electrolyser
    electrolysers_gdf['nearest_bus'] = nearest_bus

    ely_df = electrolysers_gdf.drop(columns=["node"])
    ely_df = ely_df.rename(columns={"nearest_bus":"bus"})


    def calc_cap_el(df, LHV = 33.33, eta = 0.7, CF = 0.7):
        df["kWh_H2"] = df["kg_H2/day"] * LHV # "Result" is kg_H2/day
        df["kW_el"] = df["kWh_H2"] / eta / (CF*24)
        tot_GW = df["kW_el"].sum() / 1000**2         # GW_el
        
        df["MW_el"] = df["kW_el"] / 1000
        df["MW_H2"] = df["MW_el"] * eta
        print(df["MW_H2"].max())
        print(df["MW_H2"].min())
        print("Domestic H2 production [TWh_H2]: ", df["kWh_H2"].sum()*365/1e9)
        print("Installed capacity [GW_elec]: ", tot_GW)



    calc_cap_el(ely_df)



    out = ely_df.drop(columns=["kg_H2/day", "kWh_H2", "kW_el", "MW_H2"])
    out = out.rename(columns={"MW_el":"p_nom"})

    out.to_csv("resources/"+key+"_"+buses+".csv", sep=';', index=False)