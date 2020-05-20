#!/usr/bin/env python

"""
-------------------------------------------------------------------------------------------------
__Author__ : Simone Scrima
Group : Computational Biology Laboratory ; Danish Cancer Society Research Center ; CPH ; Denmark 
PLATFORM : Linux-64
Year : 2019 
Version : 0.1
-------------------------------------------------------------------------------------------------
"""



# coding: utf-8

from __future__ import absolute_import
from __future__ import print_function
import MDAnalysis as mda
import numpy as np
import math
import os, sys
from collections import OrderedDict
import errno
import subprocess
import argparse
import shutil
import multiprocessing as mp


#/******************************************************************
#*    Title: calcOrderParameters.py
#*    Author: J. Melcr with contribution of H.Antila
#*    Date: 2018/03/26 
#*    Availability: https://github.com/NMRLipids/MATCH/scripts
#*


# In Angstroms, max distance between atoms for reasonable OP 
# calculation (PBC and sanity check)
bond_len_max=1.5  
bond_len_max_sq=bond_len_max**2

class OrderParameter:
    
    # Class for storing and manipulating
    # order parameter (OP) related metadata (definition, name, ...),
    # trajectories and methods to evaluate OPs.

    # OP definition consist of:
    #    - name of the OP
    #    - residue name
    #    - involved atoms (exactly 2)
    #    + extra: mean, std.dev. & err. estimate 
    #             (using standard error of the means from individual residues)
    #             of the OP (when reading-in an already calculated result)
    

    def __init__(self, name, resname, atom_A_name, atom_B_name, *args):
        
        """Initialization of an instance of this class.
        Parameters
        ----------
        name : str 
                Name of the order parameter, a label
        resname : str
                Name of residue atoms are in
        atom_A_name : str
                XXX
        atom_B_name : str
                XXX

        """

        self.name = name             # Name of the order parameter, a label
        self.resname = resname       # Name of residue atoms are in
        self.atAname = atom_A_name
        self.atBname = atom_B_name

        # variables for error estimate -- standard error of the mean (STEM)
        self.avg   = None   # average/mean value from all residues
        self.means = None   # list of mean values from each individual residue
        self.std   = None   # standard deviation (sqrt(variance))
        self.stem  = None   # STandard Error of the Mean
        
        # Trajectory as list
        self.traj = []  # for storing OPs
        
        for field in self.__dict__:
            if not isinstance(field, str):
                raise UserWarning("provided name >> {} << is not a string! \n \
                Unexpected behaviour might occur.".format(field))
            else:
                if not field.strip():
                    raise RuntimeError("provided name >> {} << is empty! \n \
                    Cannot use empty names for atoms and OP definitions.".format(field))
        
        # extra optional arguments allow setting avg,std values -- suitable for reading-in results of this script
        if len(args) == 2:
            self.avg = args[0]
            self.std = args[1]
        elif len(args) == 3:
            self.avg  = args[0]
            self.std  = args[1]
            self.stem = args[2]
        else:
            if len(args) != 0:
                raise UserWarning("Number of optional positional arguments is {len}, \
                    not 3, 2 or 0. Args: {args}\n Wrong file format?".format(len=len(args), args=args))


    def calc_OP(self, atoms):
        
        # Calculates Order Parameter according to equation
        # S = 1/2 * (3*cos(theta)^2 -1)

        
        vec = atoms[1].position - atoms[0].position
        d2 = np.square(vec).sum()
        
        if d2>bond_len_max_sq:
            raise UserWarning("Atomic distance for atoms \
                {at1} and {at2} in residue no. {resnr} is suspiciously \
                long: {d}!\nPBC removed???".format(at1=atoms[0].name, \
                at2=atoms[1].name, resnr=atoms[0].resid, d=math.sqrt(d2)
                )
                )             
        
        cos2 = vec[2]**2/d2
        S = 0.5*(3.0*cos2-1.0)
        
        return(S)


    def calc_angle(self, atoms, z_dim = 45.0):
        
        # Calculates the angle between the vector and z-axis in degrees
        # no PBC check!
        
        vec = atoms[1].position - atoms[0].position
        d = math.sqrt(np.square(vec).sum())
        cos = vec[2]/d

        
        # values for the bottom leaflet are inverted so that 
        # they have the same nomenclature as the top leaflet

        cos *= math.copysign(1.0, atoms[0].position[2]-z_dim*0.5)
        try:
            angle = math.degrees(math.acos(cos))
        except ValueError:
            if abs(cos)>=1.0:
                print("Cosine is too large = {} --> truncating it to +/-1.0".format(cos))
                cos = math.copysign(1.0, cos)
                angle = math.degrees(math.acos(cos))
        return angle

    @property
    def get_avg_std_OP(self):
        
        # Provides average and stddev of all OPs in self.traj
        # This method becomes deprecated after the introduction of 
        # error estimation
        
        # convert to numpy array
        return(np.mean(self.traj), np.std(self.traj))


    @property
    def get_avg_std_stem_OP(self):
        
        # Provides average, stddev and standard error of mean for 
        # all OPs in self.traj
        
        self.means = np.mean(self.traj, axis=0)
        return( np.mean(self.traj), 
                 np.std(self.means), 
                 np.std(self.means)/np.sqrt(len(self.means)) )  


