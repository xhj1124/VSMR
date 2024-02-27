# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved
"""
Train and eval functions used in main.py
"""
import math
import os
import sys
import torch
import torch.distributed as dist

from tqdm import tqdm
from typing import Iterable

import utils.misc as utils
import utils.loss_utils as loss_utils
import utils.eval_utils as eval_utils


def train_one_epoch(args, model: torch.nn.Module, data_loader: Iterable, 
                    optimizer: torch.optim.Optimizer, device: torch.device, 
                    epoch: int, max_norm: float = 0):
    model.train()
    metric_logger = utils.MetricLogger(delimiter="  ")
    metric_logger.add_meter('lr', utils.SmoothedValue(window_size=1, fmt='{value:.6f}'))
    header = 'Epoch: [{}]'.format(epoch)
    print_freq = 50
    for batch in metric_logger.log_every(data_loader, print_freq, header):
        img_data, text_data, target,label = batch

        # copy to GPU
        img_data = img_data.to(device)
        text_data = text_data.to(device)
        target= target.to(device)

        # from thop import profile
        # flops, params = profile(model, inputs=(img_data, text_data))
        # print('FLOPs = ' + str(flops / 1000 ** 3) + 'G')
        # print('Params = ' + str(params / 1000 ** 2) + 'M')
        # model forward
        pred_box= model(img_data, text_data)

        loss_dict = loss_utils.trans_vg_loss(pred_box,target)
        losses = sum(loss_dict[k] for k in loss_dict.keys())

        # reduce losses over all GPUs for logging purposes
        loss_dict_reduced = utils.reduce_dict(loss_dict)
        loss_dict_reduced_unscaled = {k: v
                                      for k, v in loss_dict_reduced.items()}
        losses_reduced_unscaled = sum(loss_dict_reduced_unscaled.values())
        loss_value = losses_reduced_unscaled.item()

        #检查损失有效
        if not math.isfinite(loss_value):
            print("Loss is {}, stopping training".format(loss_value))
            print(loss_dict_reduced)
            sys.exit(1)
        
        optimizer.zero_grad()
        losses.backward()
        if max_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm)
        optimizer.step()
        
        metric_logger.update(loss=loss_value, **loss_dict_reduced_unscaled)
        metric_logger.update(lr=optimizer.param_groups[0]["lr"])
    # gather the stats from all processes
    metric_logger.synchronize_between_processes()
    print("Averaged stats:", metric_logger)
    return {k: meter.global_avg for k, meter in metric_logger.meters.items()}


@torch.no_grad()
def validate(args, model: torch.nn.Module, data_loader: Iterable, device: torch.device):
    model.eval()

    metric_logger = utils.MetricLogger(delimiter="  ")
    header = 'Eval:'

    for batch in metric_logger.log_every(data_loader, 10, header):
        img_data, text_data, target,label = batch
        batch_size = img_data.tensors.size(0)
        # copy to GPU
        img_data = img_data.to(device)
        text_data = text_data.to(device)
        target = target.to(device)

        pred_boxes= model(img_data, text_data)
        miou, accu = eval_utils.trans_vg_eval_val(pred_boxes,target)
        metric_logger.update_v2('miou', torch.mean(miou), batch_size)
        metric_logger.update_v2('accu', accu, batch_size)

    # gather the stats from all processes
    metric_logger.synchronize_between_processes()
    stats = {k: meter.global_avg for k, meter in metric_logger.meters.items()}
    return stats


@torch.no_grad()
def evaluate(args, model: torch.nn.Module, data_loader: Iterable, device: torch.device):
    model.eval()

    pred_box_list = []
    gt_box_list = []
    for _,batch in enumerate(tqdm(data_loader)):
        img_data, text_data, target,label = batch
        batch_size = img_data.tensors.size(0)
        # copy to GPU
        img_data = img_data.to(device)
        text_data = text_data.to(device)
        target = target.to(device)

        output = model(img_data, text_data)
        #yuce = torch.mul(output,640)
        #zhenshi = torch.mul(target,640)
        #print("yuce",yuce)
        #print("zhenshi",zhenshi)
        pred_box_list.append(output.cpu())
        gt_box_list.append(target.cpu())

    pred_boxes = torch.cat(pred_box_list, dim=0)
    gt_boxes = torch.cat(gt_box_list, dim=0)
    total_num = gt_boxes.shape[0]
    accu_num,accu_num_6,accu_num_7,accu_num_8,accu_num_9,meaniou,cumuIou = eval_utils.trans_vg_eval_test(pred_boxes, gt_boxes)

    result_tensor = torch.tensor([accu_num, total_num]).to(device)
    torch.cuda.synchronize()
    dist.all_reduce(result_tensor)
    accuracy = float(result_tensor[0]) / float(result_tensor[1])

    result_tensor_6 = torch.tensor([accu_num_6, total_num]).to(device)
    torch.cuda.synchronize()
    dist.all_reduce(result_tensor_6)
    accuracy_6 = float(result_tensor_6[0]) / float(result_tensor_6[1])
    result_tensor_7 = torch.tensor([accu_num_7, total_num]).to(device)
    torch.cuda.synchronize()
    dist.all_reduce(result_tensor_7)
    accuracy_7 = float(result_tensor_7[0]) / float(result_tensor_7[1])
    result_tensor_8 = torch.tensor([accu_num_8, total_num]).to(device)
    torch.cuda.synchronize()
    dist.all_reduce(result_tensor_8)
    accuracy_8 = float(result_tensor_8[0]) / float(result_tensor_8[1])
    result_tensor_9 = torch.tensor([accu_num_9, total_num]).to(device)
    torch.cuda.synchronize()
    dist.all_reduce(result_tensor_9)
    accuracy_9 = float(result_tensor_9[0]) / float(result_tensor_9[1])

    
    return accuracy,accuracy_6,accuracy_7,accuracy_8,accuracy_9,meaniou,cumuIou
        