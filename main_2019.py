import pypsa
import pandas as pd
import os

import logging
logger = logging.getLogger(__name__)
# Suppress logging of the slack bus choices
pypsa.pf.logger.setLevel(logging.WARNING)

from ED_CM import *

import yaml
with open("config.yaml", "r") as f:
    config = yaml.load(f, Loader=yaml.FullLoader)


###############################################################################


def solve_network_dispatch(n, config):

    def extra_functionality(n, snapshots):

        sus = n.model.variables["StorageUnit-state_of_charge"]
        min_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.001
        max_soc = n.storage_units.max_hours * n.storage_units.p_nom * 0.999
        n.model.add_constraints(sus >= min_soc, name="StorageUnit-minimum_soc")
        n.model.add_constraints(sus <= max_soc, name="StorageUnit-maximum_soc")


    formulation = config["s2019"]['solving']['options']['formulation']
    solver_options = config["s2019"]['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )


def solve_economic_dispatch(m, config):
    
    formulation = config["s2019"]['solving']['options']['formulation']
    solver_options = config["s2019"]['solving']['solver']
    solver_name = solver_options['name']

    m.optimize(
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )


def solve_congestion_management(n, config):

    formulation = config["s2019"]['solving']['options']['formulation']
    solver_options = config["s2019"]['solving']['solver']
    solver_name = solver_options['name']
    
    n.optimize(
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )
    

def solve_congestion_management_custom(n,m,config):

    def extra_functionality(n, snapshots):
        
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

    formulation = config["s2019"]['solving']['options']['formulation']
    solver_options = config["s2019"]['solving']['solver']
    solver_name = solver_options['name']

    n.optimize(
            extra_functionality=extra_functionality,
            formulation=formulation,
            solver_name=solver_name,
            solver_options=solver_options,
            )



# import network -------------------------------------------------------
o2 = pypsa.Network(config["s2019"]['network_file'])

##################################

#o2.set_snapshots(list(o2.snapshots[0:250]))

##################################

results_dir = "results/2019/"
if not os.path.exists(results_dir):
        os.makedirs(results_dir)


# Set up network ------------------------------------------------

# Remove sus with max_hours=0
o2.mremove(
    "StorageUnit",
    o2.storage_units[o2.storage_units["max_hours"]==0].index
)


###############################################################################
# Nodal Dispatch 2019
logger.info("Solve o2")
solve_network_dispatch(o2, config)

print(o2.model.constraints)
print("\n#################\n")
print("Power system 2019 (dispatch) - o2.nc")
print("Number of variables: ",o2.model.nvars)
print("Number of constraints: ",o2.model.ncons)
print("Objective value o2 (Nodal Dispatch 2019): ", o2.objective / 1e6 )
print("\n#################\n")

o2.export_to_netcdf(results_dir + "o2-19.nc")

###############################################################################
# ED + CM Preparation

o2_temp = o2.copy()

# Fix variables
storage_units_soc_initial = o2.storage_units_t.state_of_charge.iloc[-1,:]
	# storage_units
o2_temp.storage_units["state_of_charge_initial"] = storage_units_soc_initial
o2_temp.storage_units.cyclic_state_of_charge = False
o2_temp.storage_units_t.p_dispatch_set = o2.storage_units_t.p_dispatch
o2_temp.storage_units_t.p_store_set = o2.storage_units_t.p_store


m = o2_temp.copy()  # for market model
n = o2_temp.copy()  # for redispatch model
n_custom = o2_temp.copy()  # for redispatch model


###########################################
# ED
prepare_economic_dispatch(m)

logger.info("Solve m")
solve_economic_dispatch(m, config)

print(m.model.constraints)
print("\n#################\n")
print("ED - m.nc")
print("Number of variables: ",m.model.nvars)
print("Number of constraints: ",m.model.ncons)
print("Objective value m: ", m.objective / 1e6 )
print("\n#################\n")

m.export_to_netcdf(results_dir + "m-19.nc")

###########################################
# CM
prepare_congestion_management(m, n)

logger.info("Solve n")
solve_congestion_management(n, config)

print(n.model.constraints)
print("\n#################\n")
print("CM - n.nc")
print("Number of variables: ",n.model.nvars)
print("Number of constraints: ",n.model.ncons)
print("Objective value n (should be same as o (nodal dispatch) here): ", n.objective / 1e6 )
print("n-m (CM costs in Mio): ", (n.objective - m.objective) / 1e6 )
print("ramp up [TWh]: ", (n.generators_t.p.filter(like="ramp up").groupby(n.generators.carrier, axis=1).sum().sum()).sum() / 1e6)
print("ramp down [TWh]: ", (n.generators_t.p.filter(like="ramp down").groupby(n.generators.carrier, axis=1).sum().sum()).sum() / 1e6)
print("\n#################\n")

n.export_to_netcdf(results_dir + "n-19.nc")

# CM custom objective function
prepare_congestion_management(m, n_custom)

logger.info("Solve n_custom")
solve_congestion_management_custom(n_custom, m, config)


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

n_custom.export_to_netcdf(results_dir + "n_custom-19.nc")
