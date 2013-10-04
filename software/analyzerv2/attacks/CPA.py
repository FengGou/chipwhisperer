#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2013, Colin O'Flynn <coflynn@newae.com>
# All rights reserved.
#
# Find this and more at newae.com - this file is part of the chipwhisperer
# project, http://www.assembla.com/spaces/chipwhisperer
#
#    This file is part of chipwhisperer.
#
#    chipwhisperer is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    chipwhisperer is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with chipwhisperer.  If not, see <http://www.gnu.org/licenses/>.
#=================================================


import sys

try:
    from PySide.QtCore import *
    from PySide.QtGui import *
except ImportError:
    print "ERROR: PySide is required for this program"
    sys.exit()
    
from subprocess import Popen, PIPE
sys.path.append('../common')
sys.path.append('../../openadc/controlsw/python/common')
sys.path.append('../common/traces')
imagePath = '../common/images/'

import numpy as np
import scipy as sp
from ExtendedParameter import ExtendedParameter

from joblib import Parallel, delayed


try:
    import pyqtgraph as pg
    import pyqtgraph.multiprocess as mp
    import pyqtgraph.parametertree.parameterTypes as pTypes
    from pyqtgraph.parametertree import Parameter, ParameterTree, ParameterItem, registerParameterType
    #print pg.systemInfo()    
except ImportError:
    print "ERROR: PyQtGraph is required for this program"
    sys.exit()

import attacks.models.AES128_8bit
import attacks.models.AES_RoundKeys
from attacks.AttackBaseClass import AttackBaseClass

from functools import partial

class CPAMemoryOneSubkey(object):
    def __init__(self):
        self.sumhq = [0]*256
        self.sumtq = [0]*256
        self.sumt = [0]*256
        self.sumh = [0]*256
        self.sumht = [0]*256
        self.totalTraces = 0
    
    def oneSubkey(self, bnum, pointRange, traces_all, numtraces, plaintexts, ciphertexts, keyround, modeltype, progressBar, model, pbcnt):
    
        diffs = [0]*256
        self.totalTraces += numtraces
    
        if pointRange == None:
            traces = traces_all
            padbefore = 0
            padafter = 0
        else:
            traces = np.array(traces_all[:, pointRange[bnum][0] : pointRange[bnum][1]])
            padbefore = pointRange[bnum][0]
            padafter = len(traces_all[0,:]) - pointRange[bnum][1]
            #print "%d - %d (%d %d)"%( pointRange[bnum][0],  pointRange[bnum][1], padbefore, padafter)
    
        #For each 0..0xFF possible value of the key byte
        for key in range(0, 256):                
            #Initialize arrays & variables to zero
            sumnum = np.zeros(len(traces[0,:]))
            sumden1 = np.zeros(len(traces[0,:]))
            sumden2 = np.zeros(len(traces[0,:]))
    
            hyp = [0] * numtraces
    
            #Formula for CPA & description found in "Power Analysis Attacks"
            # by Mangard et al, page 124, formula 6.2.     
            #
            # This has been modified to reduce computational requirements such that adding a new waveform
            # doesn't require you to recalculate everything
            
            #Generate hypotheticals
            for tnum in range(numtraces):
    
                if len(plaintexts) > 0:
                    pt = plaintexts[tnum]
    
                if len(ciphertexts) > 0:
                    ct = ciphertexts[tnum]
    
                if keyround == "first":
                    ct = None
                elif keyround == "last":
                    pt = None
                else:
                    raise ValueError("keyround invalid")
                
                #Generate the output of the SBOX
                if modeltype == "Hamming Weight":
                    hypint = model.HypHW(pt, ct, key, bnum);
                elif modeltype == "Hamming Distance":
                    hypint = model.HypHD(pt, ct, key, bnum);
                else:
                    raise ValueError("modeltype invalid")
                hyp[tnum] = model.getHW(hypint)
                
            hyp = np.array(hyp)                                
                
            self.sumt[key] += np.sum(traces, axis=0)
            self.sumh[key] += np.sum(hyp, axis=0)
            self.sumht[key] += np.sum(np.multiply(np.transpose(traces), hyp), axis=1)
    
            #WARNING: not casting to np.float64 causes algorithm degredation... always be careful
            #meanh = self.sumh[key] / np.float64(self.totalTraces)
            #meant = self.sumt[key] / np.float64(self.totalTraces)
    
            #numtraces * meanh * meant = sumh * meant
            #sumnum =  self.sumht[key] - meant*self.sumh[key] - meanh*self.sumt[key] + (self.sumh[key] * meant)
            #sumnum =  self.sumht[key] - meanh*self.sumt[key]
