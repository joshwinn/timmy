import numpy as np, matplotlib.pyplot as plt, pandas as pd, pymc3 as pm
import pickle, os
from astropy import units as units, constants as const
from numpy import array as nparr

import exoplanet as xo
from exoplanet.gp import terms, GP
import theano.tensor as tt

from timmy.plotting import plot_MAP_data as plot_MAP_phot
from timmy.plotting import plot_MAP_rv

from timmy.paths import RESULTSDIR

from timmy.priors import RSTAR, RSTAR_STDEV, LOGG, LOGG_STDEV

# factor * 10**logg / r_star = rho
factor = 5.141596357654149e-05

class ModelParser:

    def __init__(self, modelid):
        self.initialize_model(modelid)

    def initialize_model(self, modelid):
        self.modelid = modelid
        self.modelcomponents = modelid.split('_')
        self.verify_modelcomponents()

    def verify_modelcomponents(self):

        validcomponents = ['transit', 'gprot', 'rv']
        for i in range(5):
            validcomponents.append('{}sincosPorb'.format(i))
            validcomponents.append('{}sincosProt'.format(i))

        assert len(self.modelcomponents) >= 1

        for modelcomponent in self.modelcomponents:
            if modelcomponent not in validcomponents:
                errmsg = (
                    'Got modelcomponent {}. validcomponents include {}.'
                    .format(modelcomponent, validcomponents)
                )
                raise ValueError(errmsg)


