"""Optional trainable implementation. Requires pre-existing PyTorch only.

No import-time model downloads, device allocation, training, filesystem writes,
or PyG dependency. HF integration is lazy and local-files-only.
"""
from dataclasses import dataclass
from pathlib import Path
import copy
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from .contracts import HORIZON, SAMPLES, DT, build_history_graph


class TypedGraphTransformer(nn.Module):
    """Dense single-head typed attention for bounded fixtures, not large-graph scaling."""
    def __init__(self, input_width, width):
        super().__init__()
        self.input = nn.Linear(input_width,width)
        self.node_types = nn.Embedding(3,width)
        self.qkv = nn.Linear(width,3*width)
        self.edge_bias = nn.Embedding(5,1)
        self.norm = nn.LayerNorm(width)
        self.ff = nn.Sequential(nn.Linear(width,2*width),nn.GELU(),nn.Linear(2*width,width))
        self.width=width
    def forward(self,x,node_types,edge_types,edge_weights,node_valid):
        z=self.input(x)+self.node_types(node_types)
        q,k,v=self.qkv(z).chunk(3,-1)
        scores=q@k.transpose(-1,-2)/(self.width**.5)
        allowed=(edge_types>=0)&node_valid.unsqueeze(-2)
        no_keys=~allowed.any(-1)
        eye=torch.eye(x.shape[-2],dtype=torch.bool,device=x.device)
        allowed=allowed|(no_keys.unsqueeze(-1)&eye)
        scores=scores+self.edge_bias(edge_types.clamp_min(0)).squeeze(-1)+edge_weights.clamp_min(1e-30).log()
        scores=scores.masked_fill(~allowed,torch.finfo(scores.dtype).min)
        attended=torch.softmax(scores.float(),-1).to(v.dtype)@v
        z=self.norm(z+attended)
        z=self.norm(z+self.ff(z))
        return z*node_valid.unsqueeze(-1)


class TinyCausalBackbone(nn.Module):
    """Synthetic, randomly initialized test backbone. Explicitly NOT Qwen."""
    def __init__(self,width=24):
        super().__init__();self.hidden_size=width
        layer=nn.TransformerEncoderLayer(width,4,2*width,dropout=0.,batch_first=True)
        self.net=nn.TransformerEncoder(layer,1,enable_nested_tensor=False)
    def forward(self,inputs_embeds,attention_mask):
        length=inputs_embeds.shape[1]
        causal=torch.ones(length,length,dtype=torch.bool,device=inputs_embeds.device).triu(1)
        return self.net(inputs_embeds,mask=causal,src_key_padding_mask=~attention_mask.bool())


class HFTextBackbone(nn.Module):
    def __init__(self,text_model,revision):
        super().__init__();self.text_model=text_model
        self.hidden_size=int(text_model.config.hidden_size)
        self.revision=revision
    def forward(self,inputs_embeds,attention_mask):
        # Do NOT wrap in no_grad: frozen backbone still propagates to projector.
        result=self.text_model(inputs_embeds=inputs_embeds,attention_mask=attention_mask,
                               use_cache=False,return_dict=True)
        return result.last_hidden_state


def load_local_qwen_backbone(local_model_path,revision,*,dtype=None,freeze=True):
    """Real HF Qwen3.5 loader, only explicitly supplied local directory.

    Requires a compatible installed transformers release and complete existing
    Qwen3.5 model snapshot. Never downloads, trusts remote code, loads LoRA,
    launches a server, or edits model files. The revision is recorded provenance;
    a local directory must itself be the desired pinned snapshot. This adapter
    initially loads the native wrapper (including vision weights); releases its
    vision module after extracting language_model. Budget that transient memory.
    Caller must explicitly invoke this function; smoke never invokes it.
    """
    path=Path(local_model_path).expanduser().resolve()
    if not path.is_dir() or not (path/'config.json').is_file():
        raise FileNotFoundError("Provide an existing local HF snapshot directory with config.json")
    if not revision or not isinstance(revision,str):
        raise ValueError("Caller-supplied pinned revision/provenance required")
    try:
        from transformers import Qwen3_5Model
    except ImportError as e:
        raise ImportError("Installed transformers must provide Qwen3_5Model; no auto-install/download") from e
    kwargs=dict(local_files_only=True,trust_remote_code=False,revision=revision)
    if dtype is not None: kwargs['dtype']=dtype
    wrapper,loading_info=Qwen3_5Model.from_pretrained(str(path),output_loading_info=True,**kwargs)
    missing_text=[key for key in loading_info.get('missing_keys',[]) if 'language_model.' in key]
    if missing_text or loading_info.get('error_msgs'):
        raise RuntimeError(f'Qwen text weights did not load completely: {missing_text}; {loading_info.get("error_msgs",[])}')
    if not hasattr(wrapper,'language_model'):
        raise RuntimeError("Installed Qwen wrapper contract differs: language_model missing")
    text_model=wrapper.language_model
    adapter=HFTextBackbone(text_model,revision)
    del wrapper
    if freeze:
        for p in adapter.parameters():p.requires_grad_(False)
    return adapter