def read_trajs_calc_OPs(ordPars, top, trajs):
    
    """Procedure that creates MDAnalysis (mda) Universe instance 
    with topology top,reads in trajectories trajs and then goes 
    through every frame and evaluates each Order Parameter "S" 
    from the list of OPs ordPars.
    Parameters
    ----------
    ordPars : list of OrderParameter class instances
       each item in this list describes an Order parameter to be calculated in the trajectory
    top : str
        filename of a top file (e.g. conf.gro)
    trajs : list of strings
        filenames of trajectories
    """

    # read-in topology and trajectory
    mol = mda.Universe(top, trajs)

    # make atom selections for each OP and store it as its attribute for later use with trajectory
    for op in ordPars.values():
        # selection = pairs of atoms, split-by residues
        # this selection format preserves the order of the atoms (atA, atB) independent of their order in the topology
        selection = mol.select_atoms("resname {rnm} and name {atA}".format(
                                        rnm=op.resname, atA=op.atAname),
                                     "resname {rnm} and name {atB}".format(
                                        rnm=op.resname, atB=op.atBname)
                                    ).atoms.split("residue")
        
        for res in selection:
            
            # check if we have only 2 atoms (A & B) selected
            if res.n_atoms != 2:
                print(res.resnames, res.resids)
                for atom in res.atoms:
                    print(atom.name, atom.id)
                raise UserWarning("Selection >> name {atA} {atB} << \
                contains {nat} atoms, but should contain exactly 2!".format(
                atA=op.atAname, atB=op.atBname, nat=res.n_atoms)
                )
        op.selection = selection
      

    # Go through trajectory frame-by-frame
    # and calculate each OP from the list of OPs
    # for each residue separately
    for frame in mol.trajectory:
        for op in ordPars.values():
            
            # temporary list of order parameters for 
            # each individual residue for the given frame
            temp_S = []
            
            for residue in op.selection:
                if "vec" in op.name:
                    S = op.calc_angle(residue, z_dim=frame.dimensions[2])
                else:
                    S = op.calc_OP(residue)
                temp_S.append(S)

            # resulting S-trajectory will be a list of lists
            # so that individual residues can be easily distinquished
            op.traj.append(temp_S)


def parse_op_input(def_file):
    
    """
    parses input file with Order Parameter definitions
    file format is as follows:
    OP_name    resname    atom1    atom2  +extra: OP_mean  OP_std
    (flexible cols)
    fname : string
        string 
    returns : dictionary 
        with OrderParameters class instances
    """
    # Using ordered dict since it preserves the read-in order. Might come in handy when comparing to experiments.
    ordPars = OrderedDict()
    for line in def_file:
        if not line.startswith("#"):
            items = line.split()
            ordPars[items[0]] = OrderParameter(*items)
    return ordPars

#*******************************************************************


