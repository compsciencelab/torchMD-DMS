import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.nn.functional import mse_loss, l1_loss
from torchmdexp.nn.utils import rmsd

from pytorch_lightning import LightningModule
from torchmdnet.models.model import create_model, load_model



class LNNP(LightningModule):
    def __init__(self, hparams, prior_model=None, mean=None, std=None):
        super(LNNP, self).__init__()
        self.save_hyperparameters(hparams)
        
        
        if self.hparams.load_model:
            self.model = load_model(self.hparams.load_model, device=self.hparams.device, 
                                    derivative=self.hparams.derivative
                                   )
            ckpt = torch.load(self.hparams.load_model, map_location="cpu")
            self.save_hyperparameters(ckpt["hyper_parameters"])
            
        else:
            self.model = create_model(self.hparams, prior_model, mean, std)
            self.model.to(self.hparams.device)
    
    def configure_optimizers(self):
        optimizer = AdamW(
            self.model.parameters(),
            lr=self.hparams.lr,
        )
        scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer, step_size=self.hparams.step_size, gamma=0.8
        )
        return [optimizer], [scheduler]
    
    
    def forward(self, z, pos, batch=None):
        return self.model(z, pos, batch=batch)

    def training_step(self, z, pos, batch):
        return self.step(z, pos, batch, "train")
    
    def validation_step(self, z, pos, batch):
        Upot = self.step(z, pos, batch, "val")
        return Upot.detach()

    def step(self, z, pos, batch, stage):
        
        #pos = pos.to(self.device).type(torch.float32).reshape(-1, 3)
        #batch = torch.arange(z.size(0), device=device).repeat_interleave(
        #    z.size(1)
        #)
        #z = z.reshape(-1).to(device)
        
        with torch.set_grad_enabled(stage == "train" or self.hparams.derivative):
            # TODO: the model doesn't necessarily need to return a derivative once
            # Union typing works under TorchScript (https://github.com/pytorch/pytorch/pull/53180)
            Upot, force = self(z, pos, batch)
        
        return Upot


    
def loss_fn(currpos, native_coords):
    """
    Arguments: current system positions (shape = #replicas) , native coordinates
    Returns: loss sum over the replicas, mean rmsd over the replicas
    """
    loss = 0
    rmsds = []
    
    # Iterate through repetitions
    for idx, rep in enumerate(currpos):
        pos_rmsd, passed = rmsd(rep, native_coords[idx]) # Compute rmsd for one rep
        log_rmsd = torch.log(1.0 + pos_rmsd)             # Compute loss of one rep
        loss += log_rmsd                                 # Compute the sum of the repetition losses                           
        rmsds.append(pos_rmsd.item())                    # List of rmsds
        
    loss /= len(currpos) # Compute average loss
    
    return loss, mean(rmsds)