#            sumnum =  self.sumht[key] - meanh*self.sumt[key]
            #sumnum =  self.sumht[key] - self.sumh[key]*self.sumt[key] / np.float64(self.totalTraces)
            sumnum = self.totalTraces*self.sumht[key] - self.sumh[key]*self.sumt[key]
    
            self.sumhq[key] += np.sum(np.square(hyp),axis=0, dtype=np.float64)
            self.sumtq[key] += np.sum(np.square(traces),axis=0, dtype=np.float64)
    
            #numtraces * meanh * meanh = sumh * meanh
            #sumden1 = sumhq - (2*meanh*self.sumh) + (numtraces*meanh*meanh)
            #sumden1 = sumhq - (2*meanh*self.sumh) + (self.sumh * meanh)
            #sumden1 = sumhq - meanh*self.sumh    
            #similarly for sumden2     
            #sumden1 = self.sumhq[key] - meanh*self.sumh[key]
            #sumden2 = self.sumtq[key] - meant*self.sumt[key]
            #sumden = sumden1 * sumden2    

            sumden1 = (np.square(self.sumh[key]) - self.totalTraces * self.sumhq[key])
            sumden2 = (np.square(self.sumt[key]) - self.totalTraces * self.sumtq[key])
            sumden = sumden1 * sumden2

    
            if progressBar:
                progressBar.setValue(pbcnt)
                #progressBar.setLabelText("Byte %02d: Hyp=%02x"%(bnum, key))
                pbcnt = pbcnt + 1
                if progressBar.wasCanceled():
                    break
    
            diffs[key] = sumnum / np.sqrt(sumden)
    
            if padafter > 0:
                diffs[key] = np.concatenate([diffs[key], np.zeros(padafter)])
    
            if padbefore > 0:
                diffs[key] = np.concatenate([np.zeros(padbefore), diffs[key]])                    
        
        return (diffs, pbcnt)

def CPASimpleOneSubkey(bnum, pointRange, traces_all, numtraces, plaintexts, ciphertexts, keyround, modeltype, progressBar, model, pbcnt):
     #For all bytes of key

    diffs = [0]*256

    if pointRange == None:
        traces = traces_all
        padbefore = 0
        padafter = 0
    else:
        traces = np.array(traces_all[:, pointRange[bnum][0] : pointRange[bnum][1]])
        padbefore = pointRange[bnum][0]
        padafter = len(traces_all[0,:]) - pointRange[bnum][1]
        #print "%d - %d (%d %d)"%( pointRange[bnum][0],  pointRange[bnum][1], padbefore, padafter)

    #For each 0..0xFF possible value of the key byte
    for key in range(0, 256):                
        #Initialize arrays & variables to zero
        sumnum = np.zeros(len(traces[0,:]))
        sumden1 = np.zeros(len(traces[0,:]))
        sumden2 = np.zeros(len(traces[0,:]))

        hyp = [0] * numtraces

        #Formula for CPA & description found in "Power Analysis Attacks"
        # by Mangard et al, page 124, formula 6.2.                         
        
        #Generate hypotheticals
        for tnum in range(numtraces):

            if len(plaintexts) > 0:
                pt = plaintexts[tnum]

            if len(ciphertexts) > 0:
                ct = ciphertexts[tnum]

            if keyround == "first":
                ct = None
            elif keyround == "last":
                pt = None
            else:
                raise ValueError("keyround invalid")
            
            #Generate the output of the SBOX
            if modeltype == "Hamming Weight":
                hypint = model.HypHW(pt, ct, key, bnum);
            elif modeltype == "Hamming Distance":
                hypint = model.HypHD(pt, ct, key, bnum);
            else:
                raise ValueError("modeltype invalid")
            hyp[tnum] = model.getHW(hypint)
            
        hyp = np.array(hyp)                    

        #Mean of hypothesis
        meanh = np.mean(hyp, dtype=np.float64)

        #Mean of all points in trace
        meant = np.mean(traces, axis=0, dtype=np.float64)
                           
        #For each trace, do the following
        for tnum in range(numtraces):
            hdiff = (hyp[tnum] - meanh)
            tdiff = traces[tnum,:] - meant

            sumnum = sumnum + (hdiff*tdiff)
            sumden1 = sumden1 + hdiff*hdiff 
            sumden2 = sumden2 + tdiff*tdiff             

        if progressBar:
            progressBar.setValue(pbcnt)
            #progressBar.setLabelText("Byte %02d: Hyp=%02x"%(bnum, key))
            pbcnt = pbcnt + 1
            if progressBar.wasCanceled():
                break

        diffs[key] = sumnum / np.sqrt( sumden1 * sumden2 )

        if padafter > 0:
            diffs[key] = np.concatenate([diffs[key], np.zeros(padafter)])

        if padbefore > 0:
            diffs[key] = np.concatenate([np.zeros(padbefore), diffs[key]])                    
    
    return (diffs, pbcnt)



