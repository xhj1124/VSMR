import torch
import torch.nn as nn
import torch.nn.functional as F

from pytorch_pretrained_bert.modeling import BertModel
from torch.nn.parameter import Parameter
from . import transformer
from .visual_model.detr import build_detr
from .language_model.bert import build_bert
from .vl_transformer import build_vl_transformer
from utils.box_utils import xywh2xyxy
from .transformer import TransformerDecoderLayer_self, TransformerDecoder, TransformerDecoderLayer_self_2, \
    TransformerEncoderLayer


def load_weights_mhead(model,load_path):
    dict_trained = torch.load(load_path)['model']
    dict_new = model.state_dict().copy()
    for key in dict_new.keys():

        if 'transformer.decoder.' + key in dict_trained.keys():
            dict_new[key] = dict_trained['transformer.decoder.' + key]
            print(key)
    model.load_state_dict(dict_new)
    del dict_new
    del dict_trained
    torch.cuda.empty_cache()
    return model
class TransVG(nn.Module):
    def __init__(self, args):
        super(TransVG, self).__init__()
        hidden_dim = args.vl_hidden_dim
        divisor = 16 if args.dilation else 32
        self.num_visu_token = int((args.imsize / divisor) ** 2)
        self.num_text_token = args.max_query_len

        self.visumodel = build_detr(args)
        # self.visumodel.backbone[0].body.layer1.add_module("spital1", self.spital1)
        # self.visumodel.backbone[0].body.layer2.add_module("spital2", self.spital2)
        # self.visumodel.backbone[0].body.layer3.add_module("spital3", self.spital3)
        # self.visumodel.backbone[0].body.layer4.add_module("spital4", self.spital4)
        self.textmodel = build_bert(args)
        self.mhead1 = TransformerDecoderLayer_self(256,8)
        self.mhead2 = TransformerDecoderLayer_self(256,8)
        self.mhead3 = TransformerDecoderLayer_self_2(256,8)
        # self.Tmhead = load_weights_mhead(self.Tmheads,'/home/xdu/xhj/detr-r50-e632da11.pth')
        # self.Vmhead = load_weights_mhead(self.Vmheads, '/home/xdu/xhj/detr-r50-e632da11.pth')
        num_total = self.num_visu_token + self.num_text_token + 1
        self.k_pos_embed = nn.Embedding(self.num_visu_token, hidden_dim)
        self.q_embed = nn.Embedding(self.num_visu_token+1,hidden_dim)
        self.k2_embed = nn.Embedding(self.num_text_token,hidden_dim)
        self.visual_pos = nn.Embedding(self.num_visu_token,hidden_dim)
        self.reg_token = nn.Embedding(1, hidden_dim)
        self.vl_pos_embed = nn.Embedding(num_total,hidden_dim)
        self.visu_proj = nn.Linear(self.visumodel.num_channels, hidden_dim)
        self.text_proj = nn.Linear(self.textmodel.num_channels, hidden_dim)

        self.vl_transformer = build_vl_transformer(args)
        #self.decoder_layer = transformer.TransformerDecoderLayer(256, 8)
        #self.decoder = transformer.TransformerDecoder(decoder_layer=self.decoder_layer, num_layers=6)
        self.bbox_embed = MLP(hidden_dim, hidden_dim, 4, 3)
        self.cls_embed = MLP(hidden_dim, hidden_dim, 20, 3)

        self.text_embd = nn.Linear(768,256)

        # DINO YANZHEGN
        self.cross_visual= transformer.TransformerDecoderLayer_self(256, 8)
        self.cross_text = transformer.TransformerDecoderLayer_self(256, 8)
        self.Linear1 = nn.Linear(256, 128)
        self.Linear2 = nn.Linear(128, 256)
        self.Linear3 = nn.Linear(128, 256)

        #
        self.relu = nn.ReLU()
        #DINO DECODER
        self.decoder_layer = transformer.TransformerDINODecoderLayer(256, 8)
        self.softmax=nn.Softmax(dim=0)
        self.decoder = transformer.TransformerDINODecoder(decoder_layer=self.decoder_layer, num_layers=6)
    def forward(self, img_data, text_data):
        bs = img_data.tensors.shape[0]



        # language bert
        text_fea = self.textmodel(text_data)
        text_src, text_mask = text_fea.decompose()
        texts = []
        #texts.append(text_src[3])
        #texts.append(text_src[7])
        #text_embed =self.text_embd(text_src[-1])
        #text_embed = F.softmax(text_embed,dim=1)
        #text_new = text_src[-1]*text_embed
        #text_new = torch.sum(text_new, dim=1)  #

        texts=text_src[:,0,:]
        texts = self.text_embd(texts)
        assert text_mask is not None
        # visual backbone
        #cls = text_src[:,0,:]
        visu_mask, visu_src = self.visumodel(img_data,texts) # (N*B)xC


        
        text_last =text_src
        text_last = self.text_proj(text_last)
        # permute BxLenxC to LenxBxC
        text_last = text_last.permute(1, 0, 2)

        text_mask = text_mask.flatten(1)


        fv1 = self.mhead1(visu_src,text_last)
        fc = self.mhead2(visu_src,text_last)
        fc = fc + visu_src
        fv2 = self.mhead3(fc,visu_src)
        visu_src = fv1+fv2

        # target regression token
        tgt_src = self.reg_token.weight.unsqueeze(1).repeat(1, bs, 1)
        tgt_mask = torch.zeros((bs, 1)).to(tgt_src.device).to(torch.bool)

        vl_src = torch.cat([tgt_src, text_last, visu_src], dim=0)
        vl_mask = torch.cat([tgt_mask, text_mask, visu_mask], dim=1)
        vl_pos = self.vl_pos_embed.weight.unsqueeze(1).repeat(1, bs, 1)

        vg_hs = self.vl_transformer(vl_src, vl_mask, vl_pos)  # (1+L+N)xBxC
        vg_hs = vg_hs[0]

        pred_box = self.bbox_embed(vg_hs).sigmoid()
        pred_cls = self.cls_embed(vg_hs).sigmoid()
        return pred_box


class MLP(nn.Module):
    """ Very simple multi-layer perceptron (also called FFN)"""

    def __init__(self, input_dim, hidden_dim, output_dim, num_layers):
        super().__init__()
        self.num_layers = num_layers
        h = [hidden_dim] * (num_layers - 1)
        self.layers = nn.ModuleList(nn.Linear(n, k) for n, k in zip([input_dim] + h, h + [output_dim]))

    def forward(self, x):
        for i, layer in enumerate(self.layers):
            x = F.relu(layer(x)) if i < self.num_layers - 1 else layer(x)
        return x