class Density:

    def __init__ (self,
                  universe,
                  selection
                  ):

        """Initialize the class 

        Parameters
        -------------
        universe : object 
            MDAnalysis universe object
        selection : object
            MDAnalysis AtomGroups selections   
        """
        self.universe = universe
        self.selection = selection

        bins = 0.02
        ts = self.universe.trajectory[0] 
        # divide by ten the coordinates to convert in nm 
        self.n1 =  int(round((ts.dimensions[0]*0.1)/bins)) # X coordinate of box
        self.n2 =  int(round((ts.dimensions[1]*0.1)/bins)) # Y coordinate of box
       

    def do2dgrid(self,
                 trajectory,
                 l_grids):

        """Execute the membranes command of fatslim,
        which identifies the upper and lower leaflet of
        a membrane simulation.

        Parameters
        ----------
        trajectory : object
              MDAnalysis universe.trajectory object
        l_grids : Object/list
              Manager() type list object from Multiprocessing library.
              It is shared between processes
        """
        
        grid = np.zeros((self.n1,self.n2)) # grid with shape of n1 and n2 
        g = self.selection # AtomGroups 
        for ts in trajectory: # begin cycle through traj
            # divide by ten the coordinates to convert in nm
            invcellvol = self.n1*self.n2
            invcellvol /= np.linalg.det(ts.triclinic_dimensions*0.1)
            for atom in g: 
                # x coordinate of the atom divided by the x dimension of box
                # find the fraction of box the atom is in on X
                # divide by ten the coordinates to convert in nm
           
                m1 = (atom.position[0]*0.1)/(ts.dimensions[0]*0.1) 
                if  m1 >= 1 : 
                    m1 -= 1 # pbc maybe subtracting 1 
                if m1 < 0 : 
                    m1 +=1
                m2 = (atom.position[1]*0.1)/(ts.dimensions[1]*0.1)
                if  m2 >= 1 : 
                    m2 -= 1 # pbc maybe subtracting 1 
                if m1 < 0 : 
                    m2 +=1
                
                grid[int(m1*self.n1)][int(m2*self.n2)] +=  invcellvol  
        l_grids.append(grid)
        return(l_grids)  
             

    def normalization(self,
                      final_grid):
        """
        final_grid : numpy array
        """

        box1 = 0
        box2 = 0 
        for ts in self.universe.trajectory:
            box1 += (ts.dimensions[0]*0.1) # X coordinate
            box2 += (ts.dimensions[1]*0.1) # Y coordinate
 
        # normalize grid point by the number of frames        
        grid = np.true_divide(final_grid, len(self.universe.trajectory))  

        #normalize box1 and tick_x; tick_x == tick_y 
        box1 = box1/len(self.universe.trajectory)
        box2 = box2/len(self.universe.trajectory)
        tick_x = [(i*box1)/self.n1 for i in range(self.n1)]
        tick_y = [(i*box2)/self.n2 for i in range(self.n1)]  
        grid = np.insert(grid, 0, tick_x, 0)   # add a row
        tick_y = np.append(0, tick_y)  # add one 0 to make the shape the same
        grid = np.insert(grid, 0, tick_y, 1)# add a column
        grid = np.around(grid,decimals=5)
        return(grid)



def dmap_multiprocessing(universe,
                         selection,
                         ncore):
    
    """Function to compute 2D density maps 

        Parameters
        -------------
        universe : class 
            MDAnalysis universe class
        selection : MDAnalysis AtomGroup
        ncore : int
            Number of core to use for multiprocessing
    """


    # initialize class with the the respective AtomGroup
    u = universe
    dmap = Density(u,selection)
    # divide the frames in chunk 
    chunk = [u.trajectory[x:x+int(len(u.trajectory)/ncore)] 
             for x in range(0,len(u.trajectory),
             int(len(u.trajectory)/ncore))]
    # can be shared between processes         
    final_grid = mp.Manager().list()  
    processes = []
    for slices in chunk:
        # Passing the list
        p = mp.Process(target=dmap.do2dgrid, args=(slices,final_grid))  
        p.start()
        processes.append(p)
    for p in processes:
        p.join()
        
    # sum all the grids generated in multiple processes   
    final_grid = sum(list(final_grid))
    grid = dmap.normalization(final_grid) # normalize
    return(grid)





