import copy
import unittest
import numpy as np
from observer_proto.contracts import *
from observer_proto.fixtures import tiny_history,tiny_queries
from observer_proto.reference import *


class ContractTests(unittest.TestCase):
    def setUp(self):self.h=tiny_history();self.q=tiny_queries()
    def test_complete_numpy_chain(self):
        model=NumpyPrototype(self.h);roll=model.rollout(self.h);risk=model.risk(self.h,self.q,roll)
        self.assertEqual(roll.states.shape,(8,10,3,2));self.assertEqual(risk['logits'].shape,(8,2,11))
        np.testing.assert_allclose(risk['probabilities'].sum(-1),1)
        self.assertTrue(risk['valid'].all());self.assertTrue(np.isfinite(roll.states).all())
    def test_history_twenty_one_inclusive_samples(self):
        self.assertEqual(len(self.h.times),21);self.assertEqual(np.ptp(self.h.times),10)
        self.h.times=self.h.times[:-1]
        with self.assertRaises(ValueError):self.h.validate()
    def test_large_absolute_timestamp_cutoff(self):
        h=copy.deepcopy(self.h);base=1_800_000_000.
        h.times+=base;h.cutoff+=base;h.available_time+=base;h.support_end+=base
        for o in h.observations:
            o.event_time+=base;o.available_time+=base;o.support_end+=base
        for a in h.associations:a.available_time+=base;a.support_end+=base
        h.validate();h.times+=2
        with self.assertRaises(ValueError):h.validate()
    def test_field_order_and_domains_fail_closed(self):
        self.h.fields=(FieldSpec('y'),FieldSpec('x'))
        with self.assertRaises(NotImplementedError):self.h.validate()
        for kind in ('categorical','boolean','angle','positive','bounded','count','unit_vector'):
            with self.assertRaises(NotImplementedError):FieldSpec('x',kind).validate()
    def test_units_frames_must_match(self):
        self.h.fields=(FieldSpec('x',unit='m'),FieldSpec('y',unit='feet'))
        with self.assertRaises(ValueError):self.h.validate()
    def test_future_numeric_and_oracle_source_rejected(self):
        for name in ('available_time','support_end'):
            h=copy.deepcopy(self.h);getattr(h,name)[0,0,0]=1
            with self.assertRaises(ValueError):h.validate()
        self.h.source[0,0,0]=99
        with self.assertRaises(ValueError):self.h.validate()
    def test_future_encoder_and_association_support_rejected(self):
        h=copy.deepcopy(self.h);h.observations[0].support_end=1
        with self.assertRaises(ValueError):h.validate()
        h=copy.deepcopy(self.h);h.associations[0].available_time=1
        with self.assertRaises(ValueError):h.validate()
    def test_local_association_not_scene_clone(self):
        graph=build_history_graph(self.h)
        self.assertEqual(sum(graph.node_types==NODE_OBSERVATION),6)
        self.assertEqual((graph.edge_types==EDGE_ASSOCIATION).sum(),3)
        self.assertEqual((graph.edge_types==EDGE_OWNS).sum(),6)
        o=self.h.observations[0];a=self.h.associations[0]
        dest=20*3+self.h.entity_ids.index(a.entity_id)
        linked=np.flatnonzero(graph.edge_types[dest]==EDGE_ASSOCIATION)
        self.assertEqual(len(linked),1)
        self.assertEqual(graph.metadata['nodes'][linked[0]][1],o.observation_id)
    def test_duplicate_local_evidence_rejected_order_independently(self):
        extra=Association('obs_0','entity_a',(0,),'fixture:overlap',score=.2)
        self.h.associations=(*self.h.associations,extra)
        for associations in (self.h.associations,tuple(reversed(self.h.associations))):
            self.h.associations=associations
            with self.assertRaises(ValueError):self.h.validate()
    def test_stateful_rule_without_initial_memory_fails_closed(self):
        for op in ('hold_for','sliding_window','hysteresis'):
            q=Query('entity_a','entity_b',2.,predicate=op)
            with self.assertRaisesRegex(NotImplementedError,'initial rule memory'):q.bind(self.h)
    def test_bad_association_identity_and_coverage_rejected(self):
        self.h.associations[0].entity_id='not_an_entity'
        with self.assertRaises(ValueError):self.h.validate()
        self.h=tiny_history();self.h.associations[0].kind='potential_coverage'
        with self.assertRaises(NotImplementedError):self.h.validate()
    def test_missing_values_not_zero_evidence_and_source_retained(self):
        graph=build_history_graph(self.h);node=5*3+2
        self.assertTrue(np.isfinite(graph.features).all());np.testing.assert_array_equal(graph.features[node,:4],0)
        h=copy.deepcopy(self.h);h.source[0,0,0]=2
        self.assertNotEqual(build_history_graph(h).features[0,4],graph.features[0,4])
    def test_graph_identity_and_query_reindexing(self):
        model=NumpyPrototype(self.h);roll=model.rollout(self.h);baseline=rule_mc(self.h,self.q,roll)
        order=[2,0,1];h2=self.h.permute(order)
        new=copy.deepcopy(roll);new.states=roll.states[:,:,order];new.valid=roll.valid[:,:,order]
        result=rule_mc(h2,self.q,new)
        np.testing.assert_array_equal(result['bins'],baseline['bins'])
        graph=build_history_graph(h2)
        target=20*3+h2.entity_ids.index('entity_a')
        self.assertEqual((graph.edge_types[target]==EDGE_ASSOCIATION).sum(),1)
    def test_branch_specific_feedback_and_graph_rebuild(self):
        roll=NumpyPrototype(self.h).rollout(self.h)
        np.testing.assert_allclose(roll.previous_states[:,1:],roll.states[:,:-1])
        self.assertGreater(np.std(roll.states[:,0]),0)
        np.testing.assert_array_equal(roll.edges,geometry_edges(roll.states,roll.valid))
        self.assertFalse(np.allclose(roll.states[0],roll.states[1]))
    def test_joint_lowrank_correlation_and_masked_marginal(self):
        law=JointLaw(np.zeros((1,2,2)),np.full((1,2,2),.1),np.ones((1,2,2,1))*.5)
        cov=law.covariance();self.assertGreater(cov[0,2],0)
        draws=JointLaw(np.repeat(law.mean,5000,0),np.repeat(law.std,5000,0),np.repeat(law.factor,5000,0)).sample(np.random.default_rng(2))
        self.assertGreater(np.corrcoef(draws[:,0,0],draws[:,1,0])[0,1],.9)
        valid=np.array([[[True,False],[False,False]]]);target=np.array([[[0,np.nan],[np.nan,np.nan]]])
        expected=.5*np.log(2*np.pi*(.1**2+.5**2))
        self.assertAlmostEqual(law.masked_nll(target,valid),expected)
    def test_probability_mixture_not_logit_average(self):
        logits=np.zeros((8,1,11));logits[0,0,0]=20
        per,p=mix_probabilities(logits)
        np.testing.assert_allclose(p,per.mean(0))
        self.assertFalse(np.allclose(p,softmax(logits.mean(0))))
    def test_stage2_parameter_separation_numpy(self):
        model=NumpyPrototype(self.h)
        self.assertIsNot(model.stage1_encoder,model.stage2_encoder)
        self.assertFalse(np.shares_memory(model.stage1_encoder.q,model.stage2_encoder.q))
    def test_known_cdf_and_terminal_censoring(self):
        f=np.zeros((3,10),bool);m=np.ones_like(f);f[0,2:]=True;m[1,6:]=False;m[2,1:3]=False;f[2,3:]=True
        target,known,bins=known_cdf_targets(np.zeros(3,bool),np.ones(3,bool),f,m)
        np.testing.assert_array_equal(bins,[2,-1,-1]);self.assertFalse(known[1,-1]);self.assertTrue(known[2,3:].all())
        self.assertFalse(known[2,1:3].any());np.testing.assert_array_equal(target[2,3:],1)
    def test_initial_true_and_exact_five_seconds(self):
        future=np.ones((2,10),bool);future[0,0:]=False;future[1,-1]=False
        _,_,bins=known_cdf_targets(np.ones(2,bool),np.ones(2,bool),future,np.ones_like(future))
        np.testing.assert_array_equal(bins,[0,9])
    def test_unknown_initial_and_no_flip_are_different(self):
        _,known,bins=known_cdf_targets(np.zeros(2,bool),np.array([False,True]),np.zeros((2,10),bool),np.ones((2,10),bool))
        np.testing.assert_array_equal(bins,[-1,10]);self.assertFalse(known[0].any());self.assertTrue(known[1].all())
    def test_rule_unknown_is_not_no_flip(self):
        roll=NumpyPrototype(self.h).rollout(self.h);roll.valid[0,0,0]=False
        result=rule_mc(self.h,self.q,roll)
        self.assertEqual(result['bins'][0,0],-1);self.assertFalse(result['valid'][0]);self.assertTrue(np.isnan(result['probabilities'][0]).all())
    def test_between_tick_flip_not_claimed_visible(self):
        # A flip and recovery wholly between ticks has identical sampled truths.
        _,_,bins=known_cdf_targets(np.array([False]),np.array([True]),np.zeros((1,10),bool),np.ones((1,10),bool))
        self.assertEqual(bins[0],10)
    def test_hybrid_separate_reductions_and_terminal_cdf(self):
        p=np.full((2,11),1/11);target=np.zeros((2,10));mask=np.zeros_like(target,dtype=bool)
        mask[0,:2]=True;mask[1,9]=True
        loss=hybrid_loss(p,target,mask)
        expected=((1/11)**2+(2/11)**2)/2+.5*(-np.log(1/11))
        self.assertAlmostEqual(loss['loss'],expected);self.assertEqual(loss['timing_count'],2);self.assertEqual(loss['terminal_count'],1)
    def test_loss_empty_masks_and_unknown_nan(self):
        p=np.full((1,11),1/11);y=np.full((1,10),np.nan);mask=np.zeros((1,10),bool)
        self.assertEqual(hybrid_loss(p,y,mask)['loss'],0)
    def test_exact_rule_equality_threshold(self):
        # distance exactly 2, rule is < not <=. Initial G0 is false.
        truth,valid=query_truth(self.h,[self.q[0]],self.h.values[-1],self.h.valid[-1])
        self.assertFalse(truth[0]);self.assertTrue(valid[0])
    def test_query_invalid_type_rejected(self):
        q=Query('entity_a','entity_b',2.,predicate='any_of_91')
        with self.assertRaises(NotImplementedError):q.bind(self.h)

if __name__=='__main__':unittest.main()