class AttackCPA_SimpleLoop(QObject):
    """
    CPA Attack done as a loop - the 'classic' attack provided for familiarity to textbook samples.
    This attack does not provide trace-by-trace statistics however, you can only gather results once
    all the traces have been run through the attack.
    """

    def __init__(self, model):
        super(AttackCPA_SimpleLoop, self).__init__()
        self.model = model

    def setByteList(self, brange):
        self.brange = brange

    def setKeyround(self, keyround):
        self.keyround = keyround
    
    def setModeltype(self, modeltype):
        self.modeltype = modeltype
        
        #From all the key candidates, select the largest difference as most likely
        #foundbyte = diffs.index(max(diffs))
        #foundkey.append(foundbyte)
#            print "%2x "%foundbyte,
    
    def addTraces(self, traces, plaintexts, ciphertexts, progressBar=None, pointRange=None):
        keyround=self.keyround
        modeltype=self.modeltype
        brange=self.brange
                                                                   
        traces_all = np.array(traces)
        plaintexts =np.array(plaintexts)
        ciphertexts =np.array(ciphertexts)

        foundkey = []

        self.all_diffs = range(0,16)

        if progressBar:
            pbcnt = 0
            progressBar.setMinimum(0)
            progressBar.setMaximum(len(brange) * 256)
            progressBar.setWindowFlags(Qt.WindowStaysOnTopHint)

        numtraces = len(traces_all[:,0])

        pbcnt = 0
        #r = Parallel(n_jobs=4)(delayed(traceOneSubkey)(bnum, pointRange, traces_all, numtraces, plaintexts, ciphertexts, keyround, modeltype, progressBar, self.model, pbcnt) for bnum in brange)
        #self.all_diffs, pb = zip(*r)
        #pbcnt = 0
        for bnum in brange:
            #CPAMemoryOneSubkey
            #CPASimpleOneSubkey
            #(self.all_diffs[bnum], pbcnt) = sCPAMemoryOneSubkey(bnum, pointRange, traces_all, numtraces, plaintexts, ciphertexts, keyround, modeltype, progressBar, self.model, pbcnt)
                        
            cpa = CPAMemoryOneSubkey()
            
            tdiff = 10
            
            tstart = 0
            tend = tdiff
            
            while tstart < numtraces:                                       
                (self.all_diffs[bnum], pbcnt) = cpa.oneSubkey(bnum, pointRange, traces_all[tstart:tend], tend-tstart, plaintexts[tstart:tend], ciphertexts[tstart:tend], keyround, modeltype, progressBar, self.model, pbcnt)
                tend += tdiff
                tstart += tdiff
                
                if tend > numtraces:
                    tend = numtraces
                    
                if tstart > numtraces:
                    tstart = numtraces

    def getDiff(self, bnum, hyprange=None):
        if hyprange == None:
            hyprange = range(0,256)
        return [self.all_diffs[bnum][i] for i in hyprange];
    
    def getStatistics(self):
        t = [0]*16
        for i in self.brange:
            t[i] = self.getDiff(i)
        return t