class FatslimCommands:
     
    def __init__ (self,
                  trajectory,
                  gro,
                  headgroups_ndx_file,
                  ncore,
                  apl_cutoff,
                  thk_cutoff
                  ):

        """Initialize the class 

        Parameters
        -------------
        trajectory : file 
            Name of the .xtc file.
        gro : file 
            Name of the gro file 
        headgroups_ndx_file =  file
            Name of the ndx file containing the lipids headgroup
        ncore : str
            Number of core to parallelize the future metods
        apl_cutoff/thk_cutoff : float
            Float for the cutoff of apl or thickness command         
        """

        self.trajectory = os.path.abspath(trajectory)
        self.gro = os.path.abspath(gro)
        self.headgroups_ndx_file = os.path.abspath(headgroups_ndx_file)
        self.ncore = ncore
        self.apl_cutoff = float(apl_cutoff)
        self.thk_cutoff = float(thk_cutoff)
    
    def membranes(self,
                  out_file):

        """Execute the membranes command of fatslim,
        which identifies the upper and lower leaflet of
        a membrane simulation.

        Parameters
        ----------
        out_file : str 
                Name of the output file.
        """

        a = subprocess.call(['fatslim', 'membranes',
                             '-c',self.gro,
                             '-t',self.gro,
                             '-n',self.headgroups_ndx_file,
                             '--nthread', str(self.ncore),
                             '--output-index','bilayer.ndx'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL
                            ) 

    def raw_thickness(self,
                      out_file):

        """Execute the thickness command of fatslim,
        which compute the average thickness of lower,
        upper leaflet and the entire membrane in a
        ''.xvg file.
        In this case we compute the raw values for 
        each frame of the trajectory, creating multiple
        files.
    
        Parameters
        ----------
        out_file : str 
                Name of the output file.
        """
        
             
        # the user modified the cut-off
        if self.thk_cutoff != 2.0:
            a = subprocess.call(['fatslim', 'thickness',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--export-thickness-raw', out_file,
                                 '--thickness-cutoff',str(self.thk_cutoff)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )
        # default cut-off
        else: 
            a = subprocess.call(['fatslim', 'thickness',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--export-thickness-raw', out_file],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )


    def thickness(self,
                  out_file):

        """Execute the thickness command of fatslim,
        which compute the average thickness of lower,
        upper leaflet and the entire membrane in a
        ''.xvg file along the trajectory.
    
        Parameters
        ----------
        out_file : str 
                Name of the output file.
        """
        
        if self.thk_cutoff != 2.0: 
            a = subprocess.call(['fatslim', 'thickness',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread', str(self.ncore),
                                 '--plot-thickness', out_file,
                                 '--thickness-cutoff',str(self.thk_cutoff)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )
        else:
            a = subprocess.call(['fatslim',
                                 'thickness',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--plot-thickness', out_file],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )


    def raw_AreaPerLipid(self,
                         out_file):

        """Execute the APL command of fatslim,
        which compute the area per lipid thickness 
        of lower,upper leaflet and the entire membrane
        in a''.xvg file.
        In this case we compute the raw values for 
        each frame of the trajectory, creating multiple
        files.
    
        Parameters
        ----------
        out_file : str 
                Name of the output file.
        """

        if self.apl_cutoff != 3.0:
            a = subprocess.call(['fatslim', 'apl',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--export-apl-raw',out_file,
                                 '--apl-cutoff',str(self.apl_cutoff)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )
        else:
            a = subprocess.call(['fatslim', 'apl',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--export-apl-raw',out_file],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )


    def AreaPerLipid(self,
                     out_file):

        """Execute the APL command of fatslim,
        which compute the average area per lipid 
        of lower,upper leaflet and the entire membrane 
        in a ''.xvg file along the trajectory.
    
        Parameters
        ----------
        out_file : str 
                Name of the output file.
        """

        if self.apl_cutoff != 3.0:
            a = subprocess.call(['fatslim', 'apl',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--plot-apl',out_file,
                                 '--apl-cutoff', str(self.apl_cutoff)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )
        else:
            a = subprocess.call(['fatslim', 'apl',
                                 '-c',self.gro,
                                 '-n',self.headgroups_ndx_file,
                                 '-t',self.trajectory,
                                 '--nthread',str(self.ncore),
                                 '--plot-apl',out_file],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL
                                )

