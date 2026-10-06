"""Bounded CPU/synthetic forward+backward only, no optimizer, Qwen, GPU or download."""
import importlib.util
import json
from pathlib import Path
import sys
import time
if importlib.util.find_spec('torch') is None:
    print('BLOCKED: PyTorch is not installed. Nothing installed/downloaded; no gradient result claimed.',file=sys.stderr)
    sys.exit(2)
import torch
import numpy as np
from observer_proto.fixtures import tiny_history,tiny_queries
from observer_proto.torch_model import Stage1,Stage2,assert_untied,freeze_stage1,hybrid_loss
from observer_proto.reference import rule_mc

started=time.perf_counter();torch.set_num_threads(1);torch.manual_seed(4)
h=tiny_history();queries=tiny_queries();stage1=Stage1(h);stage2=Stage2(h);assert_untied(stage1,stage2)
# Frozen synthetic backbone still permits gradients into graph projector.
for parameter in stage1.backbone.parameters():parameter.requires_grad_(False)
rollout=stage1(h)
state_nll=rollout.laws[0].masked_nll(rollout.states[:,0].detach()+.1,rollout.valid[:,0]);state_nll.backward()
state_gradients={name:float(p.grad.norm()) if p.grad is not None else None for name,p in stage1.named_parameters() if name in ['projector.weight','history_encoder.input.weight','factor_head.weight','mean_head.weight']}
if not all(x is not None and np.isfinite(x) and x>0 for x in state_gradients.values()):
    raise AssertionError('Stage1 gradient chain failed')
stage1.zero_grad(set_to_none=True);freeze_stage1(stage1)
with torch.no_grad():rollout=stage1(h)
output=stage2(h,queries,rollout)
loss=hybrid_loss(output['probabilities'],torch.zeros((2,10)),torch.ones((2,10),dtype=torch.bool)&output['valid'][:,None])
loss['loss'].backward()
if any(p.grad is not None for p in stage1.parameters()):raise AssertionError('Frozen Stage1 received Stage2 gradients')
if stage2.encoder.input.weight.grad is None or not torch.isfinite(stage2.encoder.input.weight.grad).all():raise AssertionError('Stage2 gradient failed')
rule=rule_mc(h,queries,rollout.reference())
result={'mode':'CPU synthetic tiny backbone; no Qwen/model downloads/optimizer/GPU',
        'torch_version':torch.__version__,'rollout_shape':list(rollout.states.shape),'logit_shape':list(output['logits'].shape),
        'state_nll':float(state_nll.detach()),'state_gradients':state_gradients,
        'stage2_encoder_gradient_norm':float(stage2.encoder.input.weight.grad.norm()),
        'stage2_loss':float(loss['loss'].detach()),'stage1_frozen_during_stage2':True,
        'parameter_aliases':False,'probability_normalized':bool(torch.allclose(output['probabilities'].sum(-1),torch.ones(2))),
        'rule_mc_bins_zero_based':rule['bins'].tolist(),'elapsed_seconds':time.perf_counter()-started}
(Path(__file__).parent/'reports'/'torch-smoke.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
