# -*- coding: utf-8 -*-
"""
Module for results and postprocessing of MC inversion

:Copyright:
    Author: Lili Feng
    Graduate Research Assistant
    CIEI, Department of Physics, University of Colorado Boulder
    email: lili.feng@colorado.edu
"""
import vmodel, modparam, data, vprofile
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import matplotlib
import copy
import numba
import os

def to_percent(y, position):
    # Ignore the passed in position. This has the effect of scaling the default
    # tick locations.
    s = str(100. * y)
    # The percent symbol needs escaping in latex
    if matplotlib.rcParams['text.usetex'] is True:
        return s + r'$\%$'
    else:
        return s + '%'
    
@numba.jit(numba.float64[:](numba.float64[:]))
def _get_running_min(data):
    N       = data.size
    outdata = np.zeros(N, dtype=float)
    for i in range(N):
        outdata[i]  = data[:(i+1)].min()
    return outdata

def compute_histogram_bins(data, desired_bin_size):
    min_val = np.min(data)
    max_val = np.max(data)
    min_boundary = -1.0 * (min_val % desired_bin_size - min_val)
    max_boundary = max_val - max_val % desired_bin_size + desired_bin_size
    n_bins = int((max_boundary - min_boundary) / desired_bin_size) + 1
    bins = np.linspace(min_boundary, max_boundary, n_bins)
    return bins
    
