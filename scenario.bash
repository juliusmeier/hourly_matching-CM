#!/bin/sh
#BSUB -q hpc			    # Specify the queue you want your job to be run in (-q flag). Notice that different queues have different defaults, and access to specific queue can be restricted to specific groups of users.
#BSUB -J scenario_2030		# name of job
#BSUB -n 20			        # total number of cores (processors)
#BSUB -R "span[hosts=1]"	# hosts=1 -> all cores must be on one single host, therefore it is not possible to request more cores than the number of physical cores present on a machine. 
#BSUB -W 22:00			    # Maximum runtime of job
#BSUB -u s210280@dtu.dk
#BSUB -R "rusage[mem=16GB]"	# Memory per core
#BSUB -B
#BSUB -N
#BSUB -o Output_%J.out 
#BSUB -e Error_%J.err

echo "Start"

source /zhome/84/b/160280/miniconda3/etc/profile.d/conda.sh		# activate conda and the virtual
conda activate pypsa-eur

echo "Active environment: "
echo $CONDA_DEFAULT_ENV

module load gurobi/10.0.0

pwd

echo "Start python script"
python main.py

echo "End"