class ModelFitter(ModelParser):
    """
    Given a modelid of the form "transit", or "rv" and a dataframe containing
    (time and flux), or (time and rv), run the inference.
    """

    def __init__(self, modelid, data_df, prior_d, N_samples=2000, N_cores=16,
                 N_chains=4, plotdir=None, pklpath=None, overwrite=1,
                 rvdf=None):

        self.N_samples = N_samples
        self.N_cores = N_cores
        self.N_chains = N_chains
        self.PLOTDIR = plotdir
        self.OVERWRITE = overwrite

        if 'transit' in modelid:
            self.data = data_df
            self.x_obs = nparr(data_df['x_obs'])
            self.y_obs = nparr(data_df['y_obs'])
            self.y_err = nparr(data_df['y_err'])
            self.t_exp = np.nanmedian(np.diff(self.x_obs))

        if 'rv' in modelid:
            if 'transit' in modelid:
                errmsg = 'joint transit+RV not yet implemented'
                raise NotImplementedError(errmsg)

            # time,tel,Name,mnvel,errvel,Source
            self.data = data_df

            time = nparr(data_df['time'])
            mnvel = nparr(data_df['mnvel'])
            errvel = nparr(data_df['errvel'])
            telvec = nparr(data_df['tel'])

            self.uniqueinstrs = np.unique(telvec)

            self.x_obs = time
            self.y_obs = mnvel
            self.y_err = errvel
            self.telvec = telvec
            self.num_inst = len(self.uniqueinstrs)

            # map unique telescope names to color strings in plots, e.g.,
            # ['C0', 'C1', 'C0', ...]
            telcolors = []
            for t in telvec:
                ix = 0
                if t == self.uniqueinstrs[ix]:
                    telcolors.append('C{}'.format(ix))
                else:
                    while t != self.uniqueinstrs[ix]:
                        ix += 1
                    telcolors.append('C{}'.format(ix))

            self.telcolors = telcolors

        self.initialize_model(modelid)

        self.verify_inputdata()

        #NOTE threadsafety needn't be hardcoded
        make_threadsafe = False

        if modelid == 'transit':
            self.run_transit_inference(
                prior_d, pklpath, make_threadsafe=make_threadsafe
            )

        elif modelid == 'rv':
            self.run_rv_inference(
                prior_d, pklpath, make_threadsafe=make_threadsafe
            )


    def verify_inputdata(self):
        np.testing.assert_array_equal(
            self.x_obs,
            self.x_obs[np.argsort(self.x_obs)]
        )
        assert len(self.x_obs) == len(self.y_obs)
        assert isinstance(self.x_obs, np.ndarray)
        assert isinstance(self.y_obs, np.ndarray)


    def run_transit_inference(self, prior_d, pklpath, make_threadsafe=True):

        # if the model has already been run, pull the result from the
        # pickle. otherwise, run it.
        if os.path.exists(pklpath):
            d = pickle.load(open(pklpath, 'rb'))
            self.model = d['model']
            self.trace = d['trace']
            self.map_estimate = d['map_estimate']
            return 1

        with pm.Model() as model:

            # Fixed data errors.
            sigma = self.y_err

            # Define priors and PyMC3 random variables to sample over.

            # Stellar parameters. (Following tess.world notebooks).
            logg_star = pm.Normal("logg_star", mu=LOGG, sd=LOGG_STDEV)
            r_star = pm.Bound(pm.Normal, lower=0.0)(
                "r_star", mu=RSTAR, sd=RSTAR_STDEV
            )
            rho_star = pm.Deterministic(
                "rho_star", factor*10**logg_star / r_star
            )

            # Transit parameters.
            mean = pm.Normal(
                "mean", mu=prior_d['mean'], sd=1e-2, testval=prior_d['mean']
            )

            t0 = pm.Normal(
                "t0", mu=prior_d['t0'], sd=5e-3, testval=prior_d['t0']
            )

            period = pm.Normal(
                'period', mu=prior_d['period'], sd=5e-3,
                testval=prior_d['period']
            )

            # NOTE: might want to implement kwarg for flexibility
            # u = xo.distributions.QuadLimbDark(
            #     "u", testval=prior_d['u']
            # )

            u0 = pm.Uniform(
                'u[0]', lower=prior_d['u[0]']-0.15,
                upper=prior_d['u[0]']+0.15,
                testval=prior_d['u[0]']
            )
            u1 = pm.Uniform(
                'u[1]', lower=prior_d['u[1]']-0.15,
                upper=prior_d['u[1]']+0.15,
                testval=prior_d['u[1]']
            )
            u = [u0, u1]

            # # The Espinoza (2018) parameterization for the joint radius ratio and
            # # impact parameter distribution
            # r, b = xo.distributions.get_joint_radius_impact(
            #     min_radius=0.001, max_radius=1.0,
            #     testval_r=prior_d['r'],
            #     testval_b=prior_d['b']
            # )
            # # NOTE: apparently, it's been deprecated. DFM's manuscript notes
            # that it leads to Rp/Rs values biased high

            log_r = pm.Uniform('log_r', lower=np.log(1e-2), upper=np.log(1),
                               testval=prior_d['log_r'])
            r = pm.Deterministic('r', tt.exp(log_r))

            b = xo.distributions.ImpactParameter(
                "b", ror=r, testval=prior_d['b']
            )

            orbit = xo.orbits.KeplerianOrbit(
                period=period, t0=t0, b=b, rho_star=rho_star
            )

            mu_transit = pm.Deterministic(
                'mu_transit',
                xo.LimbDarkLightCurve(u).get_light_curve(
                    orbit=orbit, r=r, t=self.x_obs, texp=self.t_exp
                ).T.flatten()
            )

            #
            # Derived parameters
            #

            # planet radius in jupiter radii
            r_planet = pm.Deterministic(
                "r_planet", (r*r_star)*( 1*units.Rsun/(1*units.Rjup) ).cgs.value
            )

            #
            # eq 30 of winn+2010, ignoring planet density.
            #
            a_Rs = pm.Deterministic(
                "a_Rs",
                (rho_star * period**2)**(1/3)
                *
                (( (1*units.gram/(1*units.cm)**3) * (1*units.day**2)
                  * const.G / (3*np.pi)
                )**(1/3)).cgs.value
            )

            #
            # cosi. assumes e=0 (e.g., Winn+2010 eq 7)
            #
            cosi = pm.Deterministic("cosi", b / a_Rs)

            # probably safer than tt.arccos(cosi)
            sini = pm.Deterministic("sini", pm.math.sqrt( 1 - cosi**2 ))

            #
            # transit durations (T_14, T_13) for circular orbits. Winn+2010 Eq 14, 15.
            # units: hours.
            #
            T_14 = pm.Deterministic(
                'T_14',
                (period/np.pi)*
                tt.arcsin(
                    (1/a_Rs) * pm.math.sqrt( (1+r)**2 - b**2 )
                    * (1/sini)
                )*24
            )

            T_13 =  pm.Deterministic(
                'T_13',
                (period/np.pi)*
                tt.arcsin(
                    (1/a_Rs) * pm.math.sqrt( (1-r)**2 - b**2 )
                    * (1/sini)
                )*24
            )

            #
            # mean model and likelihood
            #

            mean_model = mu_transit + mean

            mu_model = pm.Deterministic('mu_model', mean_model)

            likelihood = pm.Normal('obs', mu=mean_model, sigma=sigma,
                                   observed=self.y_obs)

            # Optimizing
            map_estimate = pm.find_MAP(model=model)

            # start = model.test_point
            # if 'transit' in self.modelcomponents:
            #     map_estimate = xo.optimize(start=start,
            #                                vars=[r, b, period, t0])
            # map_estimate = xo.optimize(start=map_estimate)

            # Plot the simulated data and the maximum a posteriori model to
            # make sure that our initialization looks ok.
            self.y_MAP = (
                map_estimate['mean'] + map_estimate['mu_transit']
            )

            if make_threadsafe:
                pass
            else:
                # as described in
                # https://github.com/matplotlib/matplotlib/issues/15410
                # matplotlib is not threadsafe. so do not make plots before
                # sampling, because some child processes tries to close a
                # cached file, and crashes the sampler.

                print(map_estimate)

                if self.PLOTDIR is None:
                    raise NotImplementedError
                outpath = os.path.join(self.PLOTDIR,
                                       'test_{}_MAP.png'.format(self.modelid))
                plot_MAP_phot(self.x_obs, self.y_obs, self.y_MAP, outpath)

            # sample from the posterior defined by this model.
            trace = pm.sample(
                tune=self.N_samples, draws=self.N_samples,
                start=map_estimate, cores=self.N_cores,
                chains=self.N_chains,
                step=xo.get_dense_nuts_step(target_accept=0.8),
            )

        with open(pklpath, 'wb') as buff:
            pickle.dump({'model': model, 'trace': trace,
                         'map_estimate': map_estimate}, buff)

        self.model = model
        self.trace = trace
        self.map_estimate = map_estimate


    def run_rv_inference(self, prior_d, pklpath, make_threadsafe=True):

        # if the model has already been run, pull the result from the
        # pickle. otherwise, run it.
        if os.path.exists(pklpath):
            d = pickle.load(open(pklpath, 'rb'))
            self.model = d['model']
            self.trace = d['trace']
            self.map_estimate = d['map_estimate']
            return 1

        with pm.Model() as model:

            # Fixed data errors.
            sigma = self.y_err

            # Define priors and PyMC3 random variables to sample over.

            # Stellar parameters. (Following tess.world notebooks).
            logg_star = pm.Normal("logg_star", mu=prior_d['logg_star'][0],
                                  sd=prior_d['logg_star'][1])

            r_star = pm.Bound(pm.Normal, lower=0.0)(
                "r_star", mu=prior_d['r_star'][0], sd=prior_d['r_star'][1]
            )
            rho_star = pm.Deterministic(
                "rho_star", factor*10**logg_star / r_star
            )

            # RV parameters.

            # Chen & Kipping predicted M: 49.631 Mearth, based on Rp of 8Re. It
            # could be bigger, e.g., 94m/s if 1 Mjup.
            # Predicted K: 14.26 m/s

            #K = pm.Lognormal("K", mu=np.log(prior_d['K'][0]),
            #                 sigma=prior_d['K'][1])
            log_K = pm.Uniform('log_K', lower=prior_d['log_K'][0],
                               upper=prior_d['log_K'][1])
            K = pm.Deterministic('K', tt.exp(log_K))

            period = pm.Normal("period", mu=prior_d['period'][0],
                               sigma=prior_d['period'][1])

            ecs = xo.UnitDisk("ecs", testval=np.array([0.7, -0.3]))
            ecc = pm.Deterministic("ecc", tt.sum(ecs ** 2))

            omega = pm.Deterministic("omega", tt.arctan2(ecs[1], ecs[0]))

            phase = xo.UnitUniform("phase")

            # use time of transit, rather than time of periastron. we do, after
            # all, know it.
            t0 = pm.Normal(
                "t0", mu=prior_d['t0'][0], sd=prior_d['t0'][1],
                testval=prior_d['t0'][0]
            )

            orbit = xo.orbits.KeplerianOrbit(
                period=period, t0=t0, rho_star=rho_star, ecc=ecc, omega=omega
            )

            #FIXME edit these
            # noise model parameters: FIXME what are these?
            S_tot = pm.Lognormal("S_tot", mu=np.log(prior_d['S_tot'][0]),
                                 sigma=prior_d['S_tot'][1])
            ell = pm.Lognormal("ell", mu=np.log(prior_d['ell'][0]),
                               sigma=prior_d['ell'][1])

            # per instrument parameters
            means = pm.Normal(
                "means",
                mu=np.array([np.median(self.y_obs[self.telvec == u]) for u in
                             self.uniqueinstrs]),
                sigma=500,
                shape=self.num_inst,
            )

            # different instruments have different intrinsic jitters. assign
            # those based on the reported error bars. (NOTE: might inflate or
            # overwrite these, for say, CHIRON)
            sigmas = pm.HalfNormal(
                "sigmas",
                sigma=np.array([np.median(self.y_err[self.telvec == u]) for u
                                in self.uniqueinstrs]),
                shape=self.num_inst
            )

            # Compute the RV offset and jitter for each data point depending on
            # its instrument
            mean = tt.zeros(len(self.x_obs))
            diag = tt.zeros(len(self.x_obs))
            for i, u in enumerate(self.uniqueinstrs):
                mean += means[i] * (self.telvec == u)
                diag += (self.y_err ** 2 + sigmas[i] ** 2) * (self.telvec == u)
            pm.Deterministic("mean", mean)
            pm.Deterministic("diag", diag)

            # NOTE: local function definition is jank
            def rv_model(x):
                return orbit.get_radial_velocity(x, K=K)

            kernel = xo.gp.terms.SHOTerm(S_tot=S_tot, w0=2*np.pi/ell, Q=1.0/3)
            # NOTE temp
            gp = xo.gp.GP(kernel, self.x_obs, diag, mean=rv_model)
            # gp = xo.gp.GP(kernel, self.x_obs, diag,
            #               mean=orbit.get_radial_velocity(self.x_obs, K=K))
            # the actual "conditioning" step, i.e. the likelihood definition
            gp.marginal("obs", observed=self.y_obs-mean)
            pm.Deterministic("gp_pred", gp.predict())

            map_estimate = model.test_point
            map_estimate = xo.optimize(map_estimate, [means])
            map_estimate = xo.optimize(map_estimate, [means, phase])
            map_estimate = xo.optimize(map_estimate, [means, phase, log_K])
            map_estimate = xo.optimize(map_estimate, [means, t0, log_K, period, ecs])
            map_estimate = xo.optimize(map_estimate, [sigmas, S_tot, ell])
            map_estimate = xo.optimize(map_estimate)

            #
            # Derived parameters
            #

            #TODO
            # # planet radius in jupiter radii
            # r_planet = pm.Deterministic(
            #     "r_planet", (r*r_star)*( 1*units.Rsun/(1*units.Rjup) ).cgs.value
            # )

        # Plot the simulated data and the maximum a posteriori model to
        # make sure that our initialization looks ok.

        # i.e., "detrended". the "rv data" are y_obs - mean. The "trend" model
        # is a GP. FIXME: AFAIK, it doesn't do much as-implemented.
        self.y_MAP = (
            self.y_obs - map_estimate["mean"] - map_estimate["gp_pred"]
        )

        t_pred = np.linspace(
            self.x_obs.min() - 10, self.x_obs.max() + 10, 10000
        )

        with model:
            # NOTE temp
            y_pred_MAP = xo.eval_in_model(rv_model(t_pred), map_estimate)
            # # NOTE temp
            # y_pred_MAP = xo.eval_in_model(
            #     orbit.get_radial_velocity(t_pred, K=K), map_estimate
            # )

        self.x_pred = t_pred
        self.y_pred_MAP = y_pred_MAP


        if make_threadsafe:
            pass
        else:
            # as described in
            # https://github.com/matplotlib/matplotlib/issues/15410
            # matplotlib is not threadsafe. so do not make plots before
            # sampling, because some child processes tries to close a
            # cached file, and crashes the sampler.

            print(map_estimate)

            if self.PLOTDIR is None:
                raise NotImplementedError
            outpath = os.path.join(self.PLOTDIR,
                                   'test_{}_MAP.png'.format(self.modelid))

            plot_MAP_rv(self.x_obs, self.y_obs, self.y_MAP, self.y_err,
                        self.telcolors, self.x_pred, self.y_pred_MAP,
                        map_estimate, outpath)

        with model:
            # sample from the posterior defined by this model.
            trace = pm.sample(
                tune=self.N_samples, draws=self.N_samples,
                start=map_estimate, cores=self.N_cores,
                chains=self.N_chains,
                step=xo.get_dense_nuts_step(target_accept=0.8),
            )

        # with open(pklpath, 'wb') as buff:
        #     pickle.dump({'model': model, 'trace': trace,
        #                  'map_estimate': map_estimate}, buff)

        self.model = model
        self.trace = trace
        self.map_estimate = map_estimate
