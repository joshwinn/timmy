"""
Fit data for transit alone, after "detrending" the stellar variability.
"""
import os
import numpy as np, pandas as pd, matplotlib.pyplot as plt, pymc3 as pm
from os.path import join
from itertools import product

from timmy.modelfitter import ModelFitter, ModelParser
import timmy.plotting as tp
from timmy.convenience import get_clean_data, detrend_data, _write_vespa
from timmy.priors import initialize_prior_d
from timmy.paths import RESULTSDIR

def main(modelid):

    make_threadsafe = 0
    phaseplot = 1
    cornerplot = 0

    writevespa = 0
    traceplot = 0
    sampleplot = 0
    splitsignalplot = 0
    binsize = None # or 120*5

    OVERWRITE = 1
    REALID = 'TOI_837'
    provenance = 'spoc' # could be "cdips"
    yval = 'PDCSAP_FLUX' # could be SAP_FLUX 

    PLOTDIR = os.path.join(
        RESULTSDIR, '{}_{}_phot_results'.format(REALID, modelid)
    )
    if not os.path.exists(PLOTDIR):
        os.mkdir(PLOTDIR)
    PLOTDIR = os.path.join(PLOTDIR, '20200518')

    ##########################################

    assert modelid == 'transit'

    print(42*'#')
    print(modelid)
    print(42*'#')

    if not os.path.exists(PLOTDIR):
        os.mkdir(PLOTDIR)

    pklpath = os.path.join(
        os.path.expanduser('~'), 'local', 'timmy',
        '{}_model_{}.pkl'.format(REALID, modelid)
    )
    np.random.seed(42)

    x_obs, y_obs, y_err = get_clean_data(provenance, yval, binsize=binsize,
                                         maskflares=1)
    y_flat, y_trend = detrend_data(x_obs, y_obs, y_err)
    s = np.isfinite(y_flat) & np.isfinite(x_obs) & np.isfinite(y_err)
    x_obs, y_flat, y_err = x_obs[s], y_flat[s], y_err[s]

    if writevespa:
        _write_vespa(x_obs, y_flat, y_err)
        return

    # note: we're fitting the detrended data
    mp = ModelParser(modelid)
    prior_d = initialize_prior_d(mp.modelcomponents)

    m = ModelFitter(modelid, x_obs, y_flat, y_err, prior_d, plotdir=PLOTDIR,
                    pklpath=pklpath, overwrite=OVERWRITE)

    print(pm.summary(m.trace, var_names=list(prior_d.keys())))
    summdf = pm.summary(m.trace, var_names=list(prior_d.keys()), round_to=10,
                        kind='stats', stat_funcs={'median':np.nanmedian},
                        extend=True)

    if make_threadsafe:
        pass

    else:
        if traceplot:
            outpath = join(PLOTDIR, '{}_{}_traceplot.png'.format(REALID, modelid))
            tp.plot_traceplot(m, outpath)

        if sampleplot:
            outpath = join(PLOTDIR, '{}_{}_sampleplot.png'.format(REALID, modelid))
            tp.plot_sampleplot(m, outpath, N_samples=100)

        if phaseplot:
            outpath = join(PLOTDIR, '{}_{}_phaseplot.png'.format(REALID, modelid))
            tp.plot_phasefold(m, summdf, outpath)

        if splitsignalplot:
            outpath = join(PLOTDIR, '{}_{}_splitsignalmap.png'.format(REALID, modelid))
            ydict = tp.plot_splitsignal_map(m, outpath)

        if cornerplot:
            outpath = join(PLOTDIR, '{}_{}_cornerplot.png'.format(REALID, modelid))
            tp.plot_cornerplot(prior_d, m, outpath)


if __name__ == "__main__":
    main('transit')
