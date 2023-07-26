import pypsa
import logging
logger = logging.getLogger(__name__)

# Suppress logging of the slack bus choices
pypsa.pf.logger.setLevel(logging.WARNING)



def add_battery_constraints(n):
    """
    Add constraint ensuring that charger = discharger, i.e.
    1 * charger_size - efficiency * discharger_size = 0
    """
    if not n.links.p_nom_extendable.any():
        return

    discharger_bool = n.links.index.str.contains("battery discharger")
    charger_bool = n.links.index.str.contains("battery charger")

    dischargers_ext = n.links[discharger_bool].query("p_nom_extendable").index
    chargers_ext = n.links[charger_bool].query("p_nom_extendable").index

    eff = n.links.efficiency[dischargers_ext].values
    lhs = (
        n.model["Link-p_nom"].loc[chargers_ext]
        - n.model["Link-p_nom"].loc[dischargers_ext] * eff
    )
    
    logger.info("battery constraint")
    
    n.model.add_constraints(lhs == 0, name="Link-charger_ratio")


def country_res_constraints(n, config):
    """
    taken from Zeyen et al.
    inlcudes generators, links, and storage_units
    RES generation == traget * load
    """

    ci_name = config['ci']['name']
    ct = "DE"

    grid_res_techs = config["global"]["grid_res_techs"]
    target = config["scenario"]["res_share"] / 100

    weights = n.snapshot_weightings["generators"]

    grid_buses = n.buses.index[(n.buses.index.str[:2]==ct) |
                                       (n.buses.index == f"{ci_name}")]

    grid_loads = n.loads.index[n.loads.bus.isin(grid_buses)]

    country_res_gens = n.generators.index[n.generators.bus.isin(grid_buses)
                                              & n.generators.carrier.isin(grid_res_techs)]
    country_res_links = n.links.index[n.links.bus1.isin(grid_buses)
                                          & n.links.carrier.isin(grid_res_techs)]
    country_res_storage_units = n.storage_units.index[n.storage_units.bus.isin(grid_buses)
                                                          & n.storage_units.carrier.isin(grid_res_techs)]

    eff_links = n.links.loc[country_res_links, "efficiency"]


    gens =  n.model['Generator-p'].loc[:,country_res_gens] * weights
    links = n.model['Link-p'].loc[:,country_res_links] * eff_links * weights
    sus = n.model['StorageUnit-p_dispatch'].loc[:,country_res_storage_units] * weights

    lhs = gens.sum() + sus.sum() + links.sum()

    total_load = (n.loads_t.p_set[grid_loads].sum(axis=1)*weights).sum() # number

    # add for ct in zone electrolysis demand to load if not "reference" scenario
    # needed to represent the elys elec load in constraint. the CI generators are already included, but not the offtake volume elec demand
    if (f"{ci_name}" in n.buses.index):
            
        logger.info("Consider electrolysis demand for RES target.")
        # H2 demand in zone
        offtake_volume = config["scenario"]["offtake_volume"]
        # efficiency of electrolysis
        efficiency = config["global"]["electrolyser"]["efficiency"]

        # electricity demand of electrolysis
        demand_electrolysis = (offtake_volume/efficiency*n.snapshot_weightings.generators).sum()
        # total electricity load
        total_load += demand_electrolysis

    logger.info(f"country RES constraint for {ct} {target} and total load {round(total_load/1e6)} TWh")

    n.model.add_constraints(lhs == target*total_load, name=f"country_res_constraints_{ct}")



def excess_constraints(n, h2buses_df, config):
    '''
    hourly matching constraint
    Inspired by Zeyen et al. but changed a lot since the desired outcome could not be reproduced with the
    given constraint
    '''

    h2buses = h2buses_df.index
    
    name = config['ci']['name']
    excess = 1 + config["scenario"]["excess"] / 100

    ci_gens = list(n.generators[n.generators.index.str.contains("CI")].index)
    res_gens = []
    dummy_gens = []
    for i in ci_gens:
        if 'dummy' in i:
            dummy_gens.append(i)
        else:
            res_gens.append(i)
    res_dis = list(n.links[n.links.index.str.contains("CI battery discharger")].index)
    res_ch = list(n.links[n.links.index.str.contains("CI battery charger")].index) 

    weights = n.snapshot_weightings["generators"]

    res = (n.model['Generator-p'].loc[:,res_gens] * weights).sum("Generator")
    dis = (n.model['Link-p'].loc[:,res_dis] * weights * n.links.loc[res_dis, "efficiency"]).sum("Link")
    ch = (n.model['Link-p'].loc[:,res_ch] * weights).sum("Link")
    if config["global"]["dummies"]:
        dummies = (n.model['Generator-p'].loc[:,dummy_gens] * weights).sum("Generator")

    ely_links = [h2bus + " " + name + " H2 Electrolysis" for h2bus in h2buses]
    electrolysis = (n.model['Link-p'].loc[:,ely_links] * weights).sum("Link")

    allowed_excess = 1

    if config["global"]["dummies"]:
        lhs = (res + dis - ch - electrolysis + dummies) * allowed_excess
    else:
        lhs = (res + dis - ch - electrolysis) * allowed_excess

    logger.info("RES hourly matching constraint")

    n.model.add_constraints(lhs == 0, name="RES_hourly_excess")


