from torch.utils.data import Dataset
import os
from moleculekit.molecule import Molecule
import shutil
import torch
import numpy as np
from .utils import CA_MAP, CACB_MAP, pdb2psf_CA
import copy
import random
from operator import itemgetter 

class ProteinDataset(Dataset):
    """ 
    Class to create a dataset of proteins as moleculekit objects.
    
    Parameters:
    ------------
    pdbids: list
        List with the names of the protein files.cacaca
    data_dir: str
        Directory where protein structural files are stored.
    topology: set
        Set with the topology terms that are considered. e.g : ('bonds', 'angles', 'dihedrals')
    
    Attributes:
    ------------
    pdbids: list
        List with the names of the protein files.
    dat
    
    """
    def __init__(self, filename=None, data_dict = {}):
        
        if filename is not None:
            self.dataset = np.load(filename, allow_pickle=True).item()
        else:
            self.dataset = data_dict
            
        self.size = len(self.dataset['names'])
                        
    def __len__(self):
        return self.size
    
    def __getitem__(self, index):
        
        new_dataset = {key: self.dataset[key][index] for key in self.dataset.keys()}
        
        if isinstance(index, slice):
            index_list = list(range(index.stop)[index]) if index.stop is not None else [0]
        elif isinstance(index, int):
            index_list = [index]
        else:
            raise IndexError(f'Index {index} provided is not as slice nor an integer.')

        first_idx = index_list[0]
        n_to_add = (index_list[-1] + 1) - self.size
        batch_size = len(index_list)
        if n_to_add > 0 and batch_size < self.size and first_idx < self.size:
            rdm_idx = torch.multinomial(torch.ones(first_idx), num_samples=n_to_add)
            
            for key in self.dataset.keys():
                mols_to_sample = self.dataset[key][:first_idx]
                new_dataset[key] += list(itemgetter(*rdm_idx)(mols_to_sample)) if len(rdm_idx) != 1 else [mols_to_sample[rdm_idx[0]]]
            
        return self._create_dataset(new_dataset)

    @classmethod
    def _create_dataset(cls, data_dict):
        return cls(data_dict = data_dict)
    
    def get(self, key):
        """ Returns dataset values of a given key. """
        return self.dataset[key]
    
    def set_value(self, data_dict):
        """ Sets a new value for the given dataset keys"""
        
        for k, v in data_dict.items():
            self.dataset[k] = v
    
    def get_keys(self):
        """ Returns the dataset keys as a list. """
        return list(self.dataset.keys())
    
    def shuffle(self):
        temp = list(zip(*[self.dataset[key] for key in self.dataset.keys()]))
        random.shuffle(temp)
        keys = list(self.dataset.keys())
        for idx, rdm_list in enumerate(zip(*temp)):
            self.dataset[keys[idx]] = list(rdm_list)
    
    def add_gaussian_noise(self, mu = 0.0, std = 0.1, sigma = 1.0):
        
        noisy_molecules = []
        for mol in self.dataset.get('molecules'):
            noise = np.random.normal(mu, std, size = mol.coords.shape).astype(mol.coords.dtype)
            mol.coords = mol.coords + sigma * noise
            noisy_molecules.append(mol)
        self.dataset['molecules'] = noisy_molecules