class AttackCPA_Bayesian(QObject):
    """
    Bayesian CPA, as described in .
    """

    def __init__(self, model):
        super(AttackCPA_Bayesian, self).__init__()
        self.model = model

    def setByteList(self, brange):
        self.brange = brange

    def setKeyround(self, keyround):
        self.keyround = keyround
    
    def setModeltype(self, modeltype):
        self.modeltype = modeltype
    
    def addTraces(self, traces, plaintexts, ciphertexts, progressBar=None, pointRange=None, algo="log"):
        keyround=self.keyround
        modeltype=self.modeltype
        brange=self.brange
                                          
        traces = np.array(traces)
        plaintexts =np.array(plaintexts)
        ciphertexts =np.array(ciphertexts)

        foundkey = []

        self.all_diffs = range(0,16)

        if progressBar:
            pbcnt = 0
            progressBar.setMinimum(0)
            progressBar.setMaximum(len(brange) * 256)

        #For all bytes of key
        for bnum in brange:
            diffs = [0]*256

            #For each 0..0xFF possible value of the key byte
            for key in range(0, 256):                
                #Initialize arrays & variables to zero
                sumstd = np.zeros(len(traces[0,:]), dtype=np.float64)
                hyp = [0] * len(traces[:,0])           
                
                #Generate hypotheticals
                for tnum in range(len(traces[:,0])):

                    if len(plaintexts) > 0:
                        pt = plaintexts[tnum]

                    if len(ciphertexts) > 0:
                        ct = ciphertexts[tnum]

                    if keyround == "first":
                        ct = None
                    elif keyround == "last":
                        pt = None
                    else:
                        raise ValueError("keyround invalid")
                    
                    #Generate the output of the SBOX
                    if modeltype == "Hamming Weight":
                        hypint = self.model.HypHW(pt, ct, key, bnum);
                    elif modeltype == "Hamming Distance":
                        hypint = self.model.HypHD(pt, ct, key, bnum);
                    else:
                        raise ValueError("modeltype invalid")
                    
                    hyp[tnum] = self.model.getHW(hypint)
                    
                hyp = np.array(hyp)                    

                #Mean & STD-DEV of hypothesis
                meanh = np.mean(hyp, dtype=np.float64)
                stddevh = np.std(hyp, dtype=np.float64)

                #Mean & STD-DEV of all points in trace
                meant = np.mean(traces, axis=0, dtype=np.float64)
                stddevt = np.std(traces, axis=0, dtype=np.float64)
                                   
                #For each trace, do the following
                for tnum in range(len(traces[:,0])):
                    #Make both zero-mean
                    hdiff = np.float64(hyp[tnum]) - meanh
                    tdiff = np.array((np.array(traces[tnum,:], dtype=np.float64) - meant), dtype=np.float64)

                    #Scale by std-dev
                    hdiff = hdiff / np.float64(stddevh)
                    tdiff = tdiff / np.array(stddevt, dtype=np.float64)

                    #Calculate error term                   
                    error = (hdiff - tdiff)
                    sumstd = sumstd + pow(error, 2)

                q = len(traces[:,0])

                if algo == "original":
                    #Original Algorithm
                    sumstd = pow(np.sqrt((sumstd/q)), -q)
                elif algo == "log":
                    #LOG Algorithm
                    sumstd = -q * ( (0.5*np.log(sumstd)) - np.log(q))
                else:
                    raise RuntimeError("algo not defined")

                if progressBar:
                    progressBar.setValue(pbcnt)
                    #progressBar.setLabelText("Byte %02d: Hyp=%02x"%(bnum, key))
                    pbcnt = pbcnt + 1
                    if progressBar.wasCanceled():
                        break

                diffs[key] = sumstd

            #Gotten all stddevs - now process algorithm

            #Original algorithm
            if algo == "original":
                sums = np.sum(diffs, axis=0, dtype=np.float64)
                for key in range(0, 256):
                    diffs[key] = diffs[key] / sums

            elif algo == "log":
            #Logarithm Algorithm
                summation = np.zeros(len(traces[0,:]), dtype=np.float64)
                for key in range(1, 256):
                    diff = diffs[key] - diffs[0]
                    summation = summation + np.exp(diff)
                summation = summation + 1
                summation = np.log(summation)
                summation = summation + diffs[0]
                           
                for key in range(0, 256):
                    diffs[key] = diffs[key] - summation

            else:
                raise RuntimeError("algo not defined")
                            
            self.all_diffs[bnum] = diffs
        self.algo = algo
            #From all the key candidates, select the largest difference as most likely
            #foundbyte = diffs.index(max(diffs))
            #foundkey.append(foundbyte)