def geometry_edges(states,valid,radius=5.):
    xy=states[...,:2];ok=valid[...,:2].all(-1)
    dist=torch.linalg.vector_norm(xy.unsqueeze(-2)-xy.unsqueeze(-3),dim=-1)
    return (dist<radius)&ok.unsqueeze(-1)&ok.unsqueeze(-2)


def local_graph(states,valid,time,source_code=3.):
    """One or batched current graph, sharing schema with future Stage2 graphs."""
    shape=states.shape[:-1]+(1,)
    time=torch.as_tensor(time,device=states.device,dtype=states.dtype).expand(shape)
    feat=torch.cat([torch.where(valid,states,0),valid.to(states.dtype),time,
                    torch.full(shape,source_code,device=states.device,dtype=states.dtype)],-1)
    edges=geometry_edges(states,valid)
    et=torch.where(edges,1,-1)
    eye=torch.eye(states.shape[-2],dtype=torch.bool,device=states.device)
    et=torch.where(eye,0,et)
    node_valid=valid.any(-1)
    return feat,torch.zeros(states.shape[:-1],dtype=torch.long,device=states.device),et,(et>=0).to(states.dtype),node_valid


@dataclass
class LowRankLaw:
    mean: torch.Tensor  # [K,N,D], or [N,D]
    std: torch.Tensor
    factor: torch.Tensor  # [...,N,D,R]
    def rsample(self,generator=None):
        scene_shape=self.mean.shape[:-2]+(self.factor.shape[-1],)
        z=torch.randn(scene_shape,device=self.mean.device,dtype=self.mean.dtype,generator=generator)
        eps=torch.randn(self.mean.shape,device=self.mean.device,dtype=self.mean.dtype,generator=generator)
        return self.mean+torch.einsum('...ndr,...r->...nd',self.factor,z)+self.std*eps
    def masked_nll(self,target,valid):
        """Exact observed subvector marginal, not zero-filling missing targets."""
        means=self.mean.reshape(-1,self.mean.shape[-2]*self.mean.shape[-1])
        stds=self.std.reshape_as(means)
        factors=self.factor.reshape(len(means),means.shape[-1],self.factor.shape[-1])
        targets=target.reshape_as(means);masks=valid.reshape_as(means)
        losses=[]
        for mu,std,fac,y,m in zip(means,stds,factors,targets,masks):
            if m.any():
                distribution=torch.distributions.LowRankMultivariateNormal(mu[m],fac[m],std[m].square())
                losses.append(-distribution.log_prob(y[m]))
        return torch.stack(losses).mean() if losses else self.mean.sum()*0


@dataclass
class TorchRollout:
    states: torch.Tensor
    valid: torch.Tensor
    edges: torch.Tensor
    times: torch.Tensor
    laws: list
    previous_states: torch.Tensor
    def reference(self):
        from .reference import Rollout
        return Rollout(self.states.detach().cpu().numpy(),self.valid.cpu().numpy(),
            self.edges.cpu().numpy(),self.times.cpu().numpy(),[],self.previous_states.detach().cpu().numpy())


