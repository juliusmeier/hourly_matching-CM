import pypsa
import pandas as pd
import os

import logging
logger = logging.getLogger(__name__)
# Suppress logging of the slack bus choices
pypsa.pf.logger.setLevel(logging.WARNING)
#from vresutils.benchmark import memory_logger

from solve_together import *
from additional_constraints import *
from ED_CM import *

import yaml
with open("config.yaml", "r") as f:
    config = yaml.load(f, Loader=yaml.FullLoader)


###############################################################################


def solve_network(n,config):

    def extra_functionality(n, snapshots):

        add_battery_constraints(n)
        country_res_constraints(n, config)

        sus = n.model.variables["StorageUnit-state_of_charge"]
        min_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.01
        max_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.99
        n.model.add_constraints(sus >= min_soc, name="StorageUnit-minimum_soc")
        n.model.add_constraints(sus <= max_soc, name="StorageUnit-maximum_soc")

    formulation = config['solving']['options']['formulation']
    solver_options = config['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )

def solve_network_dispatch(n,config):

    def extra_functionality(n, snapshots):

        sus = n.model.variables["StorageUnit-state_of_charge"]
        min_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.001
        max_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.999
        n.model.add_constraints(sus >= min_soc, name="StorageUnit-minimum_soc")
        n.model.add_constraints(sus <= max_soc, name="StorageUnit-maximum_soc")


    formulation = config['solving']['options']['formulation']
    solver_options = config['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )


def solve_economic_dispatch(m, config):
    
    def extra_functionality(m, snapshot):

        m.model.constraints.remove("StorageUnit-fix-p_dispatch-lower")
        m.model.constraints.remove("StorageUnit-fix-p_dispatch-upper")
        m.model.constraints.remove("StorageUnit-fix-p_store-lower")
        m.model.constraints.remove("StorageUnit-fix-p_store-upper")

    formulation = config['solving']['options']['formulation']
    solver_options = config['solving']['solver']
    solver_name = solver_options['name']

    m.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )


def solve_congestion_management(n, config):

    def extra_functionality(n, snapshot):

        n.model.constraints.remove("StorageUnit-fix-p_dispatch-lower")
        n.model.constraints.remove("StorageUnit-fix-p_dispatch-upper")
        n.model.constraints.remove("StorageUnit-fix-p_store-lower")
        n.model.constraints.remove("StorageUnit-fix-p_store-upper")

    formulation = config['solving']['options']['formulation']
    solver_options = config['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )
    

def solve_congestion_management_custom(n,m,config):

    def extra_functionality(n, snapshots):

        n.model.constraints.remove("StorageUnit-fix-p_dispatch-lower")
        n.model.constraints.remove("StorageUnit-fix-p_dispatch-upper")
        n.model.constraints.remove("StorageUnit-fix-p_store-lower")
        n.model.constraints.remove("StorageUnit-fix-p_store-upper")
        
        # new objective function
        weights = n.snapshot_weightings["generators"]
        #eff_links = n.links.loc[:, "efficiency"]
        expr=[]
        for g in n.generators[n.generators.index.str.contains("ramp up")].index:
            expr.append(n.model['Generator-p'].sel(Generator=g)
                        * weights
                        * n.generators.loc[g,"marginal_cost"])
        for g in n.generators[n.generators.index.str.contains("ramp down")].index:
            expr.append(n.model['Generator-p'].sel(Generator=g)
                        * weights
                        * -1
                        * (m.buses_t.marginal_price.BZ - n.generators.loc[g,"marginal_cost"]) 
                        )

        obj_fct = sum(expr).sum()    

        n.model.add_objective(obj_fct, overwrite=True)

    formulation = config['solving']['options']['formulation']
    solver_options = config['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )


# import network -------------------------------------------------------
o = pypsa.Network(config['network_file'])

##################################

#o.set_snapshots(list(o.snapshots[0:72]))

##################################

results_dir = "results/2030_ref/"
if not os.path.exists(results_dir):
        os.makedirs(results_dir)


# network pre-modifications ----------------------------------

bat_color = o.carriers.color.loc["battery"]
o.madd(
        "Carrier",
        ["H2 electrolysis", "H2 fuel cell", "battery charger", "battery discharger"],
        color=["#ff29d9", "#c251ae", bat_color, bat_color]
    )


o.links.loc[o.links.carrier != "DC", "marginal_cost"] = config["global"]["mc_usc"]


# Set up network ------------------------------------------------
shutdown_lineexp(o)

if config["global"]["dummies"]:
    add_dummies(o, config)

# Oversize stores 2 %
o.stores.e_min_pu = 0.01
o.stores.e_max_pu = 0.99

# Remove sus with max_hours=0
o.mremove(
    "StorageUnit",
    o.storage_units[o.storage_units["max_hours"]==0].index
)

###############################################################################
# Build 2030 power system
logger.info("Solve o")
solve_network(o, config)

# export moved below

print(o.model.constraints)
print("\n#################\n")
print("Power system 2030 - o.nc")
print("Number of variables: ",o.model.nvars)
print("Number of constraints: ",o.model.ncons)
print("Objective value o (Investment + Dispatch): ", o.objective / 1e6 )
print("\n#################\n")



###############################################################################
# ED + CM Preparation

# Fixing optimal capcities
o.optimize.fix_optimal_capacities()
o.export_to_netcdf(results_dir + "o-r.nc")


o2 = o.copy()

drop_empty_components(o2)

# solve initial dispatch only -----------------------------------------
logger.info("Solve o2 (dispatch only of o)")
solve_network_dispatch(o2, config)

o2.export_to_netcdf(results_dir + "o2-r.nc")

print(o2.model.constraints)
print("\n#################\n")
print("Power system 2030 dispatch only - o2.nc")
print("Number of variables: ",o2.model.nvars)
print("Number of constraints: ",o2.model.ncons)
print("Objective value o2 (Nodal Dispatch): ", o2.objective / 1e6 )
print("\n#################\n")


###########################################
o2_temp = o2.copy()

# Free up oversized space 
o2_temp.stores.e_min_pu = 0.0  
o2_temp.stores.e_max_pu = 1.0

# Fix variables
stores_e_initial = o2.stores_t.e.iloc[-1,:]
storage_units_soc_initial = o2.storage_units_t.state_of_charge.iloc[-1,:]
	# storage_units
o2_temp.storage_units["state_of_charge_initial"] = storage_units_soc_initial
o2_temp.storage_units.cyclic_state_of_charge = False
o2_temp.storage_units_t.p_dispatch_set = o2.storage_units_t.p_dispatch
o2_temp.storage_units_t.p_store_set = o2.storage_units_t.p_store
	# stores and links: without CI H2 and bat
o2_temp.stores.e_cyclic = False
o2_temp.stores.e_initial = stores_e_initial

p0_links_pu = o2_temp.links_t.p0 / o2_temp.links.p_nom
o2_temp.links_t.p_min_pu = p0_links_pu - 0.001
o2_temp.links_t.p_max_pu = p0_links_pu + 0.001

o2_temp.links_t.p_min_pu.drop(columns=["T10","T18","T20"], inplace=True)
o2_temp.links_t.p_max_pu.drop(columns=["T10","T18","T20"], inplace=True)

###############################################


m = o2_temp.copy()  # for market model
n = o2_temp.copy()  # for redispatch model
n_custom = o2_temp.copy()  # for redispatch model


###############################################
# ED
prepare_economic_dispatch(m)

logger.info("Solve m")
solve_economic_dispatch(m, config)

m.export_to_netcdf(results_dir + "m-r.nc")

print(m.model.constraints)
print("\n#################\n")
print("ED - m.nc")
print("Number of variables: ",m.model.nvars)
print("Number of constraints: ",m.model.ncons)
print("Objective value m: ", m.objective / 1e6 )
print("\n#################\n")


###########################################
# CM
prepare_congestion_management(m, n)

logger.info("Solve n")
solve_congestion_management(n, config)

n.export_to_netcdf(results_dir + "n-r.nc")

print(n.model.constraints)
print("\n#################\n")
print("CM - n.nc")
print("Number of variables: ",n.model.nvars)
print("Number of constraints: ",n.model.ncons)
print("Objective value n (should be same as o2): ", n.objective / 1e6 )
print("n-m (CM costs in Mio): ", (n.objective - m.objective) / 1e6 )
print("ramp up [TWh]: ", (n.generators_t.p.filter(like="ramp up").groupby(n.generators.carrier, axis=1).sum().sum())
      .sum() / 1e6)
print("ramp down [TWh]: ", (n.generators_t.p.filter(like="ramp down").groupby(n.generators.carrier, axis=1).sum().sum())
      .sum() / 1e6)
print("\n#################\n")


# CM custom objective function
prepare_congestion_management(m, n_custom)

logger.info("Solve n_custom")
solve_congestion_management_custom(n_custom, m, config)

n_custom.export_to_netcdf(results_dir + "n_custom-r.nc")

print(n_custom.model.constraints)
print("\n#################\n")
print("CM CUSTOM - n_custom.nc")
print("Number of variables: ",n_custom.model.nvars)
print("Number of constraints: ",n_custom.model.ncons)
print("objective value (CM costs): ", n_custom.objective / 1e6 )
print("ramp up [TWh]: ", (n_custom.generators_t.p.filter(like="ramp up").groupby(n_custom.generators.carrier, axis=1).sum().sum())
      .sum() / 1e6)
print("ramp down [TWh]: ", (n_custom.generators_t.p.filter(like="ramp down").groupby(n_custom.generators.carrier, axis=1).sum().sum())
      .sum() / 1e6)
print("\n#################\n")


