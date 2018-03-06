# -*- coding:utf-8 -*-

import os
import random
import math

import argparse
import tqdm

import numpy as np
import struct

import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable

from generator import Generator
from discriminator import Discriminator
from rollout import Rollout
from data_iter import GenDataIter, DisDataIter
# ================== Parameter Definition =================

parser = argparse.ArgumentParser(description='Training Parameter')
parser.add_argument('--cuda', action='store', default=None, type=int)
opt = parser.parse_args()
print(opt)

# Basic Training Paramters
SEED = 88
BATCH_SIZE = 29
TOTAL_BATCH = 10
GENERATED_NUM = 80
NEGATIVE_FILE = 'training.data'
EVAL_FILE = 'evaluation.data'
VOCAB_SIZE = 22
PRE_EPOCH_NUM = 10

if opt.cuda is not None and opt.cuda >= 0:
    torch.cuda.set_device(opt.cuda)
    opt.cuda = True

# Genrator Parameters
g_state_dim = 22
g_hidden_dim = 44
g_action_dim = 22
g_sequence_len = 70

# Discriminator Parameters
d_num_class = 2
d_state_dim = 22
d_hidden_dim = 44




def generate_samples(model, batch_size, generated_num):
    samples = []
    for _ in range(int(generated_num / batch_size)):
        sample = model.sample(batch_size, g_sequence_len).cpu().data.numpy()
        samples.append(sample)
    return np.asarray(samples)

def train_epoch(model, data_iter, criterion, optimizer):
    total_loss = 0.
    total_words = 0.
    for (data, target) in data_iter:#tqdm(
        #data_iter, mininterval=2, desc=' - Training', leave=False):
        data = Variable(data)
        target = Variable(target)
        if opt.cuda:
            data, target = data.cuda(), target.cuda()
        target = target.float()
        pred = model.forward(data)
        loss = criterion(pred, target)
        total_loss += loss.data[0]
        total_words += data.size(0) * data.size(1)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    data_iter.reset()
    return math.exp(total_loss / total_words)

def eval_epoch(model, data_iter, criterion):
    total_loss = 0.
    total_words = 0.
    for (data, target) in data_iter:#tqdm(
        #data_iter, mininterval=2, desc=' - Training', leave=False):
        data = Variable(data, volatile=True)
        target = Variable(target, volatile=True)
        if opt.cuda:
            data, target = data.cuda(), target.cuda()
        target = target.contiguous().view(-1)
        pred = model.forward(data)
        loss = criterion(pred, target)
        total_loss += loss.data[0]
        total_words += data.size(0) * data.size(1)
    data_iter.reset()
    return math.exp(total_loss / total_words)

class GANLoss(nn.Module):
    """Reward-Refined NLLLoss Function for adversial training of Gnerator"""
    def __init__(self):
        super(GANLoss, self).__init__()

    def forward(self, prob, reward):
        """
        Args:
            prob: (N), torch Variable 
            reward : (N, ), torch Variable
        """
        loss = prob * reward
        loss =  -torch.sum(loss)
        return loss

# transfer a 1-d vector into a string
def to_string(x):
    ret = ""
    for i in range(x.shape[0]):
        ret += "{:.3f} ".format(x[i])
    return ret

def load_expert_data(num):
    train_addr = "anon/train/"
    addrs = os.listdir(train_addr)
    Data = []
    Actions = []
    for d in addrs:
        num -= 1
        if num < 0:
            break
        
        seq_len = int(d.split('-')[2][:2])
        if seq_len != 70:
            continue
        content = open(train_addr + d, 'rb').read()
        data = np.zeros((seq_len, 22), dtype=np.float)
        action = np.zeros((seq_len-1, 22), dtype=np.float)
        for i in range(seq_len):
            pre_data = np.asarray(struct.unpack('16i', content[64*i:64*i+64]), dtype=np.float)
            for j in range(11):
                data[i, 2*j] = np.clip(pre_data[j] / 360, 0, 399) / 400
                data[i, 2*j+1] = np.clip(pre_data[j] % 360, 0, 359) / 360
        
            if i > 0:
                action[i-1] = data[i] - data[i-1]
        
        data = data[:-1]
        Data.append(data)
        Actions.append(action)

    Data = np.stack(Data)
    Actions = np.stack(Actions)
    
    tot_data = Data.shape[0]
    #rand_ind = np.random.permutation(tot_data)
    #Data, Actions = Data[rand_ind], Actions[rand_ind]
    train_data, train_action = Data[:int(tot_data*0.8)], Actions[:int(tot_data*0.8)]
    val_data, val_action = Data[int(tot_data*0.8):], Actions[int(tot_data*0.8):]
    
    ave_stepsize = np.mean(np.abs(train_action), axis = (0, 1))
    std_stepsize = np.std(train_action, axis = (0, 1))
    ave_length = np.mean(np.sum(np.sqrt(np.square(train_action[:, :, ::2]) + np.square(train_action[:, :, 1::2])), axis = 1), axis = 0)
    ave_near_bound = np.mean((train_data < 1.0 / 100.0) + (train_data > 99.0 / 100.0), axis = (0, 1))
    print(ave_stepsize, std_stepsize, ave_length, ave_near_bound)
    with open("val_stats.txt", "a") as text_file:
        text_file.write('Expert:\n')
        text_file.write('ave_stepsize: ' + to_string(ave_stepsize) + '\n')
        text_file.write('std_stepsize: ' + to_string(std_stepsize) + '\n')
        text_file.write('ave_length: ' + to_string(ave_length) + '\n')
        text_file.write('ave_near_bound: ' + to_string(ave_near_bound) + '\n')
        text_file.write('\n')
    
    print("train_data.shape:", train_data.shape, "val_data.shape:", val_data.shape)
    return train_data, train_action, val_data, val_action, ave_stepsize, std_stepsize, ave_length, ave_near_bound

