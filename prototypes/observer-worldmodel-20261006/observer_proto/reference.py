"""Executable NumPy math/semantics reference, using RANDOM tiny weights.

This is a contract smoke, not learned forecasting and not a Qwen benchmark.
"""
from dataclasses import dataclass
import numpy as np
from .contracts import HORIZON, SAMPLES, DT, build_history_graph


def softmax(x, axis=-1):
    shifted = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(shifted)
    return e/e.sum(axis=axis, keepdims=True)


def mix_probabilities(logits):
    if logits.ndim != 3 or logits.shape[-1] != HORIZON+1:
        raise ValueError("Expected [K,Q,11] logits (single scene reference)")
    if not np.isfinite(logits).all():
        raise ValueError("Nonfinite logits")
    per_path = softmax(logits)
    return per_path, per_path.mean(axis=0)


def known_cdf_targets(initial, initial_valid, future, future_valid):
    """Conservative discrete-grid identifiability, separate from frozen parity.

    Shapes [...], [...,10]. Known changed value certifies event has happened,
    even after an unknown gap; exact first-flip bin then remains unidentified.
    No observed change certifies survival only over a complete known prefix.
    Unknown initial truth invalidates the whole query. This routine's exact
    parity to the unavailable legacy mask function is NOT claimed.
    """
    initial = np.asarray(initial, dtype=bool)
    initial_valid = np.asarray(initial_valid, dtype=bool)
    future, future_valid = np.asarray(future, bool), np.asarray(future_valid, bool)
    if future.shape[-1] != HORIZON or future.shape != future_valid.shape or initial.shape != future.shape[:-1] or initial_valid.shape != initial.shape:
        raise ValueError("Wrong label truth/mask shape")
    changed = (future != initial[...,None]) & future_valid
    seen_change = np.maximum.accumulate(changed, axis=-1)
    complete_prefix = np.logical_and.accumulate(future_valid, axis=-1)
    known = initial_valid[...,None] & (seen_change | complete_prefix)
    target = seen_change.astype(float)
    exact = np.full(initial.shape, -1, dtype=np.int64)
    for idx in np.ndindex(initial.shape):
        if not initial_valid[idx]:
            continue
        flips = np.flatnonzero(changed[idx])
        if len(flips) and future_valid[idx][:flips[0]+1].all():
            exact[idx] = int(flips[0])
        elif not len(flips) and future_valid[idx].all():
            exact[idx] = HORIZON
    return target, known, exact


def hybrid_loss(probabilities, target, known, epsilon=1e-7):
    """Known CDF MSE j=1..9 + 0.5*known terminal BCE j=10.

    Each component has its own denominator. Empty component returns 0.
    epsilon is an explicit PROTOTYPE choice, not verified frozen clamp parity.
    """
    p, y, mask = np.asarray(probabilities), np.asarray(target), np.asarray(known, bool)
    if p.shape[:-1] != y.shape[:-1] or p.shape[-1] != 11 or y.shape[-1] != 10 or mask.shape != y.shape:
        raise ValueError("Probability/CDF shape mismatch")
    if not np.isfinite(p).all() or np.any(p < 0) or not np.allclose(p.sum(-1), 1):
        raise ValueError("Invalid categorical probabilities")
    if not np.isfinite(y[mask]).all() or np.any((y[mask] < 0) | (y[mask] > 1)):
        raise ValueError("Invalid known CDF labels")
    cdf = p[...,:10].cumsum(-1)
    def known_mean(x, m):
        return float(x[m].mean()) if m.any() else 0.0
    timing = known_mean((cdf[...,:9]-y[...,:9])**2, mask[...,:9])
    terminal_p = np.clip(cdf[...,9], epsilon, 1-epsilon)
    terminal = known_mean(-(y[...,9]*np.log(terminal_p)+(1-y[...,9])*np.log1p(-terminal_p)), mask[...,9])
    return {"loss": timing+0.5*terminal, "timing": timing, "terminal": terminal,
            "rps_report_j1_to_j10": known_mean((cdf-y)**2, mask),
            "timing_count": int(mask[...,:9].sum()), "terminal_count": int(mask[...,9].sum())}


def query_truth(history, queries, states, valid):
    """Input [...,N,D], output [...,Q], exact supported distance predicate."""
    if states.shape != valid.shape:
        raise ValueError("State/mask shapes differ")
    truth, masks = [], []
    for q in queries:
        a, b, fields = q.bind(history)
        va, vb = np.take(states[...,a,:], fields, -1), np.take(states[...,b,:], fields, -1)
        ma, mb = np.take(valid[...,a,:], fields, -1), np.take(valid[...,b,:], fields, -1)
        ok = ma.all(-1) & mb.all(-1) & np.isfinite(va).all(-1) & np.isfinite(vb).all(-1)
        dist = np.linalg.norm(np.where(ma & mb, va-vb, 0), axis=-1)
        truth.append(dist < q.threshold); masks.append(ok)
    return np.stack(truth, -1), np.stack(masks, -1)


