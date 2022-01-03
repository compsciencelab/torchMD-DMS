from .base import Sampler
from .utils import get_embeddings, get_native_coords
import torch
from torchmd.forcefields.forcefield import ForceField
from torchmd.forces import Forces
from torchmd.integrator import Integrator, maxwell_boltzmann
from torchmd.parameters import Parameters
from torchmd.systems import System
from torchmdexp.nn.calculator import External
import collections
import itertools
from torchmdexp.utils.get_native_coords import get_native_coords
import numpy as np
import copy

class TorchMD_Sampler(Sampler):
    """
    Sampler that uses torchmd package to simulate a given system.
    
    Parameters
    -----------
    mol: Moleculekit object
        Contain the system to simulate. Can have more than one molecule
    nnp: LightningModule
        Neural Network Potential used to simulate the system
    mls: list
        Contains the lengths of each molecule in the Moleculekit object
    focefield: str
        Directory of the forcefield file
    device: torch.device
        CPU or specific GPU where class computations will take place.
    replicas: int
        Number of replicas (simulations of the same system) to run
    cutoff : float
        If set to a value it will only calculate LJ, electrostatics and bond energies for atoms which are closer
        than the threshold
    rfa : bool
        Use with `cutoff` to enable the reaction field approximation for scaling of the electrostatics up to the cutoff.
        Uses the value of `solventDielectric` to model everything beyond the cutoff distance as solvent with uniform
        dielectric.
    switch_dist: float
        Switching distance for LJ
    exclusions: tuple
        exclusions for the LJ or repulsionCG term
    timestep: int
        Timestep in fs
    precision: torch.precision
        'Floating point precision'
    temperature: float
        Assign velocity from initial temperature in K
    langevin_temperature: float
        Temperature in K of the thermostat
    langevin_gamma: float
        Langevin relaxation ps^-1
    
    Attributes:
    ------------
    precision: torch.precision
        'Floating point precision'
    mls: list
        Contains the lengths of each molecule in the Moleculekit object
    sim_dict: dict
        Dict containing information about each state (coordinates) and prior Energy of each molecule simulated
    integrator: Integrator class
        Integrator class used to run the simulation
        
    """
    
    def __init__(self,
                 mol,
                 nnp,
                 device,
                 mls,
                 ground_truth,
                 forcefield, 
                 forceterms,
                 replicas, 
                 cutoff, 
                 rfa, 
                 switch_dist, 
                 exclusions,
                 timestep=1,
                 precision=torch.double,
                 temperature=350,
                 langevin_temperature=350,
                 langevin_gamma=0.1 
                ):
        
        self.precision = precision
        self.mls = mls
        self.temperature = temperature
        self.replicas = replicas
        self.init_coords = mol.coords
        
        # ------------------- Neural Network Potential -----------------------------
        self.nnp = nnp

        # ------------------- Set the ground truth list (PDB coordinates) -----------
        self.ground_truth = ground_truth
        
        # Create the dictionary used to return states and prior energies
        self.sim_dict = collections.defaultdict(dict)
        for idx , ml in enumerate(mls):
            self.sim_dict['system' + str(idx)]['states'] = None
            self.sim_dict['system' + str(idx)]['U_prior'] = 0
            
        
        # Create embeddings and the external force
        embeddings = get_embeddings(mol, device, replicas)
        external = External(nnp, embeddings, device = device, mode = 'val')
        
        # Add the embeddings to the sim_dict
        my_e = embeddings 
        for idx, ml in enumerate(mls):
            mol_embeddings, my_e = my_e[:, :ml], my_e[:, ml:]
            self.sim_dict['system' + str(idx)]['embeddings'] = mol_embeddings
            
        # Create forces
        ff = ForceField.create(mol,forcefield)
        parameters = Parameters(ff, mol, terms=forceterms, device=device) 
        self.forces = Forces(parameters,terms=forceterms, external=external, cutoff=cutoff, 
                             rfa=rfa, switch_dist=switch_dist, exclusions = exclusions
                        )
        
        # Create the system
        system = System(mol.numAtoms, nreplicas=self.replicas, precision = precision, device=device)
        system.set_positions(mol.coords)
        system.set_box(mol.box)
        system.set_velocities(maxwell_boltzmann(self.forces.par.masses, T=self.temperature, replicas=self.replicas))
        
        self.integrator = Integrator(system, self.forces, timestep, gamma = langevin_gamma, 
                                device = device, T= langevin_temperature)
        
    @classmethod
    def create_factory(cls,
                       forcefield, 
                       forceterms,
                       replicas, 
                       cutoff, 
                       rfa, 
                       switch_dist, 
                       exclusions,
                       timestep=1,
                       precision=torch.double,
                       temperature=350,
                       langevin_temperature=350,
                       langevin_gamma=0.1):
        """ 
        Returns a function to create new TorchMD_Sampler instances.
        
        Parameters
        -----------
        focefield: str
            Directory of the forcefield file
        device: torch.device
            CPU or specific GPU where class computations will take place.
        replicas: int
            Number of replicas (simulations of the same system) to run
        cutoff : float
            If set to a value it will only calculate LJ, electrostatics and bond energies for atoms which are closer
            than the threshold
        rfa : bool
            Use with `cutoff` to enable the reaction field approximation for scaling of the electrostatics up to the cutoff.
            Uses the value of `solventDielectric` to model everything beyond the cutoff distance as solvent with uniform
            dielectric.
        switch_dist: float
            Switching distance for LJ
        exclusions: tuple
            exclusions for the LJ or repulsionCG term
        timestep: int
            Timestep in fs
        precision: torch.precision
            'Floating point precision'
        temperature: float
            Assign velocity from initial temperature in K
        langevin_temperature: float
            Temperature in K of the thermostat
        langevin_gamma: float
            Langevin relaxation ps^-1
        
        Returns
        ---------
        create_sampler_instance: func
            creates a new TorchMD_Sampler instance.
        """

        def create_sampler_instance(mol, nnp, device, mls, ground_truth):
            return cls(mol,
                       nnp,
                       device,
                       mls, # molecule lengths
                       ground_truth,
                       forcefield, 
                       forceterms,
                       replicas, 
                       cutoff, 
                       rfa, 
                       switch_dist, 
                       exclusions,
                       timestep,
                       precision,
                       temperature,
                       langevin_temperature,
                       langevin_gamma)
        
        return create_sampler_instance

            
    def simulate(self, steps, output_period):
        """
        Function to run a simulation of the system, and sample a given number of states with their prior energies 
        from the trajectory.
        
        Parameters
        -----------
        steps: int
            Trajectory length.
        output_period: int
            Number of steps required to sample a new state.
            
        Returns
        -----------
        sim_dict: dict
            Dictionary with the sampled states and their prior Energies.
                number of states = steps // output_period
        """
            
        # Iterator and start computing forces
        iterator = range(1,int(steps/output_period)+1)
        self.integrator.systems.set_positions(self.init_coords)
        self.integrator.systems.set_velocities(maxwell_boltzmann(self.forces.par.masses, T=self.temperature, replicas=self.replicas))
        
        # Define the states
        nstates = int(steps // output_period)
        states = torch.zeros(nstates, len(self.integrator.systems.pos[0]), 3, device = "cpu",
                         dtype = self.precision)

        # Create dict to collect states and energies
        sample_dict = copy.deepcopy(self.sim_dict)
        
        
        # Run the simulation
        for i in iterator:
            Ekin, Epot, T = self.integrator.step(niter=output_period)
            states[i-1] = self.integrator.systems.pos.to("cpu")
            
            # Extract prior energies
            E_bonds = self.integrator.forces.E_bonds.to('cpu')
            E_dih = self.integrator.forces.E_dihedrals.to('cpu')
            ava_idx_cut = self.integrator.forces.ava_idx_cut.to('cpu')
            E_rep = self.integrator.forces.E_repulsioncg.to('cpu')
            
            # Fill dict
            sample_dict = self._split_bonds_E(E_bonds, sample_dict)
            sample_dict = self._split_dih_E(E_dih, sample_dict)
            sample_dict = self._split_rep_E(E_rep, ava_idx_cut, sample_dict)

        sample_dict = self._split_states(states, sample_dict)
        self.sim_dict.update(sample_dict)
                
        return self.sim_dict

    def set_init_state(self, init_coords):
        """
        Changes the initial coordinates of the system.
        
        Parameters
        -----------
        init_coords: np.array
            Array with the new coordinates of the system 
                Size = 
        """
            
        self.init_coords = init_coords
    
    def set_weights(self, weights):
        self.nnp.load_state_dict(weights)
    
    def get_ground_truth(self, gt_idx):
        return self.ground_truth[gt_idx]
    
    
    def _split_bonds_E(self, E_bonds, sample_dict):
        """
        Computes the sum of the bonded energies of each molecule simulated.
        And adds them to the sample_dict
        """
        
        for idx, ml in enumerate(self.mls):
            len_bonds = ml - 1 
            E_bonds_mol, E_bonds = E_bonds[:len_bonds], E_bonds[len_bonds:]
            sample_dict['system' + str(idx)]['U_prior'] += E_bonds_mol.sum()
        
        return sample_dict
    
    def _split_dih_E(self, E_dih, sample_dict):
        """
        Computes the sum of the dihedrals energies of each molecule simulated.
        And adds them to the sample_dict
        """

        for idx, ml in enumerate(self.mls):
            len_dihedrals = ml - 3
            E_dih_mol, E_dih = E_dih[:len_dihedrals], E_dih[len_dihedrals:]
            sample_dict['system' + str(idx)]['U_prior'] += E_dih_mol.sum()
        
        return sample_dict

    def _split_rep_E(self, E_rep, ava_idx_cut, sample_dict):
        """
        Computes the sum of the repulsioncg energies of each molecule simulated.
        And adds them to the sample_dict
        """

        mol_num = 0
        len_rep = 0
        prev_ml = 0
        
        for pair in ava_idx_cut: 
            pair = pair.tolist()
            if set(pair).intersection(set(range(prev_ml, prev_ml + self.mls[mol_num]))): 
                len_rep += 1
            else:
                len_rep += 1
                E_rep_mol, E_rep = E_rep[:len_rep], E_rep[len_rep:]
                sample_dict['system' + str(mol_num)]['U_prior'] += E_rep_mol.sum()
                len_rep = 0
                prev_ml += self.mls[mol_num]
                mol_num += 1
                
        sample_dict['system' + str(mol_num)]['U_prior'] += E_rep.sum()
        return sample_dict
    
    def _split_states(self, states, sample_dict):
        """
        Split the states tensor and adds the coordinates of each molecule to the sample_dict
        """
        for idx, ml in enumerate(self.mls):
            states_mol, states = states[:, :ml, :], states[:, ml:, :]
            sample_dict['system' + str(idx)]['states'] = states_mol
        return sample_dict
    
    
def moleculekit_system_factory(molecules, num_workers):

    prev_div = 0 
    axis = 0
    move = np.array([0, 0, 0,])
    
    batch_size = len(molecules) // num_workers
    systems = []
    worker_info = []
    
    for i in range(num_workers):
        batch_molecules, molecules = molecules[:batch_size], molecules[batch_size:]
        batch_mls = []
        batch_gt = [] 
        
        for idx, mol in enumerate(batch_molecules):

            native_coords = get_native_coords(mol)
            name = mol.viewname[:-4]
            ml = len(mol.coords)

            if idx == 0:
                mol.dropFrames(keep=0)
                batch = copy.copy(mol)

            else:
                div = idx // 6
                if div != prev_div:
                    prev_div = div
                    axis = 0
                if idx % 2 == 0:
                    move[axis] = 1000 + 1000 * div
                else:
                    move[axis] = -1000 + -1000 * div
                    axis += 1

                mol.dropFrames(keep=0)

                mol.moveBy(move)
                move = np.array([0, 0, 0])

                batch.append(mol) # join molecules 
                batch.box = np.array([[0],[0],[0]], dtype = np.float32)
                batch.dihedrals = np.append(batch.dihedrals, mol.dihedrals + ml, axis=0)
            batch_mls.append(ml)
            batch_gt.append(native_coords)
            
        systems.append(batch)
        info = {'mls': batch_mls, 'ground_truth': batch_gt}
        worker_info.append(info)
        
    return systems, worker_info