print "starting to load to data"
train_states, train_actions, val_states, val_actions, exp_ave_stepsize, exp_std_stepsize, exp_ave_length, exp_ave_near_bound \
    = load_expert_data(20000)
print "done loading data"
random.seed(SEED)
np.random.seed(SEED)

# Define Networks
generator = Generator(g_state_dim, g_hidden_dim, g_action_dim, opt.cuda, num_layers=1)
discriminator = Discriminator(d_num_class, d_state_dim, d_hidden_dim, num_layers=1)
if opt.cuda:
    generator = generator.cuda()
    discriminator = discriminator.cuda()

# Load data from file
gen_data_iter = GenDataIter(train_states, train_actions, BATCH_SIZE)

# Pretrain Generator using MLE
gen_criterion = nn.BCELoss(size_average=False)
gen_optimizer = optim.Adam(generator.parameters())
if opt.cuda:
    gen_criterion = gen_criterion.cuda()
print('Pretrain with BCE ...')
for epoch in range(PRE_EPOCH_NUM):
    loss = train_epoch(generator, gen_data_iter, gen_criterion, gen_optimizer)
    print('Epoch [%d] Model Loss: %f'% (epoch, loss))
    generate_samples(generator, BATCH_SIZE, GENERATED_NUM, EVAL_FILE)
    # eval_iter = GenDataIter(EVAL_FILE, BATCH_SIZE)
    # loss = eval_epoch(target_lstm, eval_iter, gen_criterion)
    # print('Epoch [%d] True Loss: %f' % (epoch, loss))

# Pretrain Discriminator
dis_criterion = nn.NLLLoss(size_average=False)
dis_optimizer = optim.Adam(discriminator.parameters())
if opt.cuda:
    dis_criterion = dis_criterion.cuda()
print('Pretrain Discriminator ...')
for epoch in range(5):
    generate_samples(generator, BATCH_SIZE, GENERATED_NUM, NEGATIVE_FILE)
    dis_data_iter = DisDataIter(data, NEGATIVE_FILE, BATCH_SIZE)
    for _ in range(3):
        loss = train_epoch(discriminator, dis_data_iter, dis_criterion, dis_optimizer)
        print('Epoch [%d], loss: %f' % (epoch, loss))
# Adversarial Training 
rollout = Rollout(generator, 0.8)
print('#####################################################')
print('Start Adversarial Training...\n')
gen_gan_loss = GANLoss()
gen_gan_optm = optim.Adam(generator.parameters())
if opt.cuda:
    gen_gan_loss = gen_gan_loss.cuda()
gen_criterion = nn.NLLLoss(size_average=False)
if opt.cuda:
    gen_criterion = gen_criterion.cuda()
dis_criterion = nn.NLLLoss(size_average=False)
dis_optimizer = optim.Adam(discriminator.parameters())
if opt.cuda:
    dis_criterion = dis_criterion.cuda()
for total_batch in range(TOTAL_BATCH):
    ## Train the generator for one step
    for it in range(1):
        samp_ind = np.random.choice(train_states.shape[0], BATCH_SIZE)
        mod_samples = torch.from_numpy(train_states[samp_ind].copy())
        starts = Variable(mod_samples[:, :1, :].clone())

        samples, targets = generator.sample(BATCH_SIZE, g_sequence_len, starts)
        # calculate the reward
        rewards = rollout.get_reward(samples, 16, discriminator)
        rewards = Variable(torch.Tensor(rewards))
        if opt.cuda:
            rewards = torch.exp(rewards.cuda()).contiguous().view((-1,))
        prob = generator.get_log_prob(samples, targets).contiguous().view((-1,))
        loss = gen_gan_loss(prob, rewards)
        gen_gan_optm.zero_grad()
        loss.backward()
        gen_gan_optm.step()

    '''
    if total_batch % 1 == 0 or total_batch == TOTAL_BATCH - 1:
        samples = generate_samples(generator, BATCH_SIZE, GENERATED_NUM, EVAL_FILE)
        eval_iter = GenDataIter(EVAL_FILE, BATCH_SIZE)
        loss = eval_epoch(target_lstm, eval_iter, gen_criterion)
        print('Batch [%d] True Loss: %f' % (total_batch, loss))
    '''
    rollout.update_params()
    
    for _ in range(4):
        samples = generate_samples(generator, BATCH_SIZE, GENERATED_NUM)
        dis_data_iter = DisDataIter(train_states, samples, BATCH_SIZE)
        for _ in range(2):
            loss = train_epoch(discriminator, dis_data_iter, dis_criterion, dis_optimizer)

