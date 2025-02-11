import numpy as np
import torch
import copy
from ..datasets.utils import get_chains

def get_embeddings(mol, device, replicas, multi_chain=False):
    """ 
    Recieve moleculekit object and translates its aminoacids 
    to an embeddings list
    
    Args:
        multi_chain (bool, optional): Determines whether to use different embeddings
        for receptor and ligand. Defaults to False.
    """

    if not multi_chain:
        emb = np.array([AA2INT[c][aa] for c, aa in zip(mol.name, mol.resname)])  
    
    # Same as without multichain but add 22 to ligand chain to get different embeddings
    else:
        emb = np.array([AA2INT[x] if (ch.startswith('R')) else AA2INT[x] + 21 \
                                            for x, ch in zip(mol.resname, mol.chain)])

    emb = torch.tensor(emb, device = device).repeat(replicas, 1)
    return emb
    
def get_native_coords(mol, device='cpu'):
    """
    Return the native structure coordinates as a torch tensor and with shape (mol.numAtoms, 3)
    """
    pos = torch.zeros(mol.numAtoms, 3, device = device)
    
    atom_pos = np.transpose(mol.coords, (2, 0, 1))

    pos[:] = torch.tensor(
            atom_pos, dtype=pos.dtype, device=pos.device
    )
    pos = pos.type(torch.float32)
    
    pos.to(device)
    
    return pos

def moleculekit_system_factory(num_workers):
    
    #batch_size = len(systems_dataset) // num_workers
    systems = []
    worker_info = []
    
    for i in range(num_workers):
        batch = systems_dataset[batch_size * i:batch_size * (i+1)]
        systems.append(batch.get('molecules'))

        info = {}
        for key in batch.get_keys():
            if key != 'molecules': info[key] = batch.get(key)

        worker_info.append(info)
        
    return systems, worker_info

def create_system(molecules, dist = 200):
    """
    Return a system with multiple molecules separated by a given distance. 
    
    Parameters:
    -------------
    molecules: list
        List of moleculekit objects
    dist: float
        Minimum distance separation between the centers of the molecules
    
    Return:
    -------------
    batch: moleculekit object
        Moleculekit object with all the molecules.
    """
    prev_div = 0 
    axis = 0

    for idx, mol in enumerate(molecules):
        move = np.array([0, 0, 0,])
        if idx == 0:
            batch = copy.deepcopy(mol)
        else:
            div = idx // 6
            if div != prev_div:
                prev_div = div
                axis = 0
            if idx % 2 == 0:
                move[axis] = dist + dist * div
            else:
                move[axis] = -dist + -dist * div
                axis += 1
            
            #mol.dropFrames(keep=0)
            #mol.moveBy(move)
            mol.coords = mol.coords + move[:, None]

            ml = len(batch.coords)
            batch.append(mol) # join molecules
            batch.box = np.array([[0],[0],[0]], dtype = np.float64)
            batch.dihedrals = np.append(batch.dihedrals, mol.dihedrals + ml, axis=0)

    return batch




AA2INT = {'CA': {'ALA': 1, 'GLY':2, 'PHE':3, 'TYR':4, 'ASP':5, 'GLU':6, 'TRP':7,'PRO':8,
              'ASN':9, 'GLN':10, 'HIS':11, 'HSD':11, 'HSE':11, 'SER':12,'THR':13,
              'VAL':14, 'MET':15, 'CYS':16, 'NLE':17, 'ARG':18,'LYS':19, 'LEU':20,
              'ILE':21,
             },
                'CB': {'ALA': 22, 'GLY':23, 'PHE':24, 'TYR':25, 'ASP':26, 'GLU':27, 'TRP':28,'PRO':29,
              'ASN':30, 'GLN':31, 'HIS':32, 'HSD':33, 'HSE':34, 'SER':35,'THR':36,
              'VAL':37, 'MET':38, 'CYS':39, 'NLE':40, 'ARG':41,'LYS':42, 'LEU':43,
              'ILE':44
             }}


a = ''' AA2INT = {'ALA':1,
         'GLY':2,
         'PHE':3,
         'TYR':4,
          'ASP':5,
          'GLU':6,
          'TRP':7,
          'PRO':8,
          'ASN':9,
          'GLN':10,
          'HIS':11,
          'HSE':11,
          'HSD':11,
          'SER':12,
          'THR':13,
          'VAL':14,
          'MET':15,
          'CYS':16,
          'NLE':17,
          'ARG':19,
          'LYS':20,
          'LEU':21,
          'ILE':22
         } '''
