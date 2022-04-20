import torch
from statistics import mean
import numpy as np

BOLTZMAN = 0.001987191

class WeightedEnsemble:
    def __init__(
        self,
        nnp,
        nstates,
        lr,
        metric,
        loss_fn,
        val_fn,
        max_grad_norm = 550,
        T = 350,
        replicas = 1,
        device='cpu',
        precision = torch.double,
     ):
        self.nstates = nstates
        self.metric = metric
        self.loss_fn = loss_fn
        self.val_fn = val_fn
        self.max_grad_norm = max_grad_norm
        self.T = T
        self.replicas = replicas
        self.device = device
        self.precision = precision
        
        # ------------------- Neural Network Potential and Optimizer -----------------
        self.nnp = nnp
        self.optimizer = torch.optim.Adam(self.nnp.parameters(), lr=lr)

        # ------------------- Loss ----------------------------------
        self.loss = torch.tensor(0, dtype = precision, device=device)
        
        # ------------------- Create the states ---------------------
        self.states = None
        self.init_coords = None
        
    @classmethod
    def create_factory(cls,
                       nstates,
                       lr,
                       metric,
                       loss_fn,
                       val_fn,
                       max_grad_norm = 550,
                       T = 350,
                       replicas = 1,
                       precision = torch.double):
        """
        Returns a function to create new WeightedEnsemble instances
        
        Parameters
        -----------
        nstates: int
            Number of states
        T: float
            Temperature of the system
        replicas: int
            Number of replicas (simulations of the same system) to run
        device: torch.device
            CPU or specific GPU where class computations will take place.
        precision: torch.precision
            'Floating point precision'
        
        Returns
        --------
        create_weighted_ensemble_instance: func
            A function to create new WeightedEnseble instances.
            
        """
        
        def create_weighted_ensemble_instance(nnp, device):
            return cls(nnp,
                       nstates,
                       lr,
                       metric,
                       loss_fn,
                       val_fn,
                       max_grad_norm,
                       T,
                       replicas,
                       device,
                       precision
                      )
        return create_weighted_ensemble_instance
    
    def _extEpot(self, states, embeddings, mode="train"):
        
        # Prepare pos, embeddings and batch tensors
        pos = states.to(self.device).type(torch.float32).reshape(-1, 3)
        embeddings = embeddings.repeat(states.shape[0] , 1)
        batch = torch.arange(embeddings.size(0), device=self.device).repeat_interleave(
            embeddings.size(1)
        )
        embeddings = embeddings.reshape(-1).to(self.device)
                
        # Compute external energies
        if mode == "train":
            ext_energies, ext_forces = self.nnp(embeddings, pos, batch)
        elif mode == "val":
            ext_energies, ext_forces = self.nnp(embeddings, pos, batch)
        
        ext_forces.detach()
        return ext_energies.squeeze(1)
                       
    def _weights(self, states, embeddings, U_prior):
        
        # Compute external Epot and create a new eternal Epot detached 
        U_ext = self._extEpot(states, embeddings, mode="train")
        U_ext_hat = U_ext.detach()
        
        U_prior = U_prior.to(U_ext.device)

        U_ref = torch.add(U_prior, U_ext_hat)
        U = torch.add(U_prior, U_ext)

        exponentials = torch.exp(-torch.divide(torch.subtract(U, U_ref), self.T*BOLTZMAN))
        weights = torch.divide(exponentials, exponentials.sum())
        return weights, U_ext_hat
    
    def _effectiven(self, weights):
        
        lnwi = torch.log(weights)
        neff = torch.exp(-torch.sum(torch.multiply(weights, lnwi), axis=0)).detach()
        
        return neff
    
    def compute(self, states, ground_truth, embeddings, U_prior, neff_threshold=None):
        
        weights, U_ext_hat = self._weights(states, embeddings, U_prior)
        
        n = len(weights)
        
        # Compute the weighted ensemble of the conformations 
        states = states.to(self.device)
                
        obs = torch.tensor([self.metric(state, ground_truth) for state in states], device = self.device, dtype = self.precision)
        w_ensemble = torch.multiply(weights, obs).sum(0) 
        
        return w_ensemble
    
    def compute_loss(self, ground_truth, states, embeddings, U_prior):
        
        w_e = self.compute(states, ground_truth, embeddings, U_prior)
        loss = self.loss_fn(w_e)  
        return loss
        
    
    def compute_gradients(self, ground_truth, states, embeddings, U_prior, grads_to_cpu=True):
                
        self.optimizer.zero_grad()
        loss = self.compute_loss(ground_truth, states, embeddings, U_prior)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.nnp.parameters(), self.max_grad_norm)
        
        
        grads = []
        for p in self.nnp.parameters():
            if grads_to_cpu:
                if p.grad is not None: grads.append(p.grad.data.cpu().numpy())
                else: grads.append(None)
            else:
                if p.grad is not None:
                    grads.append(p.grad)
                    
        return grads, loss.item()
        
    
    def get_loss(self):
        return self.loss.detach().item()
    
    def compute_val_loss(self, ground_truth, states, **kwargs):
        
        # Compute val loss
        
        n_states = 'last'
        if n_states == 'last':
            val_rmsd = self.val_fn(states[-1], ground_truth).item()
        elif n_states == 'last10':
            val_rmsd = mean([self.val_fn(ground_truth, state).item() for state in states[-10:]])
        else:
            val_rmsd = mean([self.val_fn(ground_truth, state).item() for state in states])   
        
        #self.init_coords = states[-1]
        
        return val_rmsd
        
    def apply_gradients(self, gradients):
        
        if gradients:
            for g, p in zip(gradients, self.nnp.parameters()):
                if g is not None:
                    p.grad = torch.from_numpy(g).to(self.device)
                    
        self.optimizer.step()
    
    def set_lr(self, lr):
        for g in self.optimizer.param_groups:
            g['lr'] = lr
    
    def get_native_U(self, ground_truth, embeddings):
        ground_truth = ground_truth.unsqueeze(0)
        return self._extEpot(ground_truth, embeddings, mode='val')
    
    def get_init_state(self):
        return self.init_coords