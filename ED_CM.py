import pypsa
from pypsa.descriptors import get_switchable_as_dense as as_dense


import logging
logger = logging.getLogger(__name__)
# Suppress logging of the slack bus choices
pypsa.pf.logger.setLevel(logging.WARNING)


#########################################################################################
# Add ramping limits


#########################################################################################
# extendable = False
def shutdown_extendability(n):

    logger.info("shutdown extendability")

    n.generators.p_nom_extendable = False
    n.links.p_nom_extendable = False
    n.stores.e_nom_extendable = False
    n.storage_units.p_nom_extendable = False
    n.lines.s_nom_extendable = False

def drop_empty_components(n):
    """
    Only drops empty generators. Important for CI gens.
    """

    logger.info("drop empty components")
    
    empty_comps = []

    for i in n.generators.index:
        if n.generators.loc[i,"p_nom"] == 0:
            empty_comps.append(i)

    print("Empty components that will be removed: ",empty_comps)

    n.mremove("Generator",n.generators[n.generators.p_nom == 0.0].index)

#########################################################################################
def prepare_economic_dispatch(m):
    # Build market model `m` with single zones
    # h2buses_df, config

    logger.info("prepare economic dispatch")
    
    m.generators.loc[:,"bus"] = "BZ"
    m.storage_units.loc[:,"bus"] = "BZ"
    m.loads.loc[m.loads["carrier"] == "", "bus"] = "BZ"


    # this removes all elec links and lines, but no elys or battery inverters
    for c in m.iterate_components(m.branch_components): # Line, Link, Transformer
        c.df.loc[c.df["carrier"].isin(["DC","AC"]), ["bus0","bus1"]] = ["BZ","BZ"] # for lines
        c.df.loc[~c.df["bus0"].isin(m.buses[~(m.buses.carrier.isin(["AC", "DC"]))].index), "bus0"] = "BZ"  # for links
        c.df.loc[~c.df["bus1"].isin(m.buses[~(m.buses.carrier.isin(["AC", "DC"]))].index), "bus1"] = "BZ"  # for links
        internal = c.df.bus0 == c.df.bus1
        m.mremove(c.name, c.df.loc[internal].index)

    m.mremove("Bus", m.buses[m.buses.carrier.isin(["AC", "DC"])].index)
    m.madd("Bus", ["BZ"], x=10. , y=51.2 , country='DE', v_nom = 380 , carrier='AC')






#########################################################################################
# Build redispatch model `n`
def prepare_congestion_management(m, n):

    logger.info("prepare congestion management")

    p = m.generators_t.p / m.generators.p_nom
    n.generators_t.p_min_pu = p
    n.generators_t.p_max_pu = p

    g_up = n.generators.copy()
    g_down = n.generators.copy()

    g_up.index = g_up.index.map(lambda x: x + " ramp up")
    g_down.index = g_down.index.map(lambda x: x + " ramp down")

    up = (
        as_dense(m, "Generator", "p_max_pu") * m.generators.p_nom - m.generators_t.p
    ).clip(0) / m.generators.p_nom
    down = -m.generators_t.p / m.generators.p_nom

    up.columns = up.columns.map(lambda x: x + " ramp up")
    down.columns = down.columns.map(lambda x: x + " ramp down")

    n.madd("Generator", g_up.index, p_max_pu=up, **g_up.drop("p_max_pu", axis=1))

    n.madd(
        "Generator",
        g_down.index,
        p_min_pu=down,
        p_max_pu=0,
        **g_down.drop(["p_max_pu", "p_min_pu"], axis=1)
    );

    # include if statement here ? remove here or adapt hourly_matching constraint
    #n.mremove("Generator", n.generators[n.generators.index.str.contains("CI") & n.generators.index.str.contains("ramp")].index)
    