def rule_mc(history, queries, rollout):
    initial, initial_mask = query_truth(history, queries, history.values[-1], history.valid[-1])
    future, future_mask = query_truth(history, queries, rollout.states, rollout.valid)
    k = rollout.states.shape[0]
    targets, known, bins = known_cdf_targets(np.broadcast_to(initial,(k,len(queries))),
        np.broadcast_to(initial_mask,(k,len(queries))), future.swapaxes(1,2), future_mask.swapaxes(1,2))
    per_path = np.full((k,len(queries),11), np.nan)
    ok = bins >= 0
    per_path[ok] = np.eye(11)[bins[ok]]
    # Never renormalize away unknown paths or mislabel unknown as no-flip.
    query_valid = ok.all(axis=0)
    probs = np.full((len(queries),11), np.nan)
    probs[query_valid] = per_path[:,query_valid].mean(0)
    return {"bins": bins, "per_path_probs": per_path, "probabilities": probs,
            "valid": query_valid, "cdf_target": targets, "cdf_known": known}


@dataclass
class JointLaw:
    mean: np.ndarray  # [K,N,D], next-state mean
    std: np.ndarray  # [K,N,D], diagonal std, not variance
    factor: np.ndarray  # [K,N,D,R], covariance diag(std^2)+FF^T
    def sample(self, rng):
        k, n, d = self.mean.shape
        shared = rng.normal(size=(k, self.factor.shape[-1]))
        residual = rng.normal(size=(k,n,d))
        return self.mean + np.einsum("kndr,kr->knd",self.factor,shared) + self.std*residual
    def covariance(self, k=0):
        f = self.factor[k].reshape(-1,self.factor.shape[-1])
        return np.diag(self.std[k].ravel()**2) + f@f.T
    def masked_nll(self, target, valid):
        terms=[]
        for k in range(len(self.mean)):
            m=valid[k].ravel()
            if not m.any():
                continue
            delta=(target[k]-self.mean[k]).ravel()[m]
            cov=self.covariance(k)[np.ix_(m,m)]
            sign,ld=np.linalg.slogdet(cov)
            if sign <= 0: raise ValueError("Nonpositive covariance")
            terms.append(0.5*(len(delta)*np.log(2*np.pi)+ld+delta@np.linalg.solve(cov,delta)))
        return float(np.mean(terms)) if terms else 0.0

@dataclass
class Rollout:
    states: np.ndarray  # [K,10,N,D]
    valid: np.ndarray
    edges: np.ndarray  # [K,10,N,N], sample-specific deterministic proximity
    times: np.ndarray  # [10], absolute seconds
    laws: list
    previous_states: np.ndarray  # diagnostic, proves branch self feedback


def geometry_edges(states, valid, radius=5.0):
    xy = states[...,:2]
    ok = valid[...,:2].all(-1)
    dist = np.linalg.norm(xy[..., :,None,:]-xy[...,None,:,:],axis=-1)
    return (dist < radius) & ok[..., :,None] & ok[...,None,:]


class TinyGraphEncoder:
    """One typed GraphTransformer layer, RANDOM parameters for CPU fixtures."""
    def __init__(self, input_width, width, rng):
        self.input = rng.normal(scale=.1,size=(input_width,width))
        self.node_type = rng.normal(scale=.1,size=(3,width))
        self.q=rng.normal(scale=.1,size=(width,width)); self.k=rng.normal(scale=.1,size=(width,width)); self.v=rng.normal(scale=.1,size=(width,width))
        self.edge_bias=rng.normal(scale=.1,size=(5,))
    def __call__(self, graph):
        z=np.tanh(graph.features@self.input+self.node_type[graph.node_types])
        score=(z@self.q)@(z@self.k).T/np.sqrt(z.shape[-1])
        usable=(graph.edge_types>=0) & graph.node_mask[None,:]
        # Absent rows retain a self slot numerically, then are zeroed out.
        usable |= np.diag(~usable.any(-1))
        score+=self.edge_bias[np.maximum(graph.edge_types,0)] + np.log(np.maximum(graph.edge_weights,1e-30))
        score=np.where(usable,score,-1e30)
        out=np.tanh(z+softmax(score)@(z@self.v))
        return out*graph.node_mask[:,None]


