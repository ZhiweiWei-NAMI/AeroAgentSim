"""No downloads, no optimizer/training, CPU/NumPy only."""
import importlib.util
import json
from pathlib import Path
import time
import numpy as np
from observer_proto.fixtures import tiny_history,tiny_queries
from observer_proto.contracts import build_history_graph
from observer_proto.reference import NumpyPrototype,rule_mc,known_cdf_targets,hybrid_loss


def main():
    started=time.perf_counter()
    h=tiny_history(); queries=tiny_queries(); model=NumpyPrototype(h)
    roll=model.rollout(h); risk=model.risk(h,queries,roll); rule=rule_mc(h,queries,roll)
    # Synthetic labels: query 0 flips at tick3, query1 has no observed flip but
    # recording stops after tick6. They never enter Stage1/Stage2 model input.
    truth=np.zeros((2,10),bool); truth[0,2:]=True
    known=np.ones_like(truth); known[1,6:]=False
    target,mask,bins=known_cdf_targets(np.zeros(2,bool),np.ones(2,bool),truth,known)
    metrics=hybrid_loss(risk['probabilities'],target,mask & risk['valid'][:,None])
    graph=build_history_graph(h)
    result={'mode':'random-weight NumPy reference; NOT actual Qwen; NOT trained; synthetic modality tokens',
       'history_samples':21,'history_seconds':10,'rollout_shape':list(roll.states.shape),
       'logit_shape':list(risk['logits'].shape),'probability_shape':list(risk['probabilities'].shape),
       'graph_nodes':len(graph.features),'graph_edges':int((graph.edge_types>=0).sum()),
       'observer_owned_observations':graph.metadata['observations'],'explicit_local_associations':graph.metadata['associations'],
       'probability_normalized':bool(np.allclose(risk['probabilities'].sum(-1),1)),
       'sample_self_feedback':bool(np.allclose(roll.previous_states[:,1:],roll.states[:,:-1])),
       'joint_covariance_offdiagonal_max':float(np.max(np.abs(roll.laws[0].covariance()-np.diag(np.diag(roll.laws[0].covariance()))))),
       'rule_mc_bins_zero_based':rule['bins'].tolist(),'rule_mc_probabilities':rule['probabilities'].tolist(),
       'synthetic_label_bins_zero_based':bins.tolist(),'hybrid':metrics,
       'torch_available':importlib.util.find_spec('torch') is not None,
       'torch_gradient_tests':'BLOCKED: torch not installed in this executor',
       'frozen_clamp_mask_parity':'UNVERIFIED: original function unavailable; formula/semantic conformance only',
       'elapsed_seconds':time.perf_counter()-started}
    out=Path(__file__).parent/'reports'/'numpy-smoke.json';out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