#            print "%2x "%foundbyte,



    def _getResult(self, bnum, hyprange=None):
        if hyprange == None:
            hyprange = range(0,256)
        return [self.all_diffs[bnum][i] for i in hyprange];
    
    def getDiff(self, bnum, hyprange=None):
        if self.algo == "original":
            return self._getResult(bnum, hyprange)
        elif self.algo == "log":
            return np.exp(self._getResult(bnum, hyprange))
        else:
            raise RuntimeError("algo not defined")
    
    def getStatistics(self):
        t = [0]*16
        for i in self.brange:
            t[i] = self.getDiff(i)
        return t

class AttackCPA_SciPyCorrelation(QObject):
    """
    Instead uses correlation functions provided by SciPy.
    WARNING: DOES NOT WORK RIGHT NOW.
    """

    def __init__(self, model):
        super(AttackCPA_SciPyCorrelation, self).__init__()
        self.model = model

    def setByteList(self, brange):
        self.brange = brange

    def setKeyround(self, keyround):
        self.keyround = keyround
    
    def setModeltype(self, modeltype):
        self.modeltype = modeltype
    
    def addTraces(self, traces, plaintexts, ciphertexts, progressBar=None, pointRange=None):
        keyround=self.keyround
        modeltype=self.modeltype
        brange=self.brange
                                                                   
        traces_all = np.array(traces)
        plaintexts =np.array(plaintexts)
        ciphertexts =np.array(ciphertexts)

        foundkey = []

        self.all_diffs = range(0,16)

        if progressBar:
            pbcnt = 0
            progressBar.setMinimum(0)
            progressBar.setMaximum(len(brange) * 256)

        numtraces = len(traces_all[:,0])
        numpoints =  len(traces_all[0,:])

        #For all bytes of key
        for bnum in brange:

            diffs = [0]*256

            if pointRange == None:
                traces = traces_all
                padbefore = 0
                padafter = 0
            else:
                traces = np.array(traces_all[:, pointRange[bnum][0] : pointRange[bnum][1]])
                padbefore = pointRange[bnum][0]
                padafter = len(traces_all[0,:]) - pointRange[bnum][1]
                #print "%d - %d (%d %d)"%( pointRange[bnum][0],  pointRange[bnum][1], padbefore, padafter)

            #For each 0..0xFF possible value of the key byte
            for key in range(0, 256):               

                hyp = np.empty((numtraces, 1))
                
                
                #Generate hypotheticals
                for tnum in range(numtraces):

                    if len(plaintexts) > 0:
                        pt = plaintexts[tnum]

                    if len(ciphertexts) > 0:
                        ct = ciphertexts[tnum]

                    if keyround == "first":
                        ct = None
                    elif keyround == "last":
                        pt = None
                    else:
                        raise ValueError("keyround invalid")
                    
                    #Generate the output of the SBOX
                    if modeltype == "Hamming Weight":
                        hypint = self.model.HypHW(pt, ct, key, bnum);
                    elif modeltype == "Hamming Distance":
                        hypint = self.model.HypHD(pt, ct, key, bnum);
                    else:
                        raise ValueError("modeltype invalid")
                    hyp[tnum, 0] = self.model.getHW(hypint)       

                if progressBar:
                    progressBar.setValue(pbcnt)
                    #progressBar.setLabelText("Byte %02d: Hyp=%02x"%(bnum, key))
                    pbcnt = pbcnt + 1
                    if progressBar.wasCanceled():
                        break

                diffs[key] = sp.signal.correlate(traces, hyp, 'valid')[0,:]
                
                  
            
            self.all_diffs[bnum] = diffs

            #From all the key candidates, select the largest difference as most likely
            #foundbyte = diffs.index(max(diffs))
            #foundkey.append(foundbyte)
