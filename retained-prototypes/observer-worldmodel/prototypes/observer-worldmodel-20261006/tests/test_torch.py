import importlib.util
import unittest
import numpy as np
from observer_proto.fixtures import tiny_history,tiny_queries
from observer_proto.reference import known_cdf_targets,hybrid_loss as numpy_loss,rule_mc
TORCH_AVAILABLE=importlib.util.find_spec('torch') is not None
if TORCH_AVAILABLE:
    import torch
    from observer_proto.torch_model import Stage1,Stage2,TinyCausalBackbone,LowRankLaw,assert_untied,freeze_stage1,hybrid_loss,load_local_qwen_backbone


@unittest.skipUnless(TORCH_AVAILABLE,'BLOCKED: PyTorch is not installed; no gradient tests were run')
class TorchTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1);torch.manual_seed(4)
        self.h=tiny_history();self.q=tiny_queries();self.s1=Stage1(self.h);self.s2=Stage2(self.h)
    def test_shape_probabilities_and_rule_reference(self):
        roll=self.s1(self.h);result=self.s2(self.h,self.q,roll)
        self.assertEqual(tuple(roll.states.shape),(8,10,3,2));self.assertEqual(tuple(result['logits'].shape),(8,2,11))
        torch.testing.assert_close(result['probabilities'].sum(-1),torch.ones(2))
        np.testing.assert_allclose(rule_mc(self.h,self.q,roll.reference())['probabilities'].sum(-1),1)
    def test_stage1_nll_gradient_through_frozen_backbone(self):
        for p in self.s1.backbone.parameters():p.requires_grad_(False)
        roll=self.s1(self.h);target=roll.states[:,0].detach()+.1
        loss=roll.laws[0].masked_nll(target,roll.valid[:,0]);loss.backward()
        for name,param in [('projector',self.s1.projector.weight),('history_encoder',self.s1.history_encoder.input.weight),('mean_head',self.s1.mean_head.weight),('factor_head',self.s1.factor_head.weight)]:
            self.assertIsNotNone(param.grad,name);self.assertTrue(torch.isfinite(param.grad).all(),name);self.assertGreater(param.grad.abs().sum().item(),0,name)
        self.assertTrue(all(p.grad is None for p in self.s1.backbone.parameters()))
    def test_stage2_frozen_stage1_and_parameter_separation(self):
        assert_untied(self.s1,self.s2);freeze_stage1(self.s1)
        with torch.no_grad():roll=self.s1(self.h)
        output=self.s2(self.h,self.q,roll)
        target=torch.zeros((2,10));known=torch.ones((2,10),dtype=torch.bool)&output['valid'][:,None]
        result=hybrid_loss(output['probabilities'],target,known);result['loss'].backward()
        self.assertTrue(all(p.grad is None for p in self.s1.parameters()))
        self.assertGreater(self.s2.encoder.input.weight.grad.abs().sum().item(),0)
        self.assertGreater(self.s2.head[0].weight.grad.abs().sum().item(),0)
    def test_copied_initialization_is_untied(self):
        copied=Stage2(self.h,copied_encoder=self.s1.transition_encoder);assert_untied(self.s1,copied)
        torch.testing.assert_close(copied.encoder.input.weight,self.s1.transition_encoder.input.weight)
        with torch.no_grad():copied.encoder.input.weight.add_(1)
        self.assertFalse(torch.equal(copied.encoder.input.weight,self.s1.transition_encoder.input.weight))
    def test_rollout_own_previous_state(self):
        roll=self.s1(self.h);torch.testing.assert_close(roll.previous_states[:,1:],roll.states[:,:-1])
        self.assertFalse(torch.allclose(roll.states[0],roll.states[1]))
    def test_hybrid_numpy_formula_parity_and_gradient(self):
        logits=torch.linspace(-2,2,8*2*11).reshape(8,2,11).requires_grad_()
        p=logits.softmax(-1).mean(0)
        future=np.zeros((2,10),bool);future[0,2:]=True;valid=np.ones_like(future);valid[1,5:]=False
        y,mask,_=known_cdf_targets(np.zeros(2,bool),np.ones(2,bool),future,valid)
        actual=hybrid_loss(p,torch.tensor(y),torch.tensor(mask));expected=numpy_loss(p.detach().numpy(),y,mask)
        self.assertAlmostEqual(actual['loss'].item(),expected['loss'],places=6)
        actual['loss'].backward();self.assertTrue(torch.isfinite(logits.grad).all());self.assertGreater(logits.grad.abs().sum().item(),0)
    def test_invalid_known_timing_label_rejected(self):
        p=torch.full((1,11),1/11);target=torch.zeros((1,10));target[0,0]=2
        mask=torch.zeros((1,10),dtype=torch.bool);mask[0,0]=True
        with self.assertRaises(ValueError):hybrid_loss(p,target,mask)
    def test_empty_mask_connected_zero_gradient(self):
        logits=torch.zeros((2,11),requires_grad=True);p=logits.softmax(-1)
        out=hybrid_loss(p,torch.full((2,10),float('nan')),torch.zeros((2,10),dtype=torch.bool))
        out['loss'].backward();self.assertEqual(out['loss'].item(),0);self.assertTrue(torch.equal(logits.grad,torch.zeros_like(logits)))
    def test_local_qwen_loader_rejects_remote_identifier(self):
        with self.assertRaises(FileNotFoundError):load_local_qwen_backbone('Qwen/Qwen3.5-2B-Base','caller-pinned-revision')
    def test_absolute_times_preserve_half_second_resolution(self):
        import copy
        h=copy.deepcopy(self.h);base=1_800_000_000.
        h.times+=base;h.cutoff+=base;h.available_time+=base;h.support_end+=base
        for o in h.observations:
            o.event_time+=base;o.available_time+=base;o.support_end+=base
        for a in h.associations:a.available_time+=base;a.support_end+=base
        h.validate();roll=self.s1(h)
        self.assertEqual(roll.times.dtype,torch.float64)
        torch.testing.assert_close(roll.times-h.cutoff,torch.arange(1,11,dtype=torch.float64)*.5)
        output=self.s2(h,self.q,roll)
        self.assertTrue(torch.isfinite(output['logits']).all())
    def test_lowrank_masked_marginal(self):
        mu=torch.zeros((1,2,2),requires_grad=True);std=torch.full_like(mu,.1);factor=torch.ones((1,2,2,1))*.5
        law=LowRankLaw(mu,std,factor);target=torch.tensor([[[0.,float('nan')],[float('nan'),float('nan')]]]);valid=torch.tensor([[[True,False],[False,False]]])
        loss=law.masked_nll(target,valid)
        self.assertAlmostEqual(loss.item(),.5*np.log(2*np.pi*.26),places=5);loss.backward();self.assertTrue(torch.isfinite(mu.grad).all())

if __name__=='__main__':unittest.main()
