import mcpost

vpr = mcpost.postvpr()
vpr.read_inv_data('/scratch/summit/life9360/ALASKA_work/mc_inv_files/mc_results/mc_inv.TA.TOLK.npz')
vpr.read_data('/scratch/summit/life9360/ALASKA_work/mc_inv_files/mc_results/mc_data.TA.TOLK.npz')