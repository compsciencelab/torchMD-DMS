from torchmdexp.utils.logger import LogWriter
import os
import csv
from statistics import mean

class Learner:
    """
    Task learner class.
    
    Class to manage training process.
    
    Parameters
    -----------
    scheme: Scheme
        Training scheme class, which handles coordination of workers
    log_dir: str
        Directory for model checkpoints and the monitor.csv
    """
    
    def __init__(self, scheme, steps, output_period, timestep, scheduler=None, train_names = [] , log_dir=None, keys=('epoch', 'train_loss', 'val_loss'), 
                 load_model=None):
        self.log_dir = log_dir
        self.update_worker = scheme.update_worker()
        self.keys = keys
        
        # Counters and metrics
        self.steps = steps
        self.output_period = output_period
        self.log_dir = log_dir
        self.train_names = train_names
        
        # Train losses of each batch
        self.train_losses = []
        self.train_avg_metrics = []
        self.val_losses = []
        self.val_avg_metrics = []
        
        # Losses of the epoch
        self.train_loss = None
        self.val_loss = None
        self.train_avg_metric = None
        self.val_avg_metric = None
        
        # Level, epoch and LR
        self.level = 0
        if load_model is not None:
            with open(os.path.join(self.log_dir, 'monitor.csv'), 'r') as file:
                reader = csv.reader(file)
                last_row = None
                for row in reader:
                    last_row = row
            try:
                self.epoch = int(last_row[0])
            except:
                self.epoch = 0
        else:
            self.epoch = 0

        self.update_step = 0
        self.lr = None
        self.scheduler = scheduler
        self.timestep = timestep
        
        # Prepare results dict
        self.results_dict = {key: 0 for key in keys}
        
        keys = tuple([key for key in self.results_dict.keys()])
        self.logger = LogWriter(self.log_dir,keys=keys, load_model=load_model)
        self.step_logger = LogWriter(self.log_dir, keys=keys, monitor='step_monitor.csv', load_model=load_model)
        
    def step(self, val=False, mode='val'):
        """ Takes an optimization update step """
        
        # Update step
        info = self.update_worker.step(self.steps, self.output_period, val)
        
        if val == True:
            self.val_losses.append(info['val_loss'])
            self.val_avg_metrics.append(info['val_avg_metric'])
        else:
            self.train_losses.append(info['train_loss'])
            self.train_avg_metrics.append(info['train_avg_metric'])
            lr = self.update_worker.updater.local_we_worker.weighted_ensemble.optimizer.param_groups[0]['lr']            

        info['lr'] = self.update_worker.updater.local_we_worker.weighted_ensemble.optimizer.param_groups[0]['lr']
        info['timestep'] = self.timestep
        info['steps'] = self.steps
        self.step_logger.write_row(info)

    def level_up(self):
        """ Increases level of difficulty """
        
        #self.update_worker.set_init_state(next_level)
        self.level += 1
    
    def set_init_state(self, init_state):
        """ Change init state """
        self.update_worker.set_init_state(init_state)
    
    def get_init_state(self):
        return self.update_worker.get_init_state()
    
    def set_batch(self, batch, sample='native_ensemble'):
        """ Change batch data """
        self.update_worker.set_batch(batch, sample)
    
    def set_steps(self, steps):
        """ Change number of simulation steps """
        self.steps = steps
    
    def set_output_period(self, output_period):
        """ Change output_period """
        self.output_period = output_period
    
    def set_timestep(self, timestep):
        self.timestep = timestep
        return self.update_worker.set_timestep(timestep)
    
    def save_model(self):
        
        if self.val_loss is not None:
            path = f'{self.log_dir}/epoch={self.epoch}-train_loss={self.train_loss:.4f}-val_loss={self.val_loss:.4f}.ckpt'
        else: 
            path = f'{self.log_dir}/epoch={self.epoch}-train_loss={self.train_loss:.4f}.ckpt'
            
        self.update_worker.save_model(path)
    
    def compute_epoch_stats(self):
        """ Compute epoch val loss and train loss averages and update epoch number"""
        
        # Compute train loss
        self.train_loss = mean(self.train_losses)
        self.train_avg_metric = mean(self.train_avg_metrics)
        self.results_dict['train_loss'] = self.train_loss
        self.results_dict['train_avg_metric'] = self.train_avg_metric
        
        self.results_dict['lr'] = self.update_worker.updater.local_we_worker.weighted_ensemble.optimizer.param_groups[0]['lr']
        self.results_dict['timestep'] = self.timestep
        
        # Update epoch
        self.epoch += 1
        self.results_dict['epoch'] = self.epoch

        if 'level' in self.keys:
            self.results_dict['level'] = self.level
        if 'steps' in self.keys:
            self.results_dict['steps'] = self.steps
        
        # Compute val loss
        if 'val_loss' in self.keys:
            if len(self.val_losses) > 0:
                self.val_loss = mean(self.val_losses)
                self.val_avg_metric = mean(self.val_avg_metrics)
                self.results_dict['val_loss'] = self.val_loss
                self.results_dict['val_avg_metric'] = self.val_avg_metric
            else:
                self.results_dict['val_loss'] = None
                
        # Reset everything
        self.train_losses = []
        self.train_avg_metrics = []
        self.val_losses = []
        self.val_avg_metrics = []
        
    def write_row(self):
        if self.logger:
            self.logger.write_row(self.results_dict)

    def get_val_loss(self):
        return self.val_loss
    
    def get_batch_avg_metric(self, val=False):
        if val == False:
            return self.train_avg_metrics[-1]
        else:
            return self.val_avg_metrics[-1]
    
    def get_avg_metric(self, val=False):
        if val == False:
            return self.train_avg_metric
        else:
            return self.val_avg_metric
    
    def get_train_loss(self):
        return self.train_loss
    
    def set_lr(self, lr):
        self.update_worker.set_lr(lr)

    def get_buffers(self):
        return self.update_worker.get_buffers()