class Stage1(nn.Module):
    """History Qwen encoding ONCE, then shared graph transition decoder 10 times.

    Single scene per call; K branches are vectorized. This is not per-step Qwen
    re-encoding. Discrete domains, birth/death, controls and observation decoders
    deliberately remain unsupported in this bounded skeleton.
    """
    def __init__(self,history,backbone=None,width=16,rank=2):
        super().__init__();history.validate()
        graph=build_history_graph(history);self.d=len(history.fields);self.rank=rank
        self.backbone=backbone if backbone is not None else TinyCausalBackbone()
        self.history_encoder=TypedGraphTransformer(graph.features.shape[-1],width)
        self.projector=nn.Linear(width,self.backbone.hidden_size)
        self.readout=nn.MultiheadAttention(self.backbone.hidden_size,4,batch_first=True)
        self.transition_encoder=TypedGraphTransformer(2*self.d+2,width)
        self.transition=nn.Sequential(nn.Linear(width+self.backbone.hidden_size,2*width),nn.GELU())
        self.mean_head=nn.Linear(2*width,self.d)
        self.scale_head=nn.Linear(2*width,self.d)
        self.factor_head=nn.Linear(2*width,self.d*rank)
        self.register_buffer('min_std',torch.tensor([f.min_std for f in history.fields],dtype=torch.float32))
        self.register_buffer('domain_scale',torch.tensor([f.scale for f in history.fields],dtype=torch.float32))
        self.schema=tuple(history.fields)
    @property
    def device(self):return self.projector.weight.device
    @property
    def dtype(self):return self.projector.weight.dtype
    def encode(self,history):
        if tuple(history.fields)!=self.schema:raise ValueError('Schema mismatch')
        graph=build_history_graph(history)
        device,dtype=self.device,self.dtype
        tensor=lambda a,dt:torch.as_tensor(a,device=device,dtype=dt)
        x=self.history_encoder(tensor(graph.features,dtype),tensor(graph.node_types,torch.long),
            tensor(graph.edge_types,torch.long),tensor(graph.edge_weights,dtype),tensor(graph.node_mask,torch.bool))
        projected=self.projector(x).unsqueeze(0)
        mask=tensor(graph.node_mask,torch.bool).unsqueeze(0)
        hidden=self.backbone(inputs_embeds=projected,attention_mask=mask)
        # Every entity may attend to all historical hidden states, irrespective
        # of its causal token order. This does not guarantee Qwen permutation invariance.
        query=projected[:,graph.last_entity_nodes]
        return self.readout(query,hidden,hidden,key_padding_mask=~mask,need_weights=False)[0].squeeze(0)
    def transition_law(self,current,valid,context,time):
        encoded=self.transition_encoder(*local_graph(current,valid,time))
        hidden=self.transition(torch.cat([encoded,context],-1))
        mean=current+DT*self.mean_head(hidden)*self.domain_scale
        std=F.softplus(self.scale_head(hidden))*self.domain_scale+self.min_std
        factor=self.factor_head(hidden).reshape(*current.shape,self.rank)*self.domain_scale[:,None]
        if not all(torch.isfinite(t).all() for t in (mean,std,factor)):
            raise FloatingPointError('Nonfinite joint law')
        return LowRankLaw(mean,std,factor)
    def forward(self,history,k=SAMPLES,seed=13):
        context=self.encode(history)
        current=torch.as_tensor(np.where(history.valid[-1],history.values[-1],0),device=self.device,dtype=self.dtype).unsqueeze(0).expand(k,-1,-1)
        valid=torch.as_tensor(history.valid[-1]&history.present[-1,:,None],device=self.device).unsqueeze(0).expand_as(current)
        context=context.unsqueeze(0).expand(k,-1,-1)
        generator=torch.Generator(device=self.device);generator.manual_seed(seed)
        states=[];edges=[];laws=[];prev=[]
        for step in range(HORIZON):
            prev.append(current)
            law=self.transition_law(current,valid,context,step*DT)
            current=torch.where(valid,law.rsample(generator),0)
            states.append(current);edges.append(geometry_edges(current,valid));laws.append(law)
        return TorchRollout(torch.stack(states,1),valid[:,None].expand(k,HORIZON,*valid.shape[1:]),
            torch.stack(edges,1),history.cutoff+DT*torch.arange(1,HORIZON+1,device=self.device,dtype=torch.float64),laws,torch.stack(prev,1))


