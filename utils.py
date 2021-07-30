from torch.utils.data import Dataset
import os
from moleculekit.molecule import Molecule
import shutil
import torch

# Read a dataset of input files
class ProteinDataset(Dataset):
    def __init__(self, pdbids, pdbs_dir, psfs_dir, cg=False, device='cpu'):
        self.pdbids = pdbids
        self.pdbs_dir = pdbs_dir
        self.psfs_dir = psfs_dir
        self.set_size = len(pdbids)
        self.device = device
        self.cg = cg
        
    def __len__(self):
        return self.set_size
    
    def __extract_CA(self, mol):
        # Get the structure with only CAs
            cwd = os.getcwd()
            tmp_dir = cwd + '/tmpcg/'
            os.mkdir(tmp_dir) # tmp directory to save full pdbs
            mol = mol.copy()
            mol.write(tmp_dir + 'molcg.pdb', 'name CA')
            mol = Molecule(tmp_dir + 'molcg.pdb')
            shutil.rmtree(tmp_dir)
            return mol
            
    def __getitem__(self, index):
        pdb_mol = os.path.join(self.pdbs_dir, self.pdbids[index] + '.pdb')
        mol = Molecule(pdb_mol)
        if self.cg:
            mol = self.__extract_CA(mol)
        
        psf_mol = os.path.join(self.psfs_dir, self.pdbids[index] + '.psf')
        mol.read(psf_mol)
        
        return mol


### Functions to extract parameters

def get_mol_bonds(mol):
    bonds = []
    for index in range(len(mol.atomtype) - 1):
        bond = f'({mol.atomtype[index]}, {mol.atomtype[index+1]})'
        bonds.append(bond)
    return bonds

def get_bond_params(all_bonds_dict, bonds):
    mol_bonds_dict = {}
    i = 0
    for bond in bonds:
        for key in all_bonds_dict:
            if bond == key:
                i += 1
                mol_bonds_dict[i] = {}
                mol_bonds_dict[i][key] = all_bonds_dict[key]
                
    return mol_bonds_dict

def extract_bond_params(ff, mol):
    all_bonds_dict = ff.prm['bonds']
    
    bonds = get_mol_bonds(mol)
    mol_bonds_dict = get_bond_params(all_bonds_dict, bonds)
    
    params = []
    for key in mol_bonds_dict:
        for key, value in mol_bonds_dict[key].items():
            params.append(list(value.values()))

    return torch.tensor(params)

# Functions to insert parameters

def create_bonds_dict(mol, forces):
    bond_names = get_mol_bonds(mol)
    bond_params = forces.par.bond_params.tolist()
    params_names = ['k0', 'req']
    
    bond_params_list = []
    for values in bond_params:
        bond_params_list.append(dict(zip(params_names, values)))
    mol_bonds_dict = dict(zip(bond_names,bond_params_list))
    
    return mol_bonds_dict

def insert_bond_params(mol, forces, all_bonds_dict):
    mol_bonds_dict = create_bonds_dict(mol, forces)
    
    for bond in mol_bonds_dict:
        for key in all_bonds_dict:
            if bond == key:
                all_bonds_dict[key] = mol_bonds_dict[key]
                
    return all_bonds_dict

# Functions to set the ff bond parameters 

def set_ff_bond_parameters(ff, k0, req ,todo = "mult"):
    
    for key in ff.prm["bonds"]:
        if todo == "mult":
            ff.prm["bonds"][key]['k0'] *= k0
            ff.prm["bonds"][key]['req'] *= req
        elif todo == "set":
            ff.prm["bonds"][key]['k0'] = k0
            ff.prm["bonds"][key]['req'] = req
    
    return ff
