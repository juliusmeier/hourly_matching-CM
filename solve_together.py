import pypsa
import matplotlib.pyplot as plt
plt.style.use('bmh')

import logging
logger = logging.getLogger(__name__)
# Suppress logging of the slack bus choices
pypsa.pf.logger.setLevel(logging.WARNING)

import pandas as pd




def shutdown_lineexp(n):
    '''
    remove line expansion option
    '''
    logger.info("shutdown line expansion")

    n.lines.s_nom_extendable = False
    n.links.loc[n.links.carrier=='DC', 'p_nom_extendable'] = False


def add_dummies(n, config):
    
    logger.info("add dummies for elec and ci")

    name = config['ci']['name']
    
    elec_buses = n.buses.index[n.buses.carrier == "AC"]
    #logger.info("adding dummies to",elec_buses)

    n.madd("Generator",
            elec_buses + " dummy",
            bus=elec_buses,
            carrier="dummy",
            p_nom=1e3,
            marginal_cost=1e6)
    n.madd("Generator",
            elec_buses + " " + name + " " + "dummy",
            bus=elec_buses,
            carrier="dummy",
            p_nom=1e3,
            marginal_cost=1e6)
    
    n.add(
        "Carrier",
        "dummy",
        nice_name="Lost load",
        color="#000000"
    )


def prepare_elys(elys_df, config):
    '''
    Read prepared data with electrolyser and its closest elec node.
    Aggregate electrolysers at the same elec node.
    Rescale to achieve set total electrolyser capacity.
    '''

    ely_cap = config["scenario"]["ely_cap"]

    logger.info(f"aggregate electrolysers at same bus and rescale to {ely_cap/1e3} GW total")
    
    # aggregate
    df_agg = elys_df.groupby("bus").agg({'p_nom': 'sum'})

    # rescale
    df_agg.p_nom = df_agg.p_nom/df_agg.p_nom.sum()
    df_agg.p_nom = df_agg.p_nom * ely_cap

    return df_agg


def add_H2_demand(n, config):
    '''
    Add CI H2 bus that has H2 demand. 
    All electrolyser links connect to this bus.
    '''

    logger.info("add H2 bus and H2 demand")

    name = config['ci']['name']

    n.add("Bus",
        f"{name} H2",
        carrier="H2"
        )

    offtake_volume = config["scenario"]["offtake_volume"]

    logger.info(f"offtake volume (MWh_h2 per h): {offtake_volume}")

    n.add("Load",
        f"{name} H2",
        carrier=f"{name} H2",
        bus=f"{name} H2",
        p_set=float(offtake_volume),
        )
    
    n.add(
        "Carrier",
        f"{name} H2",
        nice_name=f"{name} H2 demand",
        color="#ebaee0"
    )

    operation_mode = config["scenario"]["operation_mode"]
    h2_storage = config["scenario"]["h2_storage"]

    logger.info(f"(H2 storage) electrolysers operation mode: {operation_mode}")
    logger.info(f"(H2 storage) H2 storage: {h2_storage}")

    if h2_storage == "medium":
        store_cost = float(config["global"]["H2_store_cost"]['medium'])
        n.add("Store",
        f"{name} H2 Store",
        bus=f"{name} H2",
        e_cyclic=True,
        e_nom_extendable=True,
        carrier="H2 Store",
        capital_cost = store_cost,
        )
    elif h2_storage == "flexible":
        store_cost = float(config["global"]["H2_store_cost"]['flexible'])
        n.add("Store",
        f"{name} H2 Store",
        bus=f"{name} H2",
        e_cyclic=True,
        e_nom_extendable=True,
        carrier="H2 Store",
        capital_cost = store_cost,
        )
    """
    if operation_mode == "static":
        store_cost = float(config["global"]["H2_store_cost"]['static'])
        n.add("Store",
        f"{name} H2 Store",
        bus=f"{name} H2",
        e_cyclic=True,
        e_nom_extendable=True,
        carrier="H2 Store",
        capital_cost = store_cost,
        )
    else:
        logger.info("operation mode is not flexible or static")
    """


def add_elys(n, h2buses_df, config):
    '''
    Add electrolysers as link component between elec bus and CI H2 bus
    Adapt electrolyser capacity if operation mode == static
    '''
    
    logger.info("add CI electrolyser links")

    name = config['ci']['name']
    
    for h2bus in h2buses_df.index:
        n.add("Link",
            h2bus + " " + name + " " + "H2 Electrolysis",
            bus0=h2bus,
            bus1=f"{name} H2",
            carrier=f"{name} H2 Electrolysis",
            efficiency=config["global"]["electrolyser"]["efficiency"],
            p_nom=h2buses_df.loc[h2bus,"p_nom"] ,
            marginal_cost=0
            )
        
    n.add(
        "Carrier",
        f"{name} H2 Electrolysis",
        nice_name=f"{name} H2 Electrolysis",
        color="#f073da"
    )
    
    operation_mode = config["scenario"]["operation_mode"]
    
    logger.info(f"(Ely Links capacity) electrolysers operation mode: {operation_mode}")
    
    if operation_mode == "static":
        
        offtake_volume = config["scenario"]["offtake_volume"]
        efficiency = config["global"]["electrolyser"]["efficiency"]

        n.links.loc[n.links.index.str.contains(f"{name} H2 Electrolysis"),"p_nom"] \
            = n.links.loc[n.links.index.str.contains(f"{name} H2 Electrolysis"),"p_nom"] \
            / n.links.loc[n.links.index.str.contains(f"{name} H2 Electrolysis"),"p_nom"].sum() \
            * offtake_volume / efficiency \
            * 1.000001 # add small buffer to ensure feasibility

        print("STATIC OPERATION: total electrolyser capacity: ", n.links.loc[n.links.index.str.contains("CI H2 Electrolysis"),"p_nom"].sum())


