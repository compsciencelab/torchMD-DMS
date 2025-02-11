import torch
import logging
from torch.nn.functional import l1_loss
from torchmdexp.utils import clip_grad

BOLTZMAN = 0.001987191

class WeightedEnsemble:
    def __init__(
        self,
        nnp,
        optimizer,
        nstates,
        lr,
        metric,
        loss_fn,
        val_fn,
        max_grad_norm = 550,
        T = 350,
        replicas = 1,
        device='cpu',
        precision = torch.float32,
        energy_weight = 0.0,
        var_weight = 0.0
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
        self.energy_weight = energy_weight
        self.var_weight = var_weight
        self.logger = logging.getLogger(__name__)

        self.gradnorm_queue = clip_grad.Queue()
        self.gradnorm_queue.add(3000)


        # ------------------- Neural Network Potential and Optimizer -----------------
        self.nnp = nnp
        self.optimizer = optimizer

        # ------------------- Loss ----------------------------------
        self.loss = torch.tensor(0, dtype = precision, device=device)
        
        # ------------------- Create the states ---------------------
        self.states = None
        self.init_coords = None

    @classmethod
    def create_factory(cls,
                       optimizer,
                       nstates,
                       lr,
                       metric,
                       loss_fn,
                       val_fn,
                       max_grad_norm = 550,
                       T = 298,
                       replicas = 1,
                       precision = torch.float32,
                       energy_weight = 0.0,
                       var_weight = 0.0
                      ):
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
                       optimizer,
                       nstates,
                       lr,
                       metric,
                       loss_fn,
                       val_fn,
                       max_grad_norm,
                       T,
                       replicas,
                       device,
                       precision,
                       energy_weight,
                       var_weight
                      )
        return create_weighted_ensemble_instance
    
    def _extEpot(self, states, embeddings, mode="train"):
        
        batch_num = states.shape[0] // self.replicas
        ext_energies, ext_energies_hat = torch.tensor([], device=self.device), torch.tensor([], device=self.device)

        for irepl in range(self.replicas):
            batch_states = states[batch_num * irepl: batch_num * (irepl+1)]
            
            # Prepare pos, embeddings and batch tensors
            pos = batch_states.to(self.device).type(torch.float32).reshape(-1, 3)
            embeddings_nnp = embeddings[0].repeat(batch_states.shape[0], 1)
            batch = torch.arange(embeddings_nnp.size(0), device=self.device).repeat_interleave(
                embeddings_nnp.size(1)
            )
            embeddings_nnp = embeddings_nnp.reshape(-1).to(self.device)

            # Compute external energies
            
            batch_ext_energies, _ = self.nnp(embeddings_nnp, pos, batch)
            batch_ext_energies_hat = batch_ext_energies.detach()
            
            ext_energies = torch.cat((ext_energies, batch_ext_energies), axis=0)
            ext_energies_hat = torch.cat((ext_energies_hat, batch_ext_energies_hat), axis=0)

        return ext_energies.squeeze(1), ext_energies_hat.squeeze(1)
                       
    def _weights(self, states, embeddings):
        
        # Compute external Epot and create a new eternal Epot detached 
        U_ext, U_ext_hat = self._extEpot(states, embeddings, mode="train")
        
        U_arg = -torch.divide(torch.subtract(U_ext, U_ext_hat), self.T*BOLTZMAN)

        # Avoid very large exponential arguments because they can produce infinities
        #if (U_arg.abs() > 80).any():
        #    U_arg = (U_arg - U_arg.min()) / (U_arg.max() - U_arg.min())

        exponentials = torch.exp(U_arg)
        weights = torch.divide(exponentials, exponentials.sum())
        
        return weights, U_ext_hat
    
    def _effectiven(self, weights):

        lnwi = torch.log(weights)
        neff = torch.exp(-torch.sum(torch.multiply(weights, lnwi), axis=0)).detach()

        return neff
    
    def compute_we(self, states, crystal, embeddings, neff_threshold=None):
        weights, U_ext_hat = self._weights(states, embeddings)
        n = len(weights)

        # Compute the weighted ensemble of the conformations
        states = states.to(self.device)
        crystal = crystal.to(self.precision)

        obs = torch.tensor([self.metric(state, crystal) for state in states], device = self.device, dtype = self.precision)

        obs = torch.where(obs > 10e6, torch.tensor(10e6, device = self.device, dtype = self.precision), obs)
        avg_metric = torch.mean(obs).detach().item()

        w_ensemble = torch.multiply(weights, obs).sum(0) 
                
        return w_ensemble, avg_metric
    

    def compute_loss(self, crystal, states, embeddings, val=False):
        w_e, avg_metric = self.compute_we(states, crystal, embeddings)

        values_dict = {}
        we_loss = self.loss_fn(w_e)

        if val == False:
            loss = we_loss
            values_dict['loss_2'] = None
        
            values_dict['loss_1'] = we_loss.item()
            
        else:
            loss = we_loss
            values_dict['val_loss_2'] = None
            values_dict['val_loss_1'] = we_loss.item()
                        
        values_dict['avg_metric'] = avg_metric

        return loss, values_dict

    def compute_energy_loss(self, x, y, embeddings, nnp_prime, N):

        # Send y to device
        y = y.to(self.device)

        # Compute the delta force and energy
        pos = x.to(self.device).type(torch.float32).reshape(-1, 3)
        embeddings = embeddings.repeat(x.shape[0] , 1)
        batch = torch.arange(embeddings.size(0), device=self.device).repeat_interleave(
            embeddings.size(1)
        )
        embeddings = embeddings.reshape(-1).to(self.device)

        if nnp_prime == None:
            energy, forces = self.nnp(embeddings, pos, batch)
        else:
            energy, forces = nnp_prime(embeddings, pos, batch)

        if y.shape[-1] == 1:
            return l1_loss(y, energy)
        elif y.shape[-1] == 3:
            l1_loss(y, forces)/(3*N)        
    
    def compute_gradients(self, crystal, native_ensemble, states, embeddings,  grads_to_cpu=True, val=False):

        if val == False:
            self.optimizer.zero_grad()
            loss, values_dict = self.compute_loss(crystal, states, embeddings)
            values_dict['train_avg_metric'] = values_dict['avg_metric']
            
            if loss != 0.0:
                loss.backward()
                
                grads = []
                for p in self.nnp.parameters():
                    if grads_to_cpu:
                        if p.grad is not None: grads.append(p.grad.data.cpu().numpy())
                        else: grads.append(None)
                    else:
                        if p.grad is not None:
                            grads.append(p.grad)
            else:
                grads = None
                
        elif val == True:
            grads = None
            loss, values_dict = self.compute_loss(crystal, states, embeddings)
            loss = loss.detach()
            values_dict['val_avg_metric'] = values_dict['avg_metric']
        
        else:
            print("Invalid value for 'val'")
            raise ValueError
        
        self.logger.debug(f'loss = {loss} {values_dict}')
        return grads, loss.item(), values_dict


    def get_loss(self):
        return self.loss.detach().item()

    def apply_gradients(self, gradients):
                
        if gradients:
            for g, p in zip(gradients, self.nnp.parameters()):
                if g is not None:
                    p.grad = torch.from_numpy(g).to(self.device)
            self.clip_gradients()

        self.optimizer.step()
    
    def clip_gradients(self):
        dynamic_max_grad_norm = 1.5 * self.gradnorm_queue.mean() + 2 * self.gradnorm_queue.std()

        # Get current grad_norm
        params = [p for p in self.optimizer.param_groups for p in p['params']]
        grad_norm = clip_grad.get_grad_norm(params)

        # Clip gradients based on dynamic_max_grad_norm
        for p in params:
            if p.grad is not None:
                torch.nn.utils.clip_grad_norm_(p, max_norm=dynamic_max_grad_norm)
        
        # Log and update the Queue
        if float(grad_norm) > dynamic_max_grad_norm:
            self.gradnorm_queue.add(float(dynamic_max_grad_norm))
            print(f'Clipped gradient with value {grad_norm:.1f} while allowed {dynamic_max_grad_norm:.1f}')
        else:
            self.gradnorm_queue.add(float(grad_norm))

    def set_lr(self, lr):
        for g in self.optimizer.param_groups:
            g['lr'] = lr

    def get_native_U(self, ground_truths, embeddings):
        ground_truths = ground_truths.unsqueeze(0)
        return self._extEpot(ground_truths, embeddings, mode='val')

    def get_init_state(self):
        return self.init_coords