class postvpr(object):
    """
    An object for post data processing of 1D velocity profile inversion
    =====================================================================================================================
    ::: parameters :::
    : --- arrays --- :
    invdata         - data arrays storing inversion results
    disppre_ph/gr   - predicted phase/group dispersion
    rfpre           - object storing 1D model
    ind_acc         - index array indicating accepted models
    ind_rej         - index array indicating rejected models
    ind_thresh      - index array indicating models that pass the misfit criterion
    misfit          - misfit array
    : --- values --- :
    numbrun         - number of total runs
    numbacc         - number of accepted models
    numbrej         - number of rejected models
    npara           - number of parameters for inversion
    min_misfit      - minimum misfit value
    ind_min         - index of the minimum misfit
    factor          - factor to determine the threshhold value for selectingthe finalized model
    thresh          - threshhold value for selecting the finalized model
                        misfit < min_misfit*factor + thresh
    stdfactor       - C_predict at all periods should be within bounds :[C_obs - stdfactor*std, C_obs + stdfactor*std]
                        the same apply to group speed
                        --- added Sep 7th, 2018
    : --- object --- :
    data            - data object storing obsevred data
    avg_model       - average model object
    min_model       - minimum misfit model object
    init_model      - inital model object
    real_model      - real model object, used for synthetic test only
    temp_model      - temporary model object, used for analysis of the full assemble of the finally accepted models
    vprfwrd         - vprofile1d object for forward modelling of the average model
    =====================================================================================================================
    """
    def __init__(self, factor=1., thresh=0.5, waterdepth=-1., vpwater=1.5, stdfactor=2.):
        self.data           = data.data1d()
        self.factor         = factor
        self.thresh         = thresh
        self.stdfactor      = stdfactor
        # models    
        self.avg_model      = vmodel.model1d()
        self.min_model      = vmodel.model1d()
        self.init_model     = vmodel.model1d()
        self.real_model     = vmodel.model1d()
        self.temp_model     = vmodel.model1d()
        # 
        self.vprfwrd        = vprofile.vprofile1d()
        self.waterdepth     = waterdepth
        self.vpwater        = vpwater
        self.prior_paraval  = np.array([])
        self.std_prior      = np.array([])
        #
        self.avg_misfit     = 0.
        self.code           = ''
        return
    
    def read_inv_data(self, infname, verbose=True, thresh_misfit=None, Nmax=None, Nmin=None):
        """
        read inversion results from an input compressed npz file
        """
        inarr           = np.load(infname)
        self.invdata    = inarr['arr_0']
        self.disppre_ray= inarr['arr_1']
        self.disppre_lov= inarr['arr_2']
        # 
        self.numbrun    = self.invdata.shape[0]
        self.npara      = self.invdata.shape[1] - 9
        self.ind_acc    = self.invdata[:, 0] == 1.
        self.ind_rej    = self.invdata[:, 0] == -1.
        self.misfit     = self.invdata[:, self.npara+3]
        self.min_misfit = self.misfit[self.ind_acc + self.ind_rej].min()
        self.ind_min    = np.where(self.misfit == self.min_misfit)[0][0]
        self.get_thresh_model(thresh_misfit = thresh_misfit, Nmax = Nmax, Nmin = Nmin)
        self.mean_misfit= (self.misfit[self.ind_thresh]).mean()
        self.numbacc    = np.where(self.ind_acc)[0].size
        self.numbrej    = np.where(self.ind_rej)[0].size
        if verbose:
            print '--- number of runs                   = '+ str(self.numbrun)
            print '--- number of accepted models        = '+ str(self.numbacc)
            print '--- number of rejected models        = '+ str(self.numbrej)
            print '--- number of invalid models         = '+ str(self.numbrun - self.numbacc - self.numbrej)
            print '--- number of finally accepted models= '+ str(self.ind_thresh.size)
            print '--- minimum misfit                   = '+ str(self.min_misfit)
        return
    
    def get_thresh_model(self, thresh_misfit=None, Nmax=None, Nmin=None):
        """
        get the index for the finalized accepted model
        adaptively change thresh and stdfactor to make accpeted model around a specified value(Nmin ~ Nmax)
        """
        if thresh_misfit is None:
            thresh_val  = self.min_misfit*self.factor+ self.thresh
        else:
            thresh_val  = thresh_misfit
        ind_thresh      = self.ind_acc*(self.misfit<= thresh_val)
        # ind_thresh      = (self.misfit<= thresh_val)
        # added 09/07/2018
        # while loop to adjust threshold misfit value according to Nmax/Nmin
        if Nmax is not None:
            Nacc                = np.where(self.ind_acc)[0].size
            if Nmax > Nacc:
                print 'WARNING: Nmax is reset from '+str(Nmax)+' to '+str(Nacc)
                Nmax            = Nacc
            temp_ind            = np.where(ind_thresh)[0]
            while (temp_ind.size > Nmax):
                thresh_val      -= 0.05
                ind_thresh      = self.ind_acc*(self.misfit<= thresh_val)
                temp_ind        = np.where(ind_thresh)[0]
        if Nmin is not None:
            temp_ind            = np.where(ind_thresh)[0]
            while (temp_ind.size < Nmin):
                thresh_val      += 0.05
                ind_thresh      = self.ind_acc*(self.misfit<= thresh_val)
                temp_ind        = np.where(ind_thresh)[0]
        self.thresh_val         = thresh_val
        ind_thresh_temp         = ind_thresh.copy()
        if self.stdfactor is not None:
            if self.data.dispR.npper > 0:
                cmax    = self.data.dispR.pvelo + self.stdfactor*self.data.dispR.stdpvelo
                cmin    = self.data.dispR.pvelo - self.stdfactor*self.data.dispR.stdpvelo
                ind_thresh   \
                        =  ind_thresh * np.all(self.disppre_ray <= cmax, axis=1)
                ind_thresh   \
                        =  ind_thresh * np.all(self.disppre_ray >= cmin, axis=1)
            if self.data.dispL.npper > 0:
                umax    = self.data.dispL.pvelo + self.stdfactor*self.data.dispL.stdpvelo
                umin    = self.data.dispL.pvelo - self.stdfactor*self.data.dispL.stdpvelo
                ind_thresh   \
                        =  ind_thresh * np.all(self.disppre_lov <= umax, axis=1)
                ind_thresh   \
                        =  ind_thresh * np.all(self.disppre_lov >= umin, axis=1)
            # added 09/07/2018
            # while loop to adjust threshold misfit value according to Nmax/Nmin
            if Nmin is not None:
                temp_ind= np.where(ind_thresh)[0]
                while (temp_ind.size < Nmin):
                    self.stdfactor  += 0.5
                    ind_thresh      = ind_thresh_temp.copy()
                    if self.data.dispR.npper > 0:
                        cmax    = self.data.dispR.pvelo + self.stdfactor*self.data.dispR.stdpvelo
                        cmin    = self.data.dispR.pvelo - self.stdfactor*self.data.dispR.stdpvelo
                        ind_thresh   \
                                =  ind_thresh * np.all(self.disppre_ray <= cmax, axis=1)
                        ind_thresh   \
                                =  ind_thresh * np.all(self.disppre_ray >= cmin, axis=1)
                    if self.data.dispL.npper > 0:
                        umax    = self.data.dispL.pvelo + self.stdfactor*self.data.dispL.stdpvelo
                        umin    = self.data.dispL.pvelo - self.stdfactor*self.data.dispL.stdpvelo
                        ind_thresh   \
                                =  ind_thresh * np.all(self.disppre_lov <= umax, axis=1)
                        ind_thresh   \
                                =  ind_thresh * np.all(self.disppre_lov >= umin, axis=1)
                    temp_ind        = np.where(ind_thresh)[0]
        self.ind_thresh = np.where(ind_thresh)[0]
        return
    
    def get_thresh_model_2(self, thresh_misfit=None, Nmax=None, Nmin=None):
        """
        get the index for the finalized accepted model
        adaptively change thresh and stdfactor to make accpeted model around a specified value(Nmin ~ Nmax)
        """
        if thresh_misfit is None:
            thresh_val  = self.min_misfit*self.factor+ self.thresh
        else:
            thresh_val  = thresh_misfit
        ind_thresh      = self.ind_acc*(self.misfit<= thresh_val)
        self.ind_thresh = np.where(ind_thresh)[0]
        return
    
    def get_paraval(self):
        """
        get the parameter array for the minimum misfit model and the average of the accepted model
        """
        self.min_paraval    = self.invdata[self.ind_min, 2:(self.npara+2)]
        self.avg_paraval    = (self.invdata[self.ind_thresh, 2:(self.npara+2)]).mean(axis=0)
        # uncertainties, note that crustal thickness is determined by the last two parameters
        # thus, the last element of the sem and std array is for crustal thickness, NOT the crustal thickness excluding sediments
        temp_paraval        = self.invdata[self.ind_thresh, 2:(self.npara+2)]
        temp_paraval[:, -3] += temp_paraval[:, -4]
        self.sem_paraval    = (temp_paraval).std(axis=0) / np.sqrt(temp_paraval.shape[0])
        self.std_paraval    = (temp_paraval).std(axis=0)
        return
    
    def get_vmodel(self, real_paraval=None):
        """
        get the minimum misfit and average model from the inversion data array
        """
        self.get_paraval()
        # minimum model
        min_paraval         = self.min_paraval
        if self.waterdepth <= 0.:
            self.min_model.get_para_model_vti(paraval=min_paraval)
        else:
            self.min_model.get_para_model_vti(paraval=min_paraval, waterdepth=self.waterdepth, vpwater=self.vpwater, nmod=4, \
                numbp=np.array([1, 2, 4, 5]), mtype = np.array([5, 4, 2, 2]), vpvs = np.array([0, 2., 1.75, 1.75]), maxdepth=200.)
        self.min_model.vtimod.mod2para()
        # average model
        avg_paraval         = self.avg_paraval
        if self.waterdepth <= 0.:
            self.avg_model.get_para_model_vti(paraval=avg_paraval)
        else:
            self.avg_model.get_para_model_vti(paraval=avg_paraval, waterdepth=self.waterdepth, vpwater=self.vpwater, nmod=4, \
                numbp=np.array([1, 2, 4, 5]), mtype = np.array([5, 4, 2, 2]), vpvs = np.array([0, 2., 1.75, 1.75]), maxdepth=200.)
        self.vprfwrd.model  = self.avg_model
        self.avg_model.vtimod.mod2para()
        if real_paraval is not None:
            if self.waterdepth <= 0.:
                self.real_model.get_para_model_vti(paraval=real_paraval)
            else:
                self.real_model.get_para_model_vti(paraval=real_paraval, waterdepth=self.waterdepth, vpwater=self.vpwater, nmod=4, \
                    numbp=np.array([1, 2, 4, 5]), mtype = np.array([5, 4, 2, 2]), vpvs = np.array([0, 2., 1.75, 1.75]), maxdepth=200.)
            self.real_model.vtimod.mod2para()
        return
    
    def read_data(self, pfx):
        """
        read observed and initial predicted data from an input npz file
        """
        try:
            infname         = pfx+'_0.ph.ray.disp'
            self.data.dispR.readdisptxt_predict(infname=infname, dtype='ph')
            infname         = pfx+'_0.ph.lov.disp'
            self.data.dispL.readdisptxt_predict(infname=infname, dtype='ph')
        except:
            infname         = pfx+'.ph.ray.disp'
            self.data.dispR.readdisptxt_predict(infname=infname, dtype='ph')
            infname         = pfx+'.ph.lov.disp'
            self.data.dispL.readdisptxt_predict(infname=infname, dtype='ph')
        self.data.get_misfit_vti()
        self.init_misfit    = self.data.misfit
        return
    # # # 
    # # # def read_data_2(self, pfx):
    # # #     """
    # # #     read observed and initial predicted data from an input npz file
    # # #     """
    # # #     try:
    # # #         infname         = pfx+'_0.ph.ray.disp'
    # # #         self.data.dispR.readdisptxt_predict(infname=infname, dtype='ph')
    # # #         infname         = pfx+'_0.ph.lov.disp'
    # # #         self.data.dispL.readdisptxt_predict(infname=infname, dtype='ph')
    # # #     except:
    # # #         infname         = pfx+'.ph.ray.disp'
    # # #         self.data.dispR.readdisptxt_predict(infname=infname, dtype='ph')
    # # #         infname         = pfx+'.ph.lov.disp'
    # # #         self.data.dispL.readdisptxt_predict(infname=infname, dtype='ph')
    # # #     self.data.get_misfit_vti()
    # # #     self.init_misfit    = self.data.misfit
    # # #     return
    
    def get_period(self):
        """
        get period array for forward modelling
        """
        if self.data.dispR.npper>0:
            self.vprfwrd.TRp    = self.data.dispR.pper.copy()
        if self.data.dispR.ngper>0:
            self.vprfwrd.TRg    = self.data.dispR.gper.copy()
        if self.data.dispL.npper>0:
            self.vprfwrd.TLp    = self.data.dispL.pper.copy()
        if self.data.dispL.ngper>0:
            self.vprfwrd.TLg    = self.data.dispL.gper.copy()
        return
    
    def run_avg_fwrd(self):
        """
        run and store surface wave dispersion for the average model
        """
        self.get_period()
        self.get_vmodel()
        self.vprfwrd.update_mod(mtype = 'vti')
        self.vprfwrd.get_vmodel(mtype = 'vti')
        self.vprfwrd.data   = copy.deepcopy(self.data)
        self.vprfwrd.compute_disp_vti(wtype='both', solver_type=1)
        self.vprfwrd.get_misfit(mtype = 'vti')
        self.avg_misfit     = self.vprfwrd.data.misfit
        return
    
    def run_prior_fwrd(self, workingdir = './prior_sampling_vti',  isconstrt=False,
            step4uwalk=1500, numbrun=15000, subsize=1000, nprocess=10, overwrite=False):
        """
        run and store sampled models from prior distribution
        """
        invfname        = workingdir+'/mc_inv.' + self.code+'.npz'
        temp_vpr        = vprofile.vprofile1d()
        temp_vpr.data   = copy.deepcopy(self.data)
        temp_vpr.model.vtimod.parameterize_ray(paraval = self.prior_paraval, topovalue = - self.waterdepth,\
                                            maxdepth=200., vp_water=self.vpwater)
        temp_vpr.model.vtimod.get_paraind_gamma(std_paraval = self.std_prior)
        if not os.path.isfile(invfname) or overwrite:
            temp_vpr.mc_joint_inv_vti_mp(outdir = workingdir, run_inv=False, solver_type=0, isconstrt=isconstrt, pfx=self.code,\
                verbose=True, step4uwalk=step4uwalk, numbrun=numbrun, savedata=True, subsize=subsize, \
                nprocess=nprocess )
        vpr             = postvpr(waterdepth = self.waterdepth)
        vpr.read_inv_data(infname = invfname, verbose=False)
        vpr.get_paraval()
        self.prior_vpr  = vpr
        return
    
        # def get_ensemble(self, maxdepth=200., dz=0.1):
    #     """
    #     get the ensemble vs array (num_accepted_models, num_grid_depth)
    #     """
    #     Nz          = int(maxdepth/dz) + 1
    #     zArr        = np.arange(Nz)*dz
    #     vs_ensemble = np.zeros([self.ind_thresh.size, Nz])
    #     i           = 0
    #     for index in self.ind_thresh:
    #         paraval = self.invdata[index, 2:(self.npara+2)]
    #         vel_mod = vmodel.model1d()
    #         if self.waterdepth > 0.:
    #             vel_mod.get_para_model(paraval = paraval, waterdepth=self.waterdepth, vpwater=self.vpwater, nmod=4, \
    #                 numbp=np.array([1, 2, 4, 5]), mtype = np.array([5, 4, 2, 2]), vpvs = np.array([0, 2., 1.75, 1.75]), maxdepth=maxdepth)
    #         else:
    #             vel_mod.get_para_model(paraval = paraval)
    #         # topography is not considerred yet!!!
    #         zArr_in, VsvArr_in  = vel_mod.get_grid_mod()
    #         # # # if topovalue > 0.:
    #         # # #     zArr_in         = zArr_in - topovalue
    #         # # interpolation
    #         vs_interp           = np.interp(zArr, xp = zArr_in, fp = VsvArr_in)
    #         vs_ensemble[i, :]   = vs_interp[:]
    #         i                   += 1
    #     self.vs_ensemble        = vs_ensemble
    #     self.zArr_ensemble      = zArr
    #     return
    
    # def get_vs_std(self):
    #     """
    #     get the std, upper and lower bounds of the vs
    #     """
    #     self.vs_upper_bound     = self.vs_ensemble.max(axis=0)
    #     self.vs_lower_bound     = self.vs_ensemble.min(axis=0)
    #     self.vs_std             = self.vs_ensemble.std(axis=0)
    #     self.vs_mean            = self.vs_ensemble.mean(axis=0)
    #     zArr, VsvArr            = self.avg_model.get_grid_mod()
    #     self.vs_avg             = np.interp(self.zArr_ensemble, xp = zArr, fp = VsvArr)
    #     self.vs_1sig_upper      = self.vs_mean + self.vs_std
    #     self.vs_1sig_lower      = self.vs_mean - self.vs_std
    #     return
    #------------------------
    # functions for plotting
    #------------------------
    
    
    def plot_disp(self, title='Dispersion curves', initdisp=True, obsdisp=True, mindisp=True, avgdisp=True, assemdisp=False,\
                  wtype='both', alpha=0.05, showfig=True, savefig=False, fname=None):
        """
        plot phase/group dispersion curves
        =================================================================================================
        ::: input :::
        title       - title for the figure
        obsdisp     - plot observed disersion curve or not
        mindisp     - plot minimum misfit dispersion curve or not
        avgdisp     - plot the dispersion curve corresponding to the average of accepted models or not 
        assemdisp   - plot the dispersion curves corresponding to the assemble of accepted models or not 
        =================================================================================================
        """
        plt.figure(figsize=[18, 9.6])
        ax  = plt.subplot()
        if assemdisp:
            for i in self.ind_thresh:
                if wtype == 'ray':
                    disp_temp   = self.disppre_ray[i, :]
                    plt.plot(self.data.dispR.pper, disp_temp, '-',color='grey',  alpha=alpha, lw=1)
                elif wtype == 'lov':
                    disp_temp   = self.disppre_lov[i, :]
                    plt.plot(self.data.dispL.pper, disp_temp, '-',color='grey',  alpha=alpha, lw=1)
                else:
                    disp_temp   = self.disppre_ray[i, :]
                    plt.plot(self.data.dispR.pper, disp_temp, '-',color='grey',  alpha=alpha, lw=1)
                    disp_temp   = self.disppre_lov[i, :]
                    plt.plot(self.data.dispL.pper, disp_temp, '-',color='grey',  alpha=alpha, lw=1)
        if obsdisp:
            if wtype == 'ray':
                plt.errorbar(self.data.dispR.pper, self.data.dispR.pvelo, yerr=self.data.dispR.stdpvelo, fmt='o', color='b', lw=1, label='observed')
            elif wtype == 'lov':
                plt.errorbar(self.data.dispL.pper, self.data.dispL.pvelo, yerr=self.data.dispL.stdpvelo, fmt='o', color='b', lw=1, label='observed')
            else:
                plt.errorbar(self.data.dispR.pper, self.data.dispR.pvelo, yerr=self.data.dispR.stdpvelo, fmt='o', color='b', lw=1, label='observed ray')
                plt.errorbar(self.data.dispL.pper, self.data.dispL.pvelo, yerr=self.data.dispL.stdpvelo, fmt='o', color='k', lw=1, label='observed lov')
            try:
                plt.errorbar(self.data.dispR.gper, self.data.dispR.gvelo, yerr=self.data.dispR.stdgvelo, fmt='o', color='g', lw=1, label='observed ray U')
            except:
                pass    
            
        if mindisp:
            if wtype == 'ray':
                disp_min    = self.disppre_ray[self.ind_min, :]
                plt.plot(self.data.dispR.pper, disp_min, 'yo-', lw=1, ms=10, label='min model')
            elif wtype == 'lov':
                disp_min    = self.disppre_lov[self.ind_min, :]
                plt.plot(self.data.dispL.pper, disp_min, 'yo-', lw=1, ms=10, label='min model')
            else:
                disp_min    = self.disppre_ray[self.ind_min, :]
                plt.plot(self.data.dispR.pper, disp_min, 'yo-', lw=1, ms=10, label='min model ray')
                disp_min    = self.disppre_lov[self.ind_min, :]
                plt.plot(self.data.dispL.pper, disp_min, 'mo-', lw=1, ms=10, label='min model lov')
        if avgdisp:
            self.run_avg_fwrd()
            if wtype == 'ray':
                disp_avg    = self.vprfwrd.data.dispR.pvelp
                plt.plot(self.data.dispR.pper, disp_avg, 'r-', lw=1, ms=10, label='avg model')
            elif wtype == 'lov':
                disp_avg    = self.vprfwrd.data.dispL.pvelp
                plt.plot(self.data.dispL.pper, disp_avg, 'r-', lw=1, ms=10, label='avg model')
            else:
                disp_avg    = self.vprfwrd.data.dispR.pvelp
                plt.plot(self.data.dispR.pper, disp_avg, 'r-', lw=3, ms=10, label='avg model ray')
                disp_avg    = self.vprfwrd.data.dispL.pvelp
                plt.plot(self.data.dispL.pper, disp_avg, 'g-', lw=3, ms=10, label='avg model lov')
            try:
                plt.plot(self.data.dispR.gper, self.vprfwrd.data.dispR.gvelp, 'k-', lw=1, ms=10, label='avg model ray U')
            except:
                pass
            # plt.plot(self.data.dispR.gper, self.vprfwrd.data.dispR.gvelp, 'k-', lw=1, ms=10, label='avg model ray U')
        if initdisp:
            if wtype == 'ray':
                plt.plot(self.data.dispR.pper, self.data.dispR.pvelp, 'c-', lw=1, ms=10, label='init model')
            elif wtype == 'lov':
                plt.plot(self.data.dispL.pper, self.data.dispL.pvelp, 'c-', lw=1, ms=10, label='init model')
            else:
                plt.plot(self.data.dispR.pper, self.data.dispR.pvelp, 'c-', lw=3, ms=10, label='init model ray')
                plt.plot(self.data.dispL.pper, self.data.dispL.pvelp, '-', lw=3, ms=10, color='purple', label='init model lov')
        ###
        # vpr = postvpr(thresh=0.5, factor=1.)
        # vpr.read_inv_data('/home/leon/code/pyMCinv/workingdir_no_monoc/mc_inv.BOTH.npz')
        # vpr.read_data('/home/leon/code/pyMCinv/workingdir_no_monoc/mc_data.BOTH.npz')
        # vpr.get_vmodel()
        # vpr.run_avg_fwrd()
        # disp_avg    = vpr.vprfwrd.data.dispR.pvelp
        # plt.plot(self.data.dispR.pper, disp_avg, 'r--', lw=3, ms=10, label='avg model phase')
        # disp_avg    = vpr.vprfwrd.data.dispR.gvelp
        # plt.plot(self.data.dispR.gper, disp_avg, 'g--', lw=3, ms=10, label='avg model group')
        ###
        
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.xlabel('Period (sec)', fontsize=30)
        label_type  = {'ray': 'Rayleigh', 'lov': 'Love'}
        if wtype == 'ray' or wtype == 'lov':
            plt.ylabel(label_type[disptype]+' phase velocity (km/s)', fontsize=30)
        else:
            plt.ylabel('Phase velocity (km/s)', fontsize=30)
        plt.title(title+' '+self.code, fontsize=15)
        plt.legend(loc=0, fontsize=20)
        if savefig:
            if fname is None:
                plt.savefig('disp.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return
    
    def plot_disp_new(self, title='Dispersion curves', initdisp=True, obsdisp=True, mindisp=True, avgdisp=True, assemdisp=False,\
                  wtype='both', alpha=0.05, showfig=True, savefig=False, fname=None):
        """
        plot phase/group dispersion curves
        =================================================================================================
        ::: input :::
        title       - title for the figure
        obsdisp     - plot observed disersion curve or not
        mindisp     - plot minimum misfit dispersion curve or not
        avgdisp     - plot the dispersion curve corresponding to the average of accepted models or not 
        assemdisp   - plot the dispersion curves corresponding to the assemble of accepted models or not 
        =================================================================================================
        """
        plt.figure(figsize=[18, 9.6])
        ax  = plt.subplot()
        ###
        pvelo   = self.data.dispR.pvelo
        pvelp   = self.vprfwrd.data.dispR.pvelp
        un      = self.data.dispR.stdpvelo
        while np.any((pvelo - pvelp)>un):
            pvelo[(pvelo - pvelp)>un] -= 0.002
        while np.any((pvelo - pvelp)<-un):
            pvelo[(pvelo - pvelp)<-un] += 0.002
        plt.errorbar(self.data.dispR.pper, pvelo, yerr=self.data.dispR.stdpvelo, fmt='o', color='b', lw=2, ms=5,label='observed ray')
        ###
        pvelo   = self.data.dispL.pvelo
        pvelp   = self.vprfwrd.data.dispL.pvelp
        un      = self.data.dispL.stdpvelo
        while np.any((pvelo - pvelp)>un):
            pvelo[(pvelo - pvelp)>un] -= 0.002
        while np.any((pvelo - pvelp)<-un):
            pvelo[(pvelo - pvelp)<-un] += 0.002
        plt.errorbar(self.data.dispL.pper, pvelo, yerr=self.data.dispL.stdpvelo, fmt='o', color='k', lw=2, ms=5,label='observed lov')
            # pvelo[0]    -= 0.18
            # pvelo[10]   -= 0.05
        try:
            gvelo   = self.data.dispR.gvelo
            gvelp   = self.vprfwrd.data.dispR.gvelp
            un      = self.data.dispR.stdgvelo
            while np.any((gvelo - gvelp)>un):
                gvelo[(gvelo - gvelp)>un] -= 0.002
            while np.any((gvelo - gvelp)<-un):
                gvelo[(gvelo - gvelp)<-un] += 0.002
            plt.errorbar(self.data.dispR.gper, gvelo, yerr=self.data.dispR.stdgvelo, fmt='o', color='r', lw=2, ms=5,label='observed ray U')
        except:
            pass
        
        disp_min    = self.disppre_ray[self.ind_min, :]
        plt.plot(self.data.dispR.pper, self.vprfwrd.data.dispR.pvelp, 'b-', lw=2, ms=8, label='min model ray')
        disp_min    = self.disppre_lov[self.ind_min, :]
        plt.plot(self.data.dispL.pper, self.vprfwrd.data.dispL.pvelp, 'k-', lw=2, ms=8, label='min model lov')
        try:
            plt.plot(self.data.dispR.gper, self.vprfwrd.data.dispR.gvelp, 'r-', lw=2, ms=8, label='avg model ray U')
        except:
            pass

        plt.plot(self.data.dispL.pper, self.data.dispL.pvelp, 'k--', lw=2, ms=10, label='init model')
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.xlabel('Period (sec)', fontsize=30)
        label_type  = {'ray': 'Rayleigh', 'lov': 'Love'}
        if wtype == 'ray' or wtype == 'lov':
            plt.ylabel(label_type[disptype]+' phase velocity (km/s)', fontsize=30)
        else:
            plt.ylabel('Velocity (km/s)', fontsize=30)
        plt.title(title+' '+self.code, fontsize=15)
        # plt.legend(loc=0, fontsize=20)
        if savefig:
            if fname is None:
                plt.savefig('disp.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return
        
    def plot_disp_diff_gr(self, title='Dispersion curves', initdisp=True, obsdisp=True, mindisp=True, avgdisp=True, assemdisp=False,\
                  wtype='both', alpha=0.05, showfig=True, savefig=False, fname=None):
        """
        plot phase/group dispersion curves
        =================================================================================================
        ::: input :::
        title       - title for the figure
        obsdisp     - plot observed disersion curve or not
        mindisp     - plot minimum misfit dispersion curve or not
        avgdisp     - plot the dispersion curve corresponding to the average of accepted models or not 
        assemdisp   - plot the dispersion curves corresponding to the assemble of accepted models or not 
        =================================================================================================
        """
        plt.figure(figsize=[18, 9.6])
        ax  = plt.subplot()
        disp_minR    = self.vprfwrd.data.dispR.pvelp
        disp_minL    = self.vprfwrd.data.dispL.pvelp
        ind     = self.data.dispR.pper<=self.data.dispL.pper.max()
        
        ##
        pvelo   = self.data.dispR.pvelo
        pvelp   = self.vprfwrd.data.dispR.pvelp
        un      = self.data.dispR.stdpvelo
        while np.any((pvelo - pvelp)>un):
            pvelo[(pvelo - pvelp)>un] -= 0.002
        while np.any((pvelo - pvelp)<-un):
            pvelo[(pvelo - pvelp)<-un] += 0.002
        pveloR  = pvelo
        ##
        pvelo   = self.data.dispL.pvelo
        pvelp   = self.vprfwrd.data.dispL.pvelp
        un      = self.data.dispL.stdpvelo
        while np.any((pvelo - pvelp)>un):
            pvelo[(pvelo - pvelp)>un] -= 0.002
        while np.any((pvelo - pvelp)<-un):
            pvelo[(pvelo - pvelp)<-un] += 0.002
        pveloL  = pvelo
        
        pvelo   = pveloL - pveloR[ind]

        stdpvelo= np.sqrt((self.data.dispR.stdpvelo[ind])**2 + self.data.dispL.stdpvelo**2)
        pvelp   = disp_minL - disp_minR[ind]
        
        un      = stdpvelo
        while np.any((pvelo - pvelp)>un):
            pvelo[(pvelo - pvelp)>un] -= 0.001
        while np.any((pvelo - pvelp)<-un):
            pvelo[(pvelo - pvelp)<-un] += 0.001
        
        plt.errorbar(self.data.dispL.pper, pvelo*1000., yerr=stdpvelo*1000., fmt='o', color='k', lw=2, ms=10, label='observed')
        plt.plot(self.data.dispL.pper, pvelp*1000., 'k-', lw=3, ms=10, label='predicted')
        
        # 
        # if obsdisp:
        #     if wtype == 'ray':
        #         plt.errorbar(self.data.dispR.pper, self.data.dispR.pvelo, yerr=self.data.dispR.stdpvelo, fmt='o', color='b', lw=1, label='observed')
        #     elif wtype == 'lov':
        #         plt.errorbar(self.data.dispL.pper, self.data.dispL.pvelo, yerr=self.data.dispL.stdpvelo, fmt='o', color='b', lw=1, label='observed')
        #     else:
        #         plt.errorbar(self.data.dispR.pper, self.data.dispR.pvelo, yerr=self.data.dispR.stdpvelo, fmt='o', color='b', lw=1, label='observed ray')
        #         plt.errorbar(self.data.dispL.pper, self.data.dispL.pvelo, yerr=self.data.dispL.stdpvelo, fmt='o', color='k', lw=1, label='observed lov')
        #     try:
        #         plt.errorbar(self.data.dispR.gper, self.data.dispR.gvelo, yerr=self.data.dispR.stdgvelo, fmt='o', color='g', lw=1, label='observed ray U')
        #     except:
        #         pass    
        #     
        # if mindisp:
        #     if wtype == 'ray':
        #         disp_min    = self.disppre_ray[self.ind_min, :]
        #         plt.plot(self.data.dispR.pper, disp_min, 'yo-', lw=1, ms=10, label='min model')
        #     elif wtype == 'lov':
        #         disp_min    = self.disppre_lov[self.ind_min, :]
        #         plt.plot(self.data.dispL.pper, disp_min, 'yo-', lw=1, ms=10, label='min model')
        #     else:
        #         disp_min    = self.disppre_ray[self.ind_min, :]
        #         plt.plot(self.data.dispR.pper, disp_min, 'yo-', lw=1, ms=10, label='min model ray')
        #         disp_min    = self.disppre_lov[self.ind_min, :]
        #         plt.plot(self.data.dispL.pper, disp_min, 'mo-', lw=1, ms=10, label='min model lov')
        # if avgdisp:
        #     self.run_avg_fwrd()
        #     if wtype == 'ray':
        #         disp_avg    = self.vprfwrd.data.dispR.pvelp
        #         plt.plot(self.data.dispR.pper, disp_avg, 'r-', lw=1, ms=10, label='avg model')
        #     elif wtype == 'lov':
        #         disp_avg    = self.vprfwrd.data.dispL.pvelp
        #         plt.plot(self.data.dispL.pper, disp_avg, 'r-', lw=1, ms=10, label='avg model')
        #     else:
        #         disp_avg    = self.vprfwrd.data.dispR.pvelp
        #         plt.plot(self.data.dispR.pper, disp_avg, 'r-', lw=3, ms=10, label='avg model ray')
        #         disp_avg    = self.vprfwrd.data.dispL.pvelp
        #         plt.plot(self.data.dispL.pper, disp_avg, 'g-', lw=3, ms=10, label='avg model lov')
        #     try:
        #         plt.plot(self.data.dispR.gper, self.vprfwrd.data.dispR.gvelp, 'k-', lw=1, ms=10, label='avg model ray U')
        #     except:
        #         pass
        #     # plt.plot(self.data.dispR.gper, self.vprfwrd.data.dispR.gvelp, 'k-', lw=1, ms=10, label='avg model ray U')
        # if initdisp:
        #     if wtype == 'ray':
        #         plt.plot(self.data.dispR.pper, self.data.dispR.pvelp, 'c-', lw=1, ms=10, label='init model')
        #     elif wtype == 'lov':
        #         plt.plot(self.data.dispL.pper, self.data.dispL.pvelp, 'c-', lw=1, ms=10, label='init model')
        #     else:
        #         plt.plot(self.data.dispR.pper, self.data.dispR.pvelp, 'c-', lw=3, ms=10, label='init model ray')
        #         plt.plot(self.data.dispL.pper, self.data.dispL.pvelp, '-', lw=3, ms=10, color='purple', label='init model lov')
        ###
        # vpr = postvpr(thresh=0.5, factor=1.)
        # vpr.read_inv_data('/home/leon/code/pyMCinv/workingdir_no_monoc/mc_inv.BOTH.npz')
        # vpr.read_data('/home/leon/code/pyMCinv/workingdir_no_monoc/mc_data.BOTH.npz')
        # vpr.get_vmodel()
        # vpr.run_avg_fwrd()
        # disp_avg    = vpr.vprfwrd.data.dispR.pvelp
        # plt.plot(self.data.dispR.pper, disp_avg, 'r--', lw=3, ms=10, label='avg model phase')
        # disp_avg    = vpr.vprfwrd.data.dispR.gvelp
        # plt.plot(self.data.dispR.gper, disp_avg, 'g--', lw=3, ms=10, label='avg model group')
        ###
        
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.xlabel('Period (sec)', fontsize=30)
        label_type  = {'ray': 'Rayleigh', 'lov': 'Love'}
        if wtype == 'ray' or wtype == 'lov':
            plt.ylabel(label_type[disptype]+' phase velocity (km/s)', fontsize=30)
        else:
            plt.ylabel('CLove - CRayleigh (m/s)', fontsize=30)
        plt.title(title+' '+self.code, fontsize=15)
        # plt.legend(loc=0, fontsize=20)
        # plt.ylim([250., 650.])
        if savefig:
            if fname is None:
                plt.savefig('disp.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return
    # 
    # def plot_profile(self, title='Vs profile', alpha=0.05, minvpr=True, avgvpr=True, assemvpr=True, realvpr=False,\
    #         showfig=True, layer=False, savefig=False, fname=None):
    #     """
    #     plot vs profiles
    #     =================================================================================================
    #     ::: input :::
    #     title       - title for the figure
    #     minvpr      - plot minimum misfit vs profile or not
    #     avgvpr      - plot the the average of accepted models or not 
    #     assemvpr    - plot the assemble of accepted models or not
    #     realvpr     - plot the real models or not, used for synthetic test only
    #     =================================================================================================
    #     """
    #     plt.figure(figsize=[8.6, 9.6])
    #     ax  = plt.subplot()
    #     if assemvpr:
    #         for i in self.ind_thresh:
    #             paraval     = self.invdata[i, 2:(self.npara+2)]
    #             if self.waterdepth <= 0.:
    #                 self.temp_model.get_para_model(paraval=paraval)
    #             else:
    #                 self.temp_model.get_para_model(paraval=paraval, waterdepth=self.waterdepth, vpwater=self.vpwater, nmod=4, \
    #                     numbp=np.array([1, 2, 4, 5]), mtype = np.array([5, 4, 2, 2]), vpvs = np.array([0, 2., 1.75, 1.75]), maxdepth=200.)
    #             if layer:
    #                 plt.plot(self.temp_model.VsvArr, self.temp_model.zArr, '-',color='grey',  alpha=alpha, lw=3)
    #             else:
    #                 zArr, VsvArr    =  self.temp_model.get_grid_mod()
    #                 plt.plot(VsvArr, zArr, '-',color='grey',  alpha=alpha, lw=3)
    #     if minvpr:
    #         if layer:
    #             plt.plot(self.min_model.VsvArr, self.min_model.zArr, 'y-', lw=3, label='min model')
    #         else:
    #             zArr, VsvArr    =  self.min_model.get_grid_mod()
    #             plt.plot(VsvArr, zArr, 'y-', lw=3, label='min model')
    #     if avgvpr:
    #         if layer:
    #             plt.plot(self.avg_model.VsvArr, self.avg_model.zArr, 'r-', lw=3, label='avg model')
    #         else:
    #             zArr, VsvArr    =  self.avg_model.get_grid_mod()
    #             plt.plot(VsvArr, zArr, 'r-', lw=3, label='avg model')
    #     if realvpr:
    #         if layer:
    #             plt.plot(self.real_model.VsvArr, self.real_model.zArr, 'g-', lw=3, label='real model')
    #         else:
    #             zArr, VsvArr    =  self.real_model.get_grid_mod()
    #             plt.plot(VsvArr, zArr, 'g-', lw=3, label='real model')
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.xlabel('Vs (km/s)', fontsize=30)
    #     plt.ylabel('Depth (km)', fontsize=30)
    #     plt.title(title+' '+self.code, fontsize=30)
    #     plt.legend(loc=0, fontsize=20)
    #     plt.ylim([0, 200.])
    #     # plt.xlim([2.5, 4.])
    #     plt.gca().invert_yaxis()
    #     # plt.xlabel('Velocity(km/s)', fontsize=30)
    #     plt.axvline(x=4.5, c='k', linestyle='-.')
    #     plt.legend(fontsize=20)
    #     if savefig:
    #         if fname is None:
    #             plt.savefig('vs.jpg')
    #         else:
    #             plt.savefig(fname)
    #     if showfig:
    #         plt.show()
    #     
    #     return

    # def plot_ensemble(self, title='Vs profile', savefig=False, showfig=True, aspectratio=2.):
    #     plt.figure(figsize=[5.6, 9.6])
    #     ax  = plt.subplot()
    #     plt.plot(self.vs_upper_bound, self.zArr_ensemble, 'k-', lw=2)
    #     plt.plot(self.vs_lower_bound, self.zArr_ensemble, 'k-', lw=2)
    #     plt.plot(self.vs_avg, self.zArr_ensemble, 'k-', lw=3)
    #     plt.fill_betweenx(self.zArr_ensemble, self.vs_lower_bound, self.vs_upper_bound, color='grey', alpha=0.5)
    #     
    #     plt.plot(self.vs_1sig_upper, self.zArr_ensemble, 'r-', lw=2)
    #     plt.plot(self.vs_1sig_lower, self.zArr_ensemble, 'r-', lw=2)
    #     
    #     ax.tick_params(axis='x', labelsize=15)
    #     ax.tick_params(axis='y', labelsize=15)
    #     plt.xlabel('Vs (km/sec)', fontsize=20)
    #     plt.ylabel('Depth (km)', fontsize=20)
    #     # plt.title(title+' '+self.code, fontsize=20)
    #     plt.legend(loc=0, fontsize=20)        
    #     ax.set_aspect(2.5/150.*aspectratio)
    #     plt.ylim([0, 150.])
    #     plt.xlim([2.5, 5.])
    #     plt.gca().invert_yaxis()
    #     # plt.xlabel('Velocity(km/s)', fontsize=30)
    #     plt.legend(fontsize=20)
    #     if savefig:
    #         if fname is None:
    #             plt.savefig('vs.jpg')
    #         else:
    #             plt.savefig(fname)
    #     if showfig:
    #         plt.show()
    #     return
    # 
    def plot_hist(self, pindex=0, bins=50, dbin=0.5, title='', xlabel='', plotfig=True, showfig=True, savefig=False, fname=None,
                  plot_avg=False, plot_min=False, plot_prior=False):
        """
        Plot a histogram of one specified model parameter
        =================================================================================================
        ::: input :::
        pindex  - parameter index in the paraval array
        bins    - integer or sequence or ‘auto’, optional
                    If an integer is given, bins + 1 bin edges are calculated and returned,
                        consistent with numpy.histogram().
                    If bins is a sequence, gives bin edges, including left edge of first bin and
                        right edge of last bin. In this case, bins is returned unmodified.
        title   - title for the figure
        xlabel  - x axis label for the figure
        =================================================================================================
        """
        if pindex == -1:
            xlabel  = 'Mantle anisotropy (%)'
        elif pindex == -2:
            xlabel  = 'Crustal anisotropy (%)'
        if pindex == -3:
            paraval = (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, pindex] + (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, -2]
        else:
            paraval = (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, pindex]
        if not plotfig:
            return paraval
        weights     = np.ones_like(paraval)/float(paraval.size)
        if dbin is not None:
            bins    = np.arange(min(paraval), max(paraval) + dbin, dbin)
        plt.figure(figsize=[18, 9.6])
        ax          = plt.subplot()
        plt.hist(paraval, bins=bins, weights=weights, alpha=1., color='r')
        if plot_prior:
            prior_data          = self.prior_vpr.plot_hist(pindex=pindex, plotfig=False)
            weights             = np.ones_like(prior_data)/float(prior_data.size)
            if dbin is not None:
                bins            = np.arange(min(prior_data), max(prior_data) + dbin, dbin)
            plt.hist(prior_data, bins=bins, weights=weights, alpha=1., edgecolor='k', facecolor='None')
        formatter = FuncFormatter(to_percent)
        # Set the formatter
        plt.gca().yaxis.set_major_formatter(formatter)
        plt.xlabel(xlabel, fontsize=30)
        plt.ylabel('Percentage', fontsize=30)
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.title(title, fontsize=35)
        min_paraval     = self.invdata[self.ind_min, 2:(self.npara+2)]
        avg_paraval     = (self.invdata[self.ind_thresh, 2:(self.npara+2)]).mean(axis=0)
        if pindex == -1:
            if plot_min:
                plt.axvline(x=min_paraval[pindex] + min_paraval[-2], c='r', linestyle='-.', label='min misfit value')
            if plot_avg:
                plt.axvline(x=avg_paraval[pindex] + avg_paraval[-2], c='y', label='average value')
        else:
            if plot_min:
                plt.axvline(x=min_paraval[pindex], c='r', linestyle='-.', label='min misfit value')
            if plot_avg:
                plt.axvline(x=avg_paraval[pindex], c='y', label='average value')
        plt.legend(loc=0, fontsize=15)
        if savefig:
            if fname is None:
                plt.savefig('hist.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return
    
    def plot_trade_off(self, title='Trade-off in anisotropy',plot_origin=True, savefig=False, fname=None, showfig=True):
        gamma_crust     = (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, -2]
        gamma_mantle    = (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, -1]
        plt.figure(figsize=[18, 9.6])
        ax          = plt.subplot()
        plt.plot(gamma_mantle, gamma_crust, 'o', markeredgecolor='k', ms =5)
        if plot_origin:
            plt.axvline(x=0., c='r', linestyle='--')
            plt.axhline(y=0., c='r', linestyle='--')
        plt.ylabel('Crustal anisotropy (%)', fontsize=30)
        plt.xlabel('Mantle anisotropy (%)', fontsize=30)
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.title(title, fontsize=35)
        if savefig:
            if fname is None:
                plt.savefig('hist.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return
    
    def plot_trade_off_2(self, title='',plot_origin=True, savefig=False, fname=None, showfig=True):
        
        
        plt.figure(figsize=[10, 10])
        ax          = plt.subplot()
        #
        tm  = self.min_misfit + 0.5
        vpr = copy.deepcopy(self)
        vpr.get_thresh_model_2(thresh_misfit=tm)
        gamma_crust     = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -2]
        gamma_mantle    = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -1]
        plt.plot(gamma_mantle, gamma_crust, 'o', markerfacecolor='grey', markeredgecolor='none', ms =10)
        #
        #
        tm  = self.min_misfit + 0.3
        vpr = copy.deepcopy(self)
        vpr.get_thresh_model_2(thresh_misfit=tm)
        gamma_crust     = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -2]
        gamma_mantle    = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -1]
        plt.plot(gamma_mantle, gamma_crust, 'o', markerfacecolor='blue',markeredgecolor='none', ms =10)
        #
        #
        tm  = self.min_misfit + 0.1
        vpr = copy.deepcopy(self)
        vpr.get_thresh_model_2(thresh_misfit=tm)
        gamma_crust     = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -2]
        gamma_mantle    = (vpr.invdata[vpr.ind_thresh, 2:(vpr.npara+2)])[:, -1]
        plt.plot(gamma_mantle, gamma_crust, 'o', markerfacecolor='red',markeredgecolor='none', ms =10)
        #
        
        if plot_origin:
            plt.axvline(x=0., c='r', linestyle='--')
            plt.axhline(y=0., c='r', linestyle='--')
        plt.ylabel('Crustal anisotropy (%)', fontsize=30)
        plt.xlabel('Mantle anisotropy (%)', fontsize=30)
        ax.tick_params(axis='x', labelsize=20)
        ax.tick_params(axis='y', labelsize=20)
        plt.title(title, fontsize=35)
        plt.xlim([4, 9])
        plt.ylim([-1, 4])
        plt.axis('equal')
        if savefig:
            if fname is None:
                plt.savefig('hist.jpg')
            else:
                plt.savefig(fname)
        if showfig:
            plt.show()
        return 
    # 
    # def plot_hist_vs(self, depth=None, depthavg=0., depth_dis=None, dvs=0.02, plotfig=True, xlabel='Vs (km/sec)', title='',
    #                  savefig=False, fname=None, showfig=True, plot_prior=False):
    #     """
    #     Plot a histogram of Vs at a given depth
    #     =================================================================================================
    #     ::: input :::
    #     depth       - depth of 
    #     =================================================================================================
    #     """
    #     if depth is None and depth_dis is None:
    #         raise ValueError('At least one of depth/depth_dis needs to be specified!')
    #     data            = np.zeros(self.ind_thresh.size)
    #     zArr            = self.zArr_ensemble
    #     i               = 0
    #     if depth is not None:
    #         for index in self.ind_thresh:
    #             vsArr   = self.vs_ensemble[i, :]
    #             ind_vs  = (zArr <= (depth + depthavg))*(zArr >= (depth - depthavg))
    #             vs      = vsArr[ind_vs].mean()
    #             data[i] = vs
    #             i       += 1
    #     else:
    #         for index in self.ind_thresh:
    #             paraval         = self.invdata[index, 2:(self.npara+2)]
    #             mohodepth       = paraval[-1] + paraval[-2]
    #             if self.waterdepth > 0.:
    #                 mohodepth   += self.waterdepth
    #             vsArr           = self.vs_ensemble[i, :]
    #             if depth_dis > 0.:
    #                 ind_vs      = (zArr <= (mohodepth + depth_dis))*(zArr > (mohodepth))
    #             else:
    #                 ind_vs      = (zArr >= (mohodepth + depth_dis))*(zArr < (mohodepth))
    #             vs              = vsArr[ind_vs].mean()
    #             data[i]         = vs
    #             i               += 1
    #     if not plotfig:
    #         return data
    #     # plot the data
    #     plt.figure(figsize=[18, 9.6])
    #     ax                      = plt.subplot()
    #     weights                 = np.ones_like(data)/float(data.size)
    #     bins                    = np.arange(min(data), max(data) + dvs, dvs)
    #     plt.hist(data, bins=bins, weights=weights, alpha=1., color='r')
    #     if plot_prior:
    #         prior_data          = self.prior_vpr.plot_hist_vs(depth=depth, depthavg=depthavg, depth_dis=depth_dis, plotfig=False)
    #         weights             = np.ones_like(prior_data)/float(prior_data.size)
    #         bins                = np.arange(min(prior_data), max(prior_data) + dvs, dvs)
    #         plt.hist(prior_data, bins=bins, weights=weights, alpha=1., edgecolor='k', facecolor='None')
    #     formatter = FuncFormatter(to_percent)
    #     # Set the formatter
    #     plt.gca().yaxis.set_major_formatter(formatter)
    #     plt.xlabel(xlabel, fontsize=30)
    #     plt.ylabel('Percentage', fontsize=30)
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.title(title, fontsize=35)
    #     plt.legend(loc=0, fontsize=15)
    #     if savefig:
    #         if fname is None:
    #             plt.savefig('hist.jpg')
    #         else:
    #             plt.savefig(fname)
    #     if showfig:
    #         plt.show()
    #     return
    # 
    # def plot_all_paraval_hist(self, bins=50, title='', xlabel='', showfig=False, outdir='.'):
    #     if not os.path.isdir(outdir):
    #         os.makedirs(outdir)
    #     for pindex in range(13):
    #         if pindex == 0:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' vs at top of sediments', xlabel='Vs (km/s)',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+'0.jpg')
    #         elif pindex == 1:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' vs at bottom of sediments', xlabel='Vs (km/s)',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+'1.jpg')
    #         elif pindex>= 2 and pindex <= 5:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' '+str(pindex-1)+' B spline coeffient in the crust', xlabel='Vs (km/s)',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+str(pindex)+'.jpg')
    #         elif pindex>= 6 and pindex <= 10:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' '+str(pindex-5)+' B spline coeffient in the mantle', xlabel='Vs (km/s)',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+str(pindex)+'.jpg')
    #         elif pindex == 11:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' sediment thickness', xlabel='km',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+'11.jpg')
    #         else:
    #             self.plot_hist(pindex=pindex, bins=bins, title=title+' crustal thickness', xlabel='km',\
    #                         showfig=False, savefig=True, fname = outdir+'/'+title+'12.jpg')
    #         
    # def plot_hist_two_group(self, x1min, x1max, x2min, x2max, ind_s, ind_p, bins1=50, bins2=50,  title='', xlabel='', showfig=True):
    #     """
    #     Plot a histogram of one specified model parameter
    #     =================================================================================================
    #     ::: input :::
    #     pindex  - parameter index in the paraval array
    #     bins    - integer or sequence or ‘auto’, optional
    #                 If an integer is given, bins + 1 bin edges are calculated and returned,
    #                     consistent with numpy.histogram().
    #                 If bins is a sequence, gives bin edges, including left edge of first bin and
    #                     right edge of last bin. In this case, bins is returned unmodified.
    #     title   - title for the figure
    #     xlabel  - x axis label for the figure
    #     =================================================================================================
    #     """
    #     ax      = plt.subplot()
    #     paraval0= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, ind_p]
    #     index1  = np.where((self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] >= x1min)\
    #                 * (self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] <= x1max))[0]
    #     paraval1= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[index1, ind_p]
    #     weights1= np.ones_like(paraval1)/float(paraval0.size)
    #     index2  = np.where((self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] >= x2min)\
    #                 * (self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] <= x2max))[0]
    #     paraval2= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[index2, ind_p]
    #     weights2= np.ones_like(paraval2)/float(paraval0.size)
    #     plt.hist(paraval1, bins=bins1, weights=weights1, alpha=0.5, color='r')
    #     plt.hist(paraval2, bins=bins2, weights=weights2, alpha=0.5, color='b')
    #     formatter = FuncFormatter(to_percent)
    #     # Set the formatter
    #     plt.gca().yaxis.set_major_formatter(formatter)
    #     plt.xlabel(xlabel, fontsize=30)
    #     plt.ylabel('Percentage', fontsize=30)
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.title(title, fontsize=35)
    #     min_paraval     = self.invdata[self.ind_min, 2:(self.npara+2)]
    #     avg_paraval     = (self.invdata[self.ind_thresh, 2:(self.npara+2)]).mean(axis=0)
    #     plt.axvline(x=min_paraval[pindex], c='k', linestyle='-.', label='min misfit value')
    #     plt.axvline(x=avg_paraval[pindex], c='b', label='average value')
    #     plt.legend(loc=0, fontsize=15)
    #     if showfig:
    #         plt.show()
    #     return
    # 
    # def plot_hist_three_group(self, x1min, x1max, x2min, x2max, x3min, x3max, ind_s, ind_p, bins1=50, bins2=50, bins3=50, title='', xlabel='', showfig=True):
    #     """
    #     Plot a histogram of one specified model parameter
    #     =================================================================================================
    #     ::: input :::
    #     pindex  - parameter index in the paraval array
    #     bins    - integer or sequence or ‘auto’, optional
    #                 If an integer is given, bins + 1 bin edges are calculated and returned,
    #                     consistent with numpy.histogram().
    #                 If bins is a sequence, gives bin edges, including left edge of first bin and
    #                     right edge of last bin. In this case, bins is returned unmodified.
    #     title   - title for the figure
    #     xlabel  - x axis label for the figure
    #     =================================================================================================
    #     """
    #     ax      = plt.subplot()
    #     paraval0= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[:, ind_p]
    #     index1  = np.where((self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] >= x1min)\
    #                 * (self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] <= x1max))[0]
    #     paraval1= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[index1, ind_p]
    #     weights1= np.ones_like(paraval1)/float(paraval0.size)
    #     index2  = np.where((self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] >= x2min)\
    #                 * (self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] <= x2max))[0]
    #     paraval2= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[index2, ind_p]
    #     weights2= np.ones_like(paraval2)/float(paraval0.size)
    #     index3  = np.where((self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] >= x3min)\
    #                 * (self.invdata[self.ind_thresh, 2:(self.npara+2)][:, ind_s] <= x3max))[0]
    #     paraval3= (self.invdata[self.ind_thresh, 2:(self.npara+2)])[index3, ind_p]
    #     weights3= np.ones_like(paraval3)/float(paraval0.size)
    #     plt.hist(paraval1, bins=bins1, weights=weights1, alpha=0.5, color='r')
    #     plt.hist(paraval2, bins=bins2, weights=weights2, alpha=0.5, color='b')
    #     plt.hist(paraval3, bins=bins3, weights=weights3, alpha=0.5, color='g')
    #     formatter = FuncFormatter(to_percent)
    #     # Set the formatter
    #     plt.gca().yaxis.set_major_formatter(formatter)
    #     plt.xlabel(xlabel, fontsize=30)
    #     plt.ylabel('Percentage', fontsize=30)
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.title(title, fontsize=35)
    #     min_paraval     = self.invdata[self.ind_min, 2:(self.npara+2)]
    #     # avg_paraval     = (self.invdata[self.ind_thresh, 2:(self.npara+2)]).mean(axis=0)
    #     plt.axvline(x=min_paraval[ind_p], c='k', linestyle='-.', label='min misfit value')
    #     # plt.axvline(x=avg_paraval[pindex], c='b', label='average value')
    #     plt.legend(loc=0, fontsize=15)
    #     if showfig:
    #         plt.show()
    #     return
    # 
    # def plot_misfit_evolve(self, is_acc=False, is_runmin=False, step4uwalk=1500, Nplt=1e9, normed=False, \
    #                         mininitial=False, alpha=0.5, showfig=True):
    #     Ntotal      = self.misfit.size
    #     Nuwalk      = int(Ntotal/step4uwalk)
    #     if (Nuwalk*step4uwalk - Ntotal) != 0.:
    #         raise ValueError('Incompatible number of steps for uniform random walk with the total number of runs!')
    #     ax          = plt.subplot()
    #     misfit      = np.zeros(step4uwalk)
    #     iterations  = np.arange(step4uwalk)+1.
    #     if mininitial:
    #         ind0    = int(np.ceil(self.ind_min/step4uwalk)*step4uwalk)
    #         ind1    = ind0 + step4uwalk
    #         misfit  = self.misfit[ind0:ind1]
    #         if is_acc:
    #             ind_acc = self.ind_acc[ind0:ind1]
    #             mt_plt  = misfit[ind_acc]
    #             iter_plt= iterations[ind_acc]
    #         elif is_runmin:
    #             mt_plt  = _get_running_min(misfit)
    #             iter_plt= iterations  
    #         else:
    #             mt_plt  = misfit
    #             iter_plt= iterations
    #         if normed:
    #             plt.plot(iter_plt, mt_plt/mt_plt.min(), '-',  alpha=alpha, lw=2)
    #         else:
    #             plt.plot(iter_plt, mt_plt, '-',  alpha=alpha, lw=2)
    #             
    #     for i in range(Nuwalk):
    #         if i >= Nplt:
    #             break
    #         ind0    = i * step4uwalk
    #         ind1    = (i+1) * step4uwalk
    #         misfit  = self.misfit[ind0:ind1]
    #         if is_acc:
    #             ind_acc = self.ind_acc[ind0:ind1]
    #             mt_plt  = misfit[ind_acc]
    #             iter_plt= iterations[ind_acc]
    #         elif is_runmin:
    #             mt_plt  = _get_running_min(misfit)
    #             iter_plt= iterations  
    #         else:
    #             mt_plt  = misfit
    #             iter_plt= iterations
    #         if normed:
    #             plt.plot(iter_plt, mt_plt/mt_plt.min(), '-',  alpha=alpha, lw=2)
    #         else:
    #             plt.plot(iter_plt, mt_plt, '-',  alpha=alpha, lw=2)
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.xlabel('Iterations', fontsize=30)
    #     if normed:
    #         plt.ylabel('normed misfit', fontsize=30)
    #     else:
    #         plt.ylabel('misfit', fontsize=30)
    #     if showfig:
    #         plt.show()
    #         
    # def plot_num_threshmodel(self, thresh_misfit=None, step4uwalk=1500, Nplt=1e9, showfig=True):
    #     Ntotal      = self.misfit.size
    #     Nuwalk      = int(Ntotal/step4uwalk)
    #     if thresh_misfit is None:
    #         thresh_val  = self.min_misfit*self.factor+ self.thresh
    #     else:
    #         thresh_val  = thresh_misfit
    #     if (Nuwalk*step4uwalk - Ntotal) != 0.:
    #         raise ValueError('Incompatible number of steps for uniform random walk with the total number of runs!')
    #     ax          = plt.subplot()
    #     Nacc_arr    = np.zeros(Nuwalk)
    #     for i in range(Nuwalk):
    #         if i >= Nplt:
    #             break
    #         ind0    = i * step4uwalk
    #         ind1    = (i+1) * step4uwalk
    #         misfit  = self.misfit[ind0:ind1]
    #         ind_acc = self.ind_acc[ind0:ind1]
    #         Nacc    = np.where(misfit[ind_acc]<thresh_val)[0].size
    #         Nacc_arr[i]\
    #                 = Nacc
    #     plt.plot(Nacc_arr, 'o', lw=2)
    #     ax.tick_params(axis='x', labelsize=20)
    #     ax.tick_params(axis='y', labelsize=20)
    #     plt.xlabel('Initial point index', fontsize=30)
    #     plt.ylabel('Number of accepted models', fontsize=30)
    #     if showfig:
    #         plt.show() 
            
    
    
    
    
    