class Stage2(nn.Module):
    """Scratch shared GraphTransformer; independent from all Stage1 parameters."""
    def __init__(self,history,width=16,copied_encoder=None):
        super().__init__();self.d=len(history.fields)
        self.encoder=TypedGraphTransformer(2*self.d+2,width) if copied_encoder is None else copy.deepcopy(copied_encoder)
        if self.encoder.width!=width or self.encoder.input.in_features!=2*self.d+2:
            raise ValueError('Copied encoder shape mismatch')
        self.query=nn.Linear(width+2,width)
        self.head=nn.Sequential(nn.Linear(width+11*(2*self.d+5)+1,2*width),nn.GELU(),nn.Linear(2*width,11))
    def forward(self,history,queries,rollout):
        states=rollout.states;k,h,n,d=states.shape;device,dtype=states.device,states.dtype
        g0=torch.as_tensor(np.where(history.valid[-1],history.values[-1],0),device=device,dtype=dtype)
        m0=torch.as_tensor(history.valid[-1],device=device)
        x=torch.cat([g0[None,None].expand(k,1,n,d),states],1)
        masks=torch.cat([m0[None,None].expand(k,1,n,d),rollout.valid],1)
        times=torch.cat([torch.zeros(1,device=device,dtype=dtype),(rollout.times.to(torch.float64)-history.cutoff).to(dtype)])
        features,types,_,_,nodes=local_graph(x,masks,times[None,:,None,None])
        total=11*n
        et=torch.full((k,total,total),-1,device=device,dtype=torch.long)
        for ti in range(11):
            spatial=geometry_edges(x[:,ti],masks[:,ti])
            et[:,ti*n:(ti+1)*n,ti*n:(ti+1)*n]=torch.where(spatial,1,-1)
            if ti:
                idx=torch.arange(n,device=device)
                et[:,ti*n+idx,(ti-1)*n+idx]=2;et[:,(ti-1)*n+idx,ti*n+idx]=2
        eye=torch.eye(total,dtype=torch.bool,device=device)
        et=torch.where(eye,0,et)
        encoded=self.encoder(features.reshape(k,total,-1),types.reshape(k,total),et,(et>=0).to(dtype),nodes.reshape(k,total)).reshape(k,11,n,-1)
        logits=[];all_valid=[]
        for q in queries:
            a,b,fields=q.bind(history)
            pa=x[:,:,a][:,:,fields];pb=x[:,:,b][:,:,fields]
            ok=masks[:,:,a][:,:,fields].all(-1)&masks[:,:,b][:,:,fields].all(-1)
            distance=torch.linalg.vector_norm(pa-pb,dim=-1)
            truth0=(distance[:,0]<q.threshold).to(dtype)
            raw=torch.cat([x[:,:,a],x[:,:,b],distance[...,None],(q.threshold-distance)[...,None],ok[...,None].to(dtype),times[None,:,None].expand(k,-1,-1),truth0[:,None,None].expand(k,11,1)],-1)
            pair=(encoded[:,:,a]+encoded[:,:,b])/2
            query=self.query(torch.cat([pair[:,0],torch.full((k,1),q.threshold,device=device,dtype=dtype),truth0[:,None]],-1))
            attention=torch.softmax((pair*query[:,None]).sum(-1),-1)
            pooled=(pair*attention[...,None]).sum(1)
            logits.append(self.head(torch.cat([pooled,raw.reshape(k,-1),torch.full((k,1),q.threshold,device=device,dtype=dtype)],-1)))
            all_valid.append(ok.all(-1))
        logits=torch.stack(logits,1)
        per_path=torch.softmax(logits.float(),-1)
        return {'logits':logits,'per_path_probs':per_path,'probabilities':per_path.mean(0),
                'valid':torch.stack(all_valid,1).all(0)}


def assert_untied(stage1,stage2):
    overlap={id(p) for p in stage1.parameters()}&{id(p) for p in stage2.parameters()}
    if overlap:raise AssertionError('Stage1/Stage2 parameter alias detected')


def freeze_stage1(stage1):
    stage1.eval()
    for p in stage1.parameters():p.requires_grad_(False)


def hybrid_loss(probabilities,target,known,epsilon=1e-7):
    """Prototype clamp; old function parity explicitly unverified."""
    p=probabilities.float();y=target.to(p.dtype);known=known.bool()
    if p.shape[-1]!=11 or y.shape!=known.shape or y.shape!=p.shape[:-1]+(10,):
        raise ValueError('Hybrid probability/label shape mismatch')
    if not torch.isfinite(p).all() or (p<0).any() or not torch.allclose(p.sum(-1),torch.ones_like(p[...,0]),atol=1e-5):
        raise ValueError('Invalid mixture probabilities')
    if not torch.isfinite(y[known]).all() or ((y[known]<0)|(y[known]>1)).any():raise ValueError('Known targets must be finite and in [0,1]')
    y=torch.where(known,y,0)
    cdf=p[...,:10].cumsum(-1)
    def mean_known(values,mask):
        return values[mask].mean() if mask.any() else p.sum()*0
    timing=mean_known((cdf[...,:9]-y[...,:9]).square(),known[...,:9])
    terminal=mean_known(F.binary_cross_entropy(cdf[...,9].clamp(epsilon,1-epsilon),y[...,9],reduction='none'),known[...,9])
    return {'loss':timing+.5*terminal,'timing':timing,'terminal':terminal,
            'rps_report_j1_to_j10':mean_known((cdf-y).square(),known)}