class NumpyPrototype:
    """Whole random-weight graph→tiny backbone→correlated rollout→risk head."""
    def __init__(self, history, graph_width=16, backbone_width=24, rank=2, seed=7):
        rng=np.random.default_rng(seed)
        graph=build_history_graph(history)
        d=len(history.fields)
        self.stage1_encoder=TinyGraphEncoder(graph.features.shape[-1],graph_width,rng)
        self.projector=rng.normal(scale=.1,size=(graph_width,backbone_width))
        self.tiny_backbone=rng.normal(scale=.1,size=(backbone_width,backbone_width))
        self.transition=rng.normal(scale=.07,size=(2*d+backbone_width, d))
        self.factor_weights=rng.normal(scale=.05,size=(backbone_width,d*rank))
        self.rank=rank
        # Intentionally independent graph parameters; no aliases.
        self.stage2_encoder=TinyGraphEncoder(2*d+2,graph_width,rng)
        self.query_weights=rng.normal(scale=.1,size=(graph_width,graph_width))
        # Pair coordinates + distance/margin/valid/time/initial truth, each tick.
        bypass_width=(HORIZON+1)*(2*len(history.fields)+5)
        self.risk_head=rng.normal(scale=.03,size=(graph_width+bypass_width+1,11))

    def encode(self, h):
        graph=build_history_graph(h)
        z=self.stage1_encoder(graph)@self.projector
        # Synthetic causal sequence backbone, NOT Qwen. External cross-attention
        # gives every final entity readout access to every historical hidden state.
        hidden=[]; state=np.zeros(z.shape[-1])
        for token in z:
            state=np.tanh(token+state@self.tiny_backbone); hidden.append(state)
        hidden=np.stack(hidden)
        q=z[graph.last_entity_nodes]
        context=softmax(q@hidden.T/np.sqrt(z.shape[-1]))@hidden
        return context

    def rollout(self,h,seed=13,k=SAMPLES):
        context=self.encode(h)
        rng=np.random.default_rng(seed)
        n,d=h.values.shape[1:]
        current=np.broadcast_to(np.where(h.valid[-1],h.values[-1],0),(k,n,d)).copy()
        valid=np.broadcast_to(h.valid[-1] & h.present[-1,:,None],current.shape).copy()
        c=np.broadcast_to(context,(k,*context.shape))
        factor=np.broadcast_to((context@self.factor_weights).reshape(n,d,self.rank),(k,n,d,self.rank)).copy()
        floors=np.array([f.min_std for f in h.fields])
        states=[]; graphs=[]; laws=[]; prev=[]
        for step in range(HORIZON):
            prev.append(current.copy())
            edges=geometry_edges(current,valid)
            nbr=np.einsum("kij,kjd->kid",edges,current)/np.maximum(edges.sum(-1,keepdims=True),1)
            drift=np.tanh(np.concatenate([current,nbr,c],-1)@self.transition)
            law=JointLaw(current+DT*drift,np.broadcast_to(floors+.03,current.shape),factor)
            current=law.sample(rng)
            current=np.where(valid,current,0)
            states.append(current.copy()); graphs.append(geometry_edges(current,valid)); laws.append(law)
        return Rollout(np.stack(states,1),np.broadcast_to(valid[:,None],(k,HORIZON,n,d)).copy(),
                       np.stack(graphs,1),h.cutoff+DT*np.arange(1,HORIZON+1),laws,np.stack(prev,1))

    def risk(self,h,queries,rollout):
        from .contracts import PackedGraph
        k,steps,n,d=rollout.states.shape
        all_logits=[]; valid_queries=[]
        g0=np.where(h.valid[-1],h.values[-1],0)
        for branch in range(k):
            states=np.concatenate([g0[None],rollout.states[branch]],0)
            masks=np.concatenate([h.valid[-1][None],rollout.valid[branch]],0)
            times=np.r_[0,rollout.times-h.cutoff]
            feat=np.concatenate([np.where(masks,states,0),masks.astype(float),np.broadcast_to(times[:,None,None],(11,n,1)),np.full((11,n,1),3.)],-1).reshape(11*n,-1)
            et=np.full((11*n,11*n),-1,dtype=int)
            for ti in range(11):
                local=geometry_edges(states[ti],masks[ti])
                for a,b in np.argwhere(local): et[ti*n+a,ti*n+b]=1
                if ti:
                    for a in range(n):
                        et[ti*n+a,(ti-1)*n+a]=2; et[(ti-1)*n+a,ti*n+a]=2
            np.fill_diagonal(et,0)
            graph=PackedGraph(feat,np.zeros(11*n,dtype=int),et,(et>=0).astype(float),np.arange(10*n,11*n),np.ones(11*n,dtype=bool))
            encoded=self.stage2_encoder(graph).reshape(11,n,-1)
            logits=[]; path_valid=[]
            for q in queries:
                a,b,fields=q.bind(h)
                qa=states[:,a][:,fields]; qb=states[:,b][:,fields]
                ok=masks[:,a][:,fields].all(-1)&masks[:,b][:,fields].all(-1)
                dist=np.linalg.norm(qa-qb,axis=-1)
                truth0=float(dist[0]<q.threshold)
                # Width supports exactly 2D distance fixture, reject other domains above.
                operands=np.concatenate([states[:,a],states[:,b],dist[:,None],(q.threshold-dist)[:,None],ok[:,None],times[:,None],np.full((11,1),truth0)],-1)
                pair=(encoded[:,a]+encoded[:,b])/2
                query=pair[0]@self.query_weights
                pooled=softmax(pair@query)@pair
                features=np.r_[pooled,operands.ravel(),q.threshold]
                logits.append(features@self.risk_head); path_valid.append(bool(ok.all()))
            all_logits.append(logits); valid_queries.append(path_valid)
        logits=np.asarray(all_logits)
        per_path,final=mix_probabilities(logits)
        return {"logits":logits,"per_path_probs":per_path,"probabilities":final,
                "valid":np.asarray(valid_queries).all(0)}