#            print "%2x "%foundbyte,


    def getDiff(self, bnum, hyprange=None):
        if hyprange == None:
            hyprange = range(0,256)
        return [self.all_diffs[bnum][i] for i in hyprange];
    
    def getStatistics(self):
        t = [0]*16
        for i in self.brange:
            t[i] = self.getDiff(i)
        return t

#TODO: This should be broken into a separate function I think
class CPA(AttackBaseClass):
    """Correlation Power Analysis Attack"""
            
    def __init__(self, parent=None, log=None):
        super(CPA, self).__init__(parent)
        self.log=log 
        
    def setupParameters(self):      
        attackParams = [{'name':'Hardware Model', 'type':'group', 'children':[
                        {'name':'Crypto Algorithm', 'type':'list', 'values':{'AES-128 (8-bit)':attacks.models.AES128_8bit}, 'value':'AES-128'},
                        {'name':'Key Round', 'type':'list', 'values':['first', 'last'], 'value':'first', 'set':self.setRound},
                        {'name':'Power Model', 'type':'list', 'values':{'HW-VCC':('Hamming Weight', 'VCC'), 'HW-GND':('Hamming Weight', 'GND'), 'HD-VCC':('Hamming Distance', 'VCC'), 'HD-GND':('Hamming Distance', 'GND')}, 'value':('Hamming Weight', 'VCC'), 'set':self.setModel},
                        ]},
                       {'name':'Take Absolute', 'type':'bool', 'value':True},
                       
                       #TODO: Should be called from the AES module to figure out # of bytes
                       {'name':'Attacked Bytes', 'type':'group', 'children':
                         self.getByteList()                                                 
                        },                    
                      ]
        self.params = Parameter.create(name='Attack', type='group', children=attackParams)
        ExtendedParameter.setupExtended(self.params)
        
        self.setRound('first')
        self.setModel(("Hamming Weight", "VCC"))
            
    def setRound(self, rnd):
        self.attackRound = rnd
        
    def setModel(self, model):
        self.attackModel = model[0]
        self.attackModelVccGnd = model[1]
        
    def processKnownKey(self, inpkey):
        if inpkey is None:
            return None
        
        if self.attackRound == 'last':
            return attacks.models.AES_RoundKeys.AES_RoundKeys().getFinalKey(inpkey)
        else:
            return inpkey
            
    def doAttack(self):        
        #TODO: support start/end point different per byte
        (startingPoint, endingPoint) = self.getPointRange(None)
        (startingTrace, endingTrace) = self.getTraceRange()
        
        #print "%d-%d"%(startingPoint, endingPoint)
        
        data = []
        textins = []
        textouts = []
        
        for i in range(startingTrace, endingTrace):
            d = self.trace.getTrace(i)
            
            if d is None:
                continue
            
            d = d[startingPoint:endingPoint]
            
            data.append(d)
            textins.append(self.trace.getTextin(i))
            textouts.append(self.trace.getTextout(i)) 
        
        self.attack = AttackCPA_SimpleLoop(attacks.models.AES128_8bit)
         
        #Following still needs debugging
        #self.attack = AttackCPA_Bayesian(attacks.models.AES128_8bit)                        
        
        #Following attack DOES NOT WORK
        #self.attack = AttackCPA_SciPyCorrelation(attacks.models.AES128_8bit)
        
        self.attack.setByteList(self.bytesEnabled())
        self.attack.setKeyround(self.attackRound)
        self.attack.setModeltype(self.attackModel)
        
        progress = QProgressDialog("Analyzing", "Abort", 0, 100)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(1000)
        
        #TODO:  pointRange=self.TraceRangeList[1:17]
        self.attack.addTraces(data, textins, textouts, progress)
        
        self.attackDone.emit()

    def passTrace(self, powertrace, plaintext=None, ciphertext=None, knownkey=None):
        pass
    
    def getStatistics(self):
        return self.attack.getStatistics()
            
    def paramList(self):
        return [self.params, self.pointsParams]