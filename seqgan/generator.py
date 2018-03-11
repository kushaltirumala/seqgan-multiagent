# -*- coding: utf-8 -*-

import os
import math
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from utils.math_utils import *

class Generator(nn.Module):
    """Generator """
    def __init__(self, state_dim, hidden_dim, action_dim, use_cuda, log_std=0.0, num_layers=2):
        super(Generator, self).__init__()
        self.state_dim = state_dim
        self.hidden_dim = hidden_dim
        self.action_dim = action_dim
        self.use_cuda = use_cuda
        self.num_layers = num_layers

        self.gru = nn.GRU(state_dim, hidden_dim, num_layers, batch_first=True)
        self.lin = nn.Linear(hidden_dim, action_dim)
        self.init_params()
        self.constant = np.log(2*np.pi)

        self.action_log_std = nn.Parameter(torch.ones(1, action_dim) * log_std)

    def forward(self, x, h=None):
        """
        Args:
            x: (batch_size, seq_len, state_dim), sequence of tokens generated by generator
        """
        action_mean, hidden = self.gru(x, h)   ## action: batch * seq * state_dim
        action_mean = (F.sigmoid(self.lin(action_mean)) - 0.5) / 5.0  ## constrain stepsize
        action_log_std = self.action_log_std.expand_as(action_mean)
        action_std = torch.exp(action_log_std)
        return action_mean, action_log_std, action_std, hidden

    def step(self, x, h):
        """
        Args:
            x: (batch_size, 1, state_dim), sequence of tokens generated by generator
            h: (num_layers, batch_size, hidden_dim), gru hidden state
        """
        action_mean, hidden = self.gru(x, h)   ## action: batch * 1 * state_dim
        action_mean = (F.sigmoid(self.lin(action_mean)) - 0.5) / 5.0  ## constrain stepsize
        action_log_std = self.action_log_std.expand_as(action_mean)
        action_std = torch.exp(action_log_std)
        return action_mean, action_log_std, action_std, hidden

    def init_hidden(self, batch_size):
        h = Variable(torch.zeros((self.num_layers, batch_size, self.hidden_dim)).double())
        if self.use_cuda:
            h = h.cuda()
        return h
    
    def init_params(self):
        for param in self.parameters():
            param.data.uniform_(-0.05, 0.05)

    def get_log_prob(self, x, actions):
        """
        Args:
            x: (batch_size, seq_len, state_dim)
            actions: (batch_size, seq_len, action_dim)
        """
        action_mean, action_log_std, action_std, _ = self.forward(x)
        var = action_std.pow(2)
        log_density = -(actions - action_mean).pow(2) / (2 * var) - 0.5 * np.log(2 * math.pi) - action_log_std
        return log_density.sum(-1, keepdim=True)

    def select_action(self, x, h, test=False):
        action_mean, _, action_std, hidden = self.step(x, h)
        action = torch.normal(action_mean, action_std)
        if not test:
            return action, hidden
        else:
            return action, hidden, action_mean, action_std

    def env(self, x, a):
        """
        Args:
            x: (batch_size, 1, state_dim), Variable
            actions: (batch_size, 1, action_dim), Variable
        """
        x = x.data + a.data
        if self.use_cuda:
            x = x.cpu()
        x = x.numpy()   ## it seems we don't have clip in pytorch, so have to transfer it to numpy
        x = np.clip(x, 0, 1.0)
        x = Variable(torch.from_numpy(x))
        if self.use_cuda:
            x = x.cuda()

        return x  # Variable

    def sample(self, batch_size, seq_len, x): ## x is initial state or roll-out histories
        h = self.init_hidden(batch_size)
        samples = []
        actions = []
        given_len = x.size(1)
        lis = x.chunk(x.size(1), dim=1)
        for i in range(given_len):
            action, h = self.select_action(lis[i], h)
            samples.append(lis[i])
            actions.append(action)

        x = self.env(x, action)
        for i in range(given_len, seq_len):
            samples.append(x)
            action, h = self.select_action(x, h)
            actions.append(action)
            x = self.env(x, action)

        output = torch.cat(samples, dim=1)
        actions = torch.cat(actions, dim=1)
        return output, actions