def add_CI_gen_bat(n, config): # h2buses_df
    '''
    Add extendable CI onwind, solar and batteries at all elec buses that contain the corresponding technology
    '''

    logger.info("add CI RES generators and batteries")

    name = config['ci']['name']

    elec_buses = n.buses.index[n.buses.carrier == "AC"]

    for elec_bus in elec_buses:

        for carrier in config['ci']['res_techs']:
            
            gen_template = elec_bus+" "+carrier
            
            if gen_template in n.generators.index:
                n.add("Generator",
                        elec_bus + f" {name} " +carrier,
                        carrier=carrier,
                        bus=elec_bus,
                        p_nom_extendable=True,
                        p_nom_min=0.1,
                        p_max_pu=n.generators_t.p_max_pu[gen_template],
                        capital_cost=n.generators.at[gen_template,"capital_cost"],
                        marginal_cost=n.generators.at[gen_template,"marginal_cost"])
                
                n.buses.loc[elec_bus,"nom_max_"+carrier] = n.generators.at[gen_template,"p_nom_max"]
                n.buses.loc[elec_bus,"nom_min_"+carrier] = n.generators.at[gen_template,"p_nom_min"]

        if "battery" in config['ci']['sto_techs']:
            
            bat_template = elec_bus+" "+"battery"

            n.add("Bus",
                    elec_bus + f" {name} battery",
                    carrier="battery",
                    x=n.buses.at[bat_template,"x"],
                    y=n.buses.at[bat_template,"y"],
                    )
            
            n.add("Store",
                    elec_bus + f" {name} battery",
                    bus=elec_bus + f" {name} battery",
                    e_cyclic=True,
                    e_nom_extendable=True,
                    carrier="battery",
                    capital_cost=n.stores.at[bat_template, "capital_cost"],
                    lifetime= n.stores.at[bat_template, "lifetime"]
                    )

            n.add("Link",
                    elec_bus + f" {name} battery charger",
                    bus0=elec_bus,
                    bus1=elec_bus + f" {name} battery",
                    carrier="battery charger",
                    p_nom_extendable=True,
                    efficiency=n.links.at[bat_template+" "+"charger", "efficiency"],
                    capital_cost=n.links.at[bat_template+" "+"charger", "capital_cost"],
                    lifetime=n.links.at[bat_template+" "+"charger", "lifetime"],
                    marginal_cost=n.links.at[bat_template+" "+"charger", "marginal_cost"],
                    )

            n.add("Link",
                    elec_bus + f" {name} battery discharger",
                    bus0=elec_bus + f" {name} battery",
                    bus1=elec_bus,
                    carrier="battery discharger",
                    p_nom_extendable=True,
                    efficiency=n.links.at[bat_template+" "+"discharger", "efficiency"],
                    lifetime=n.links.at[bat_template+" "+"discharger", "lifetime"],
                    marginal_cost=n.links.at[bat_template+" "+"discharger", "marginal_cost"],
                    )



def prepare_CI_distribution(n, config):
    '''
    '''

    #logger.info("add H2 bus and H2 demand")

    name = config['ci']['name']

    n.add("Bus",
        f"{name} H2",
        carrier="H2"
        )

    offtake_volume = config["scenario"]["offtake_volume"]

    logger.info(f"offtake volume (MWh_h2 per h): {offtake_volume}")

    n.add("Load",
        f"{name} H2",
        carrier=f"{name} H2",
        bus=f"{name} H2",
        p_set=float(offtake_volume),
        )
    
    #n.add(
    #    "Carrier",
    #    f"{name} H2",
    #    nice_name=f"{name} H2 demand",
    #    color="#ebaee0"
    #)

    operation_mode = config["scenario"]["operation_mode"]

    logger.info(f"(H2 storage) electrolysers operation mode: {operation_mode}")

    if operation_mode == "flexible":
        store_cost = float(config["global"]["H2_store_cost"]['flexible'])
        n.add("Store",
        f"{name} H2 Store",
        bus=f"{name} H2",
        e_cyclic=True,
        e_nom_extendable=True,
        carrier="H2 Store",
        capital_cost = store_cost,
        )

    ################################

    n.add("Bus",
        f"{name} elec",
        carrier="AC for H2"
        )

    n.add("Link",
        name + " " + "H2 Electrolysis elec",
        bus0=f"{name} elec",
        bus1=f"{name} H2",
        carrier=f"{name} H2 Electrolysis",
        efficiency=config["global"]["electrolyser"]["efficiency"],
        p_nom=config["scenario"]["ely_cap"],
        marginal_cost=0
        )

    n.madd("Line",
        n.buses.index[n.buses.carrier == "AC"] + " H2 elec",
        bus0= n.buses.index[n.buses.carrier == "AC"],
        bus1=f"{name} elec",
        carrier=f"{name} H2 Electrolysis",
        s_nom = 100*1e3,
        x=1,
        r=1
        )
    
def remove_CI_dist_prep(n, config):

    name = config['ci']['name']
    operation_mode = config["scenario"]["operation_mode"]

    n.remove(
        "Bus",
        f"{name} H2",
    )

    n.remove(
        "Load",
        f"{name} H2",
    )

    if operation_mode == "flexible":
        n.remove(
            "Store",
            f"{name} H2 Store"
        )

    ################################

    n.remove(
        "Bus",
        f"{name} elec",
    )

    n.remove(
        "Link",
        name + " " + "H2 Electrolysis elec",
    )

    n.mremove(
        "Line",
        n.lines[n.lines.index.str.contains("H2 elec")